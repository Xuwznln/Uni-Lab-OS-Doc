from uuid import uuid4

from unilabos.client.materials import LocalMaterialsClient
from unilabos.resources import materials
from unilabos.resources.resource_tracker import ResourceTreeSet
from unilabos.server.database.repositories.materials import MaterialsRepository
from unilabos.server.services.materials import MaterialsService


class _SubstanceTarget:
    name = "target"

    def __init__(self) -> None:
        self.substances = []

    def set_liquids(self, substances) -> None:
        self.substances = substances


def test_material_helper_writes_solid_substance() -> None:
    target = _SubstanceTarget()

    result = materials.apply_substances(
        target,
        names=["NaCl"],
        amounts=[250.0],
        is_solid=[True],
    )

    assert result == [target]
    assert target.substances == [("NaCl", 250.0, materials.SOLID_UNIT)]


class _OrderedParent:
    """带 _ordering 的最小父级替身（deck/carrier 的 slot 解析面）。"""

    def __init__(self, keys) -> None:
        self._ordering = {key: None for key in keys}


def test_resolve_site_spot_prefers_label_over_digits() -> None:
    parent = _OrderedParent(["12", "A1", "B1"])

    # "12" 是 label：优先按 label 命中 index 0，而不是被 isdigit 当索引 12
    assert materials.resolve_site_spot(parent, "12") == 0
    assert materials.resolve_site_spot(parent, "A1") == 1
    # 非 label 的纯数字字符串按 0-based 索引
    assert materials.resolve_site_spot(parent, "2") == 2
    # int 是内部宽容形态（等价数字字符串）
    assert materials.resolve_site_spot(parent, 1) == 1
    # 未指定：由父级默认排布
    assert materials.resolve_site_spot(parent, None) is None
    assert materials.resolve_site_spot(parent, "") is None


def _graph_tree(root_uuid: str, child_uuid: str) -> ResourceTreeSet:
    """模拟开机图中的一棵「已带 uuid」的物料树。"""

    return ResourceTreeSet.from_raw_dict_list(
        [
            {
                "id": "boot-carrier",
                "uuid": root_uuid,
                "name": "boot-carrier",
                "type": "container",
                "class": "Container",
                "config": {"type": "Container"},
                "data": {},
                "extra": {},
                "template_name": "boot-carrier-template",
                "pose": {"position": {"x": 0, "y": 0, "z": 0}},
                "sites": [],
                "sites_initialized": True,
            },
            {
                "id": "boot-tube",
                "uuid": child_uuid,
                "parent_uuid": root_uuid,
                "name": "boot-tube",
                "type": "container",
                "class": "Container",
                "config": {"type": "Container"},
                "data": {},
                "extra": {},
                "template_name": "boot-tube-template",
                "pose": {"position": {"x": 0, "y": 0, "z": 0}},
                "sites": [],
                "sites_initialized": True,
            },
        ]
    )


def test_materials_ensure_adopts_graph_uuid_and_is_idempotent(tmp_path) -> None:
    """开机权威对齐：权威缺失时以图中 uuid 显式创建；重复开机不再新建。

    host 与 slave 的开机物料语义统一走本入口（原 /c2s_update_resource_tree
    add 上报 + uuid_mapping 换 uuid 的机制已退役）。
    """
    service = MaterialsService(MaterialsRepository(tmp_path / "materials.db"))
    gateway = LocalMaterialsClient(service)
    try:
        root_uuid, child_uuid = str(uuid4()), str(uuid4())

        ensured = materials.ensure(
            _graph_tree(root_uuid, child_uuid), gateway=gateway
        )
        assert len(ensured.trees) == 1
        assert ensured.trees[0].root_node.res_content.uuid == root_uuid
        assert {node.res_content.uuid for node in ensured.all_nodes} == {
            root_uuid,
            child_uuid,
        }
        # 权威中的 uuid 与图完全一致（adopt，不换 uuid）
        assert (
            service.get_material(root_uuid).material.material_uuid == root_uuid
        )

        # 第二次开机（同一张图）：命中权威，不再创建
        again = materials.ensure(
            _graph_tree(root_uuid, child_uuid), gateway=gateway
        )
        assert again.trees[0].root_node.res_content.uuid == root_uuid
        roots = [
            item
            for item in service.list_materials(roots_only=True)
        ]
        assert len(roots) == 1
    finally:
        service.repository.close()
