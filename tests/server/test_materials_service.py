"""新 materials authority 的聚合与协议测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from unilabos.server.database.repositories.materials import MaterialsRepository
from unilabos.protocol.common import AggregatePrecondition, InventoryMutation
from unilabos.protocol.materials import (
    MaterialDataWrite,
    MaterialDelete,
    MaterialIdentityWrite,
    MaterialMove,
    MaterialNodeCreate,
    MaterialPosition,
    MaterialSnapshot,
    MaterialSubstance,
    MaterialTreeCreate,
    ResourceTemplateWrite,
)
from unilabos.server.services.materials import (
    MaterialConflictError,
    MaterialsService,
)


def _mutation(operation: str, *, command_uuid: str | None = None, **values):
    return InventoryMutation(
        command_uuid=command_uuid or str(uuid4()),
        effect_key=operation,
        operation=operation,
        **values,
    )


def _template(
    service: MaterialsService,
    template_uuid: str,
    name: str,
    *,
    with_site: bool = False,
):
    return service.put_template(
        _mutation("put_template"),
        ResourceTemplateWrite(
            template_uuid=template_uuid,
            name=name,
            display_name=name.title(),
            resource_type="container",
            class_name="Container",
            available_sites=(
                [{"index": 0, "label": "A1", "content_type": ["container"]}]
                if with_site
                else []
            ),
        ),
    )


def _node(
    ref: str,
    template_name: str,
    *,
    parent: str | None = None,
):
    return MaterialNodeCreate(
        client_ref=ref,
        parent_client_ref=parent,
        identity=MaterialIdentityWrite(
            resource_id=f"resource-{ref}",
            name=ref,
            resource_type="container",
            class_name="Container",
            template_name=template_name,
        ),
        data=MaterialDataWrite(
            substances=[
                MaterialSubstance(
                    name="NaCl",
                    quantity=2,
                    quantity_unit="ug",
                    physical_state="solid",
                )
            ]
        ),
    )


def test_template_and_material_tree_roundtrip_is_authoritative(tmp_path) -> None:
    service = MaterialsService(MaterialsRepository(tmp_path / "materials.db"))
    try:
        _template(service, "deck-template", "deck", with_site=True)
        _template(service, "tube-template", "tube")
        request = MaterialTreeCreate(
            nodes=[
                _node("random-root", "deck"),
                _node("random-child", "tube", parent="random-root"),
            ],
        )
        command_uuid = str(uuid4())
        mutation = _mutation("create_material_tree", command_uuid=command_uuid)
        result = service.create_tree(mutation, request)

        assert result.replayed is False
        assert result.data.client_ref_map.keys() == {
            "random-root",
            "random-child",
        }
        assert result.data.root_material_uuid != "random-root"
        assert len(result.data.nodes) == 2
        assert result.data.nodes[1].material.parent_material_uuid == (
            result.data.root_material_uuid
        )
        assert result.data.nodes[1].data.substances[0].physical_state == "solid"
        assert result.data.nodes[0].sites[0].label == "A1"

        replay = service.create_tree(mutation, request)
        assert replay.replayed is True
        assert replay.data == result.data
    finally:
        service.repository.close()


def test_material_display_name_defaults_and_patch(tmp_path) -> None:
    """display_name 根字段：缺省回退 name（与 device 的 id/display_name 约定一致），
    显式值保留，且可经 patch 单独修改。"""
    from unilabos.protocol.materials import MaterialPatch

    service = MaterialsService(MaterialsRepository(tmp_path / "materials.db"))
    try:
        _template(service, "deck-template", "deck", with_site=True)
        _template(service, "tube-template", "tube")
        named = _node("child", "tube", parent="root")
        named.identity.display_name = "试管 A"
        request = MaterialTreeCreate(nodes=[_node("root", "deck"), named])
        result = service.create_tree(_mutation("create_material_tree"), request)

        root_node, child_node = result.data.nodes
        assert root_node.material.display_name == root_node.material.name == "root"
        assert child_node.material.display_name == "试管 A"

        patched = service.patch_material(
            _mutation("patch_material"),
            root_node.material.material_uuid,
            MaterialPatch(display_name="主甲板"),
        )
        assert patched.data.material.display_name == "主甲板"
        assert patched.data.material.name == "root"
    finally:
        service.repository.close()


def test_create_tree_adopts_explicit_material_uuid(tmp_path) -> None:
    """显式 material_uuid 是「带条件的创建」：以调用方 uuid 落库，uuid 已占用即冲突。

    这是开机图物料对齐（materials.ensure）与出库扣减产物落库的服务端契约。
    """
    service = MaterialsService(MaterialsRepository(tmp_path / "materials.db"))
    try:
        _template(service, "deck-template", "deck", with_site=True)
        _template(service, "tube-template", "tube")
        root_uuid, child_uuid = str(uuid4()), str(uuid4())
        request = MaterialTreeCreate(
            nodes=[
                _node("root", "deck").model_copy(
                    update={"material_uuid": root_uuid}
                ),
                _node("child", "tube", parent="root").model_copy(
                    update={"material_uuid": child_uuid}
                ),
            ],
        )
        result = service.create_tree(_mutation("create_material_tree"), request)

        assert result.data.root_material_uuid == root_uuid
        assert result.data.client_ref_map == {
            "root": root_uuid,
            "child": child_uuid,
        }

        # 同 uuid 再次创建（新 mutation，非幂等重放）→ 冲突
        conflict = MaterialTreeCreate(
            nodes=[
                _node("root-again", "deck").model_copy(
                    update={"material_uuid": root_uuid}
                )
            ],
        )
        with pytest.raises(MaterialConflictError, match="already exists"):
            service.create_tree(_mutation("create_material_tree"), conflict)
    finally:
        service.repository.close()


def test_create_tree_rejects_duplicate_explicit_uuid_in_one_tree() -> None:
    """同一棵创建树中显式 material_uuid 重复在协议层即被拒绝。"""
    duplicated = str(uuid4())
    with pytest.raises(ValueError, match="material_uuid"):
        MaterialTreeCreate(
            nodes=[
                _node("root", "deck").model_copy(
                    update={"material_uuid": duplicated}
                ),
                _node("child", "tube", parent="root").model_copy(
                    update={"material_uuid": duplicated}
                ),
            ],
        )


def test_position_update_checks_material_version(tmp_path) -> None:
    service = MaterialsService(MaterialsRepository(tmp_path / "materials.db"))
    try:
        _template(service, "tube-template", "tube")
        created = service.create_tree(
            _mutation("create_material_tree"),
            MaterialTreeCreate(
                nodes=[_node("tube", "tube")]
            ),
        )
        material = created.data.nodes[0]
        updated = service.put_position(
            _mutation(
                "put_position",
                preconditions=[
                    AggregatePrecondition(
                        aggregate_type="material",
                        aggregate_uuid=material.material.material_uuid,
                        expected_version=1,
                    )
                ],
            ),
            material.material.material_uuid,
            MaterialPosition(position_x=1, position_y=2, position_z=3),
        )
        assert updated.data.material.version == 2
        assert updated.data.position.position_x == 1

        with pytest.raises(MaterialConflictError, match="expected 1"):
            service.put_position(
                _mutation(
                    "put_position",
                    preconditions=[
                        AggregatePrecondition(
                            aggregate_type="material",
                            aggregate_uuid=material.material.material_uuid,
                            expected_version=1,
                        )
                    ],
                ),
                material.material.material_uuid,
                MaterialPosition(position_x=4, position_y=5, position_z=6),
            )
    finally:
        service.repository.close()


def test_move_clears_source_and_sets_destination_atomically(tmp_path) -> None:
    service = MaterialsService(MaterialsRepository(tmp_path / "materials.db"))
    try:
        _template(service, "deck-template", "deck", with_site=True)
        _template(service, "tube-template", "tube")
        first = service.create_tree(
            _mutation("create_material_tree"),
            MaterialTreeCreate(
                nodes=[
                    _node("deck-1", "deck"),
                    _node("tube", "tube", parent="deck-1"),
                ]
            ),
        )
        second = service.create_tree(
            _mutation("create_material_tree"),
            MaterialTreeCreate(
                nodes=[_node("deck-2", "deck")]
            ),
        )
        child_uuid = first.data.client_ref_map["tube"]
        source_site_uuid = first.data.nodes[0].sites[0].site_uuid
        destination_site_uuid = second.data.nodes[0].sites[0].site_uuid

        # 初次放入 source。
        service.move_material(
            _mutation("move_material"),
            MaterialMove(
                material_uuid=child_uuid,
                destination_site_uuid=source_site_uuid,
            ),
        )
        moved = service.move_material(
            _mutation("move_material"),
            MaterialMove(
                material_uuid=child_uuid,
                destination_site_uuid=destination_site_uuid,
            ),
        )

        assert moved.data.material.parent_material_uuid == (
            second.data.root_material_uuid
        )
        assert service.repository.get_site(source_site_uuid).occupied_material_uuid is None
        assert (
            service.repository.get_site(destination_site_uuid).occupied_material_uuid
            == child_uuid
        )
    finally:
        service.repository.close()


def test_snapshot_diff_and_apply_increment_material_once(tmp_path) -> None:
    service = MaterialsService(MaterialsRepository(tmp_path / "materials.db"))
    try:
        _template(service, "tube-template", "tube")
        created = service.create_tree(
            _mutation("create_material_tree"),
            MaterialTreeCreate(
                nodes=[_node("tube", "tube")]
            ),
        )
        node = created.data.nodes[0]
        changed_data = node.data.model_copy(
            update={
                "substances": [
                    MaterialSubstance(
                        substance_uuid=node.data.substances[0].substance_uuid,
                        name="NaCl",
                        quantity=5,
                        quantity_unit="ug",
                        physical_state="solid",
                    )
                ]
            }
        )
        observed_node = node.model_copy(
            update={
                "position": MaterialPosition(
                    position_x=10, position_y=20, position_z=30
                ),
                "data": changed_data,
            }
        )
        snapshot = MaterialSnapshot(
            root_material_uuid=created.data.root_material_uuid,
            nodes=[observed_node],
        )

        diff = service.compare_snapshot(snapshot)
        assert [(change.section, change.changed_fields) for change in diff.changes] == [
            ("position", ["position_x", "position_y", "position_z"]),
            ("data", ["substances"]),
        ]

        result = service.apply_snapshot(
            _mutation("apply_material_snapshot"), snapshot
        )
        updated = result.data.nodes[0]
        assert updated.material.version == 2
        assert updated.position_version == 2
        assert updated.data.version == 2
        assert updated.data.content_version == 2
        assert updated.data.substances[0].quantity == 5
        assert {
            item.aggregate_uuid for item in result.affected
        } == {updated.material.material_uuid}
    finally:
        service.repository.close()


def test_recursive_delete_and_change_feed_are_aggregate_based(tmp_path) -> None:
    service = MaterialsService(MaterialsRepository(tmp_path / "materials.db"))
    try:
        _template(service, "deck-template", "deck", with_site=True)
        _template(service, "tube-template", "tube")
        created = service.create_tree(
            _mutation("create_material_tree"),
            MaterialTreeCreate(
                nodes=[
                    _node("deck", "deck"),
                    _node("tube", "tube", parent="deck"),
                ]
            ),
        )
        result = service.delete_material(
            _mutation("delete_material"),
            MaterialDelete(
                material_uuid=created.data.root_material_uuid,
                recursive=True,
            ),
        )

        assert set(result.data.deleted_material_uuids) == set(
            created.data.client_ref_map.values()
        )
        assert service.list_materials() == []
        changes = service.changes()
        assert changes[-1].delta["deleted"] is True
        assert not hasattr(changes[-1], "delta_json")
        assert service.acknowledge_changes(changes[-1].sequence) == len(changes)
        assert all(item.delivery_status == "acknowledged" for item in service.changes())
    finally:
        service.repository.close()


def test_snapshot_moves_between_sites_in_one_transaction(tmp_path) -> None:
    service = MaterialsService(MaterialsRepository(tmp_path / "materials.db"))
    try:
        _template(service, "root-template", "root")
        _template(service, "carrier-template", "carrier", with_site=True)
        _template(service, "tube-template", "tube")
        created = service.create_tree(
            _mutation("create_material_tree"),
            MaterialTreeCreate(
                nodes=[
                    _node("root", "root"),
                    _node("carrier-1", "carrier", parent="root"),
                    _node("carrier-2", "carrier", parent="root"),
                    _node("tube", "tube", parent="carrier-1"),
                ]
            ),
        )
        identities = created.data.client_ref_map
        base = service.get_tree(created.data.root_material_uuid)
        carrier_1 = next(
            node
            for node in base.nodes
            if node.material.material_uuid == identities["carrier-1"]
        )
        carrier_2 = next(
            node
            for node in base.nodes
            if node.material.material_uuid == identities["carrier-2"]
        )
        service.move_material(
            _mutation("move_material"),
            MaterialMove(
                material_uuid=identities["tube"],
                destination_site_uuid=carrier_1.sites[0].site_uuid,
            ),
        )
        base = service.get_tree(created.data.root_material_uuid)
        changed_nodes = []
        for node in base.nodes:
            if node.material.material_uuid == identities["carrier-1"]:
                node = node.model_copy(
                    update={
                        "sites": [
                            node.sites[0].model_copy(
                                update={"occupied_material_uuid": None}
                            )
                        ]
                    }
                )
            elif node.material.material_uuid == identities["carrier-2"]:
                node = node.model_copy(
                    update={
                        "sites": [
                            node.sites[0].model_copy(
                                update={
                                    "occupied_material_uuid": identities["tube"]
                                }
                            )
                        ]
                    }
                )
            elif node.material.material_uuid == identities["tube"]:
                node = node.model_copy(
                    update={
                        "material": node.material.model_copy(
                            update={
                                "parent_material_uuid": identities["carrier-2"]
                            }
                        )
                    }
                )
            changed_nodes.append(node)

        result = service.apply_snapshot(
            _mutation("apply_material_snapshot"),
            MaterialSnapshot(
                root_material_uuid=base.root_material_uuid,
                nodes=changed_nodes,
            ),
        )

        tube = next(
            node
            for node in result.data.nodes
            if node.material.material_uuid == identities["tube"]
        )
        assert tube.material.parent_material_uuid == identities["carrier-2"]
        assert service.repository.get_site(
            carrier_1.sites[0].site_uuid
        ).occupied_material_uuid is None
        assert service.repository.get_site(
            carrier_2.sites[0].site_uuid
        ).occupied_material_uuid == identities["tube"]
    finally:
        service.repository.close()


def test_search_materials_by_name_returns_list(tmp_path) -> None:
    """search 按 name 精确匹配返回列表；未命中返回 [] 而不是抛错。

    根物料 name 有唯一索引（ux_material_root_name_active），但子物料可以与
    其他根同名，search 需要把根与子节点的命中都返回。
    """
    service = MaterialsService(MaterialsRepository(tmp_path / "materials.db"))
    try:
        _template(service, "deck-template", "deck", with_site=True)
        _template(service, "tube-template", "tube")

        def _renamed(node, name):
            return node.model_copy(
                update={"identity": node.identity.model_copy(update={"name": name})}
            )

        # 树 1：根本身叫 shared-name
        service.create_tree(
            _mutation("create_material_tree"),
            MaterialTreeCreate(nodes=[_renamed(_node("tube-a", "tube"), "shared-name")]),
        )
        # 树 2：根叫 other-root，其子节点叫 shared-name
        service.create_tree(
            _mutation("create_material_tree"),
            MaterialTreeCreate(
                nodes=[
                    _renamed(_node("deck-b", "deck"), "other-root"),
                    _renamed(_node("tube-b", "tube", parent="deck-b"), "shared-name"),
                ]
            ),
        )

        hits = service.search_materials("shared-name")
        assert [item.material.name for item in hits] == ["shared-name", "shared-name"]
        assert {item.material.resource_id for item in hits} == {
            "resource-tube-a",
            "resource-tube-b",
        }
        assert service.search_materials("missing-name") == []
    finally:
        service.repository.close()
