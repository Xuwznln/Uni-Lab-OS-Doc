"""Graph Authority：管理命名设备图快照并导出实时拓扑。

图数据与物料权威共享 ``materials.db``：运行态节点和边分别来自
``material``、``material_link``，命名快照存于 ``lab_graph``。三个模型共用
同一连接、写锁和事务边界。

- 图名派生稳定 uuid（``GRAPH_NAMESPACE`` 的 uuid5），同名 upsert 即更新
  并递增 revision；软删除后重建复活同一 uuid；
- 读取按 uuid 与名称双通道解析（``unilab -g <uuid|名称>`` / CLI identity）；
- upsert 是唯一创建/更新入口，内含节点身份对账：草稿图（节点/Site 无
  uuid）由权威发号（按图 uuid + 节点 id 稳定派生），再次上传复用既有
  身份；携带 uuid 的图与既有记录冲突时拒绝。返回 ``summary``
  （新建/更新/移除/不变/发号统计）；
- ``live_payload`` 从物料权威实时序列化当前真实拓扑（节点 + 边），
  与快照 payload（上传时刻存档）互补。
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from unilabos.protocol.base import canonical_hash
from unilabos.resources.objects.resource import normalize_legacy_graph_node
from unilabos.resources.objects.site import normalize_available_sites
from unilabos.server.services.materials.store import MaterialsRepository
from unilabos.server.database.tables.materials import (
    LabGraphRecord,
    MaterialLinkRecord,
)

if TYPE_CHECKING:
    from unilabos.server.services.materials import MaterialsService

#: 图名 -> uuid5 的稳定命名空间；跨机器/跨次启动同名同 uuid。
GRAPH_NAMESPACE = uuid5(NAMESPACE_URL, "unilabos://graph-authority")


class GraphError(Exception):
    """业务错误：``code`` 供 API/CLI 映射 envelope 业务码。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def graph_uuid_for_name(name: str) -> str:
    return str(uuid5(GRAPH_NAMESPACE, name.strip().lower()))


def validate_graph_payload(payload: Any) -> dict[str, Any]:
    """结构校验：node-link JSON 必须是含 ``nodes`` 数组的对象。"""

    if not isinstance(payload, dict):
        raise GraphError("invalid_payload", "graph payload 必须是 JSON 对象")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        raise GraphError("invalid_payload", "graph payload 必须包含 nodes 数组")
    links = payload.get("links")
    if links is not None and not isinstance(links, list):
        raise GraphError("invalid_payload", "graph links 必须是数组")
    return payload


def _node_template_name(node: Mapping[str, Any]) -> str:
    """推导 Site 归属的模板名：根字段（旧图 class 已由读取边界回填）> config.type（deck 等 PLR 物料）。"""

    config = node.get("config") if isinstance(node.get("config"), Mapping) else {}
    for candidate in (node.get("template_name"), config.get("type")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _is_plr_flat_site(site: Mapping[str, Any]) -> bool:
    """PLR 平铺 Site（图纸形态）没有 canonical 身份字段。"""

    return not any(
        key in site for key in ("schema_version", "uuid", "material_uuid", "template_name")
    )


def _canonicalize_site(
    site: Mapping[str, Any],
    *,
    ordinal: int,
    node_uuid: str,
    template_name: str,
    previous_by_label: Mapping[str, Mapping[str, Any]],
    node_id: str,
) -> dict[str, Any]:
    """把草稿 Site（PLR 平铺或缺身份的 canonical）补齐为权威 ResourceSite 形状。

    身份字段发号规则与节点一致：优先复用既有快照同 label 的 uuid，
    否则按节点 uuid + label 稳定派生。
    """

    label = str(site.get("label") or "").strip()
    if not label:
        raise GraphError(
            "invalid_payload", f"节点 {node_id} 的 Site[{ordinal}] 缺少 label"
        )
    occupied = site.get("occupied_by")
    if occupied is not None:
        raise GraphError(
            "invalid_payload",
            f"节点 {node_id} 的 Site {label} 使用了 occupied_by；"
            "占用关系请提供 occupied_material_uuid",
        )

    previous = previous_by_label.get(label) or {}
    site_uuid = (
        str(site.get("uuid") or "").strip()
        or str(previous.get("uuid") or "").strip()
        or str(uuid5(UUID(node_uuid), f"site:{label}"))
    )

    if _is_plr_flat_site(site):
        position = site.get("position") if isinstance(site.get("position"), Mapping) else {}
        size = site.get("size") if isinstance(site.get("size"), Mapping) else {}
        point = {axis: float(position.get(axis, 0.0) or 0.0) for axis in ("x", "y", "z")}
        pose: dict[str, Any] = {
            "size": {
                "width": float(size.get("width", 0.0) or 0.0),
                "height": float(size.get("height", 0.0) or 0.0),
                "depth": float(size.get("depth", 0.0) or 0.0),
            },
            "position": dict(point),
            "position3d": dict(point),
        }
        allowed = list(site.get("content_type") or site.get("allowed_resource_categories") or [])
        canonical: dict[str, Any] = {
            "schema_version": 1,
            "uuid": site_uuid,
            "template_name": template_name,
            "material_uuid": node_uuid,
            "index": site.get("index", ordinal),
            "label": label,
            "visible": bool(site.get("visible", True)),
            "occupied_material_uuid": site.get("occupied_material_uuid"),
            "pose": pose,
            "allowed_resource_categories": allowed,
            "parent_link": str(site.get("parent_link") or label),
            "description": str(site.get("description") or ""),
            "meta_data": dict(site.get("meta_data") or {}),
            "extra": dict(site.get("extra") or {}),
        }
        return canonical

    canonical = copy.deepcopy(dict(site))
    canonical.setdefault("schema_version", 1)
    canonical["uuid"] = site_uuid
    canonical["material_uuid"] = str(canonical.get("material_uuid") or "").strip() or node_uuid
    canonical["template_name"] = (
        str(canonical.get("template_name") or "").strip() or template_name
    )
    canonical.setdefault("index", ordinal)
    canonical.setdefault("label", label)
    return canonical


def reconcile_graph_payload(
    payload: Mapping[str, Any],
    previous_payload: Optional[Mapping[str, Any]],
    graph_uuid: str,
    device_site_templates: Optional[Mapping[str, Sequence[Any]]] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """节点身份对账：草稿发号、既有身份复用、冲突拒绝，并产出 diff 摘要。

    - 节点 uuid：payload 携带 > 既有快照同 id 节点 > ``uuid5(图 uuid, "node:<id>")``
      稳定派生（同一草稿反复导入结果一致）；
    - payload 与既有快照同 id 节点 uuid 不一致 → ``identity_conflict`` 拒绝；
    - 层级只认 ``parent``/``parent_uuid``；旧图的 ``children`` 列表剥离不入库；
    - Site 身份（uuid/material_uuid/template_name）随所属节点补齐；
      ``config.sites`` 的 PLR 平铺图纸转为根级 canonical 快照；
    - ``device_site_templates``（注册表 ``template_name -> available_sites``）
      提供时，未声明 sites 的设备节点在此实例化模板快照（含权威空快照
      ``sites_initialized=True, sites=[]``），满足 Edge「不得本地补齐」契约；
    - summary: created/updated/removed/unchanged 节点 id 列表 +
      ``uuid_assigned`` 发号计数。
    """

    normalized = copy.deepcopy(dict(payload))
    nodes = normalized.get("nodes") or []
    previous_nodes: dict[str, Mapping[str, Any]] = {}
    for node in (previous_payload or {}).get("nodes") or []:
        node_id = str(node.get("id") or "").strip()
        if node_id:
            previous_nodes[node_id] = node

    seen_ids: set[str] = set()
    seen_uuids: set[str] = set()
    uuid_assigned = 0
    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []

    for node in nodes:
        if not isinstance(node, dict):
            raise GraphError("invalid_payload", "graph 节点必须是对象")
        node_id = str(node.get("id") or node.get("name") or "").strip()
        if not node_id:
            raise GraphError("invalid_payload", "graph 节点缺少 id")
        if node_id in seen_ids:
            raise GraphError("invalid_payload", f"graph 节点 id 重复: {node_id}")
        seen_ids.add(node_id)

        previous = previous_nodes.get(node_id)
        previous_uuid = str((previous or {}).get("uuid") or "").strip()
        incoming_uuid = str(node.get("uuid") or "").strip()
        if incoming_uuid and previous_uuid and incoming_uuid != previous_uuid:
            raise GraphError(
                "identity_conflict",
                f"节点 {node_id} 的 uuid ({incoming_uuid}) 与权威已登记 uuid "
                f"({previous_uuid}) 不一致；如需重建身份请先删除图或移除 uuid 字段",
            )
        node_uuid = incoming_uuid or previous_uuid
        if not node_uuid:
            node_uuid = str(uuid5(UUID(graph_uuid), f"node:{node_id}"))
            uuid_assigned += 1
        if node_uuid in seen_uuids:
            raise GraphError("invalid_payload", f"graph 节点 uuid 重复: {node_uuid}")
        seen_uuids.add(node_uuid)
        node["uuid"] = node_uuid

        # 旧图字段兼容：template_name 缺失时取 class，落库即升级为新契约形态。
        normalize_legacy_graph_node(node)
        # 父子关系只由 parent/parent_uuid 表达；旧图的 children 派生列表不入库。
        node.pop("children", None)

        # ── Site 身份补齐（config.sites 平铺图纸 → 根级 canonical） ──
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        raw_sites = node.get("sites")
        config_sites = config.get("sites") if isinstance(config, dict) else None
        if raw_sites is None and isinstance(config_sites, list):
            raw_sites = config_sites
            config.pop("sites", None)
        if (
            raw_sites is None
            and device_site_templates is not None
            and str(node.get("type") or "") == "device"
        ):
            # 设备节点未声明 Site：按注册表模板在创建入口实例化（微后端边界）。
            template_key = _node_template_name(node)
            template_sites = normalize_available_sites(
                device_site_templates.get(template_key)
            )
            if template_sites:
                raw_sites = template_sites
            else:
                # 权威空快照：设备无模板位点时同样必须显式初始化。
                node["sites"] = []
                node["sites_initialized"] = True
        if isinstance(raw_sites, list) and raw_sites:
            template_name = _node_template_name(node)
            if not template_name:
                raise GraphError(
                    "invalid_payload",
                    f"节点 {node_id} 声明了 sites 但缺少 template_name/class/config.type，"
                    "无法确定 Site 模板归属",
                )
            previous_by_label = {
                str(site.get("label") or ""): site
                for site in (previous or {}).get("sites") or []
                if isinstance(site, Mapping)
            }
            before = [copy.deepcopy(site) for site in raw_sites]
            node["sites"] = [
                _canonicalize_site(
                    site,
                    ordinal=ordinal,
                    node_uuid=node_uuid,
                    template_name=template_name,
                    previous_by_label=previous_by_label,
                    node_id=node_id,
                )
                for ordinal, site in enumerate(raw_sites, start=1)
            ]
            if node["sites"] != before:
                uuid_assigned += sum(
                    1 for raw in before if not str(raw.get("uuid") or "").strip()
                )
            node["sites_initialized"] = True
            if not str(node.get("template_name") or "").strip():
                node["template_name"] = template_name

    # ── 占用关系对账：子物料按 parent + 位置匹配写入 Site 占用。──
    # 权威快照必须自带占用（树重建/deserialize 时 child 尚未注入 uuid，
    # 依赖 Site 的 expected_occupant 回填），创建入口是唯一发号边界。
    for node in nodes:
        sites = node.get("sites")
        if not isinstance(sites, list) or not sites:
            continue
        node_id = str(node.get("id") or node.get("name") or "")
        for child in nodes:
            if str(child.get("parent") or "") != node_id:
                continue
            child_position = ((child.get("pose") or {}).get("position")) or {}
            child_key = tuple(
                float(child_position.get(axis, 0.0) or 0.0) for axis in ("x", "y", "z")
            )
            for site in sites:
                if site.get("occupied_material_uuid"):
                    continue
                site_position = ((site.get("pose") or {}).get("position")) or {}
                site_key = tuple(
                    float(site_position.get(axis, 0.0) or 0.0) for axis in ("x", "y", "z")
                )
                if site_key == child_key:
                    site["occupied_material_uuid"] = child["uuid"]
                    break

    # ── diff 分类（发号与占用全部就绪后计算）──
    for node in nodes:
        node_id = str(node.get("id") or node.get("name") or "")
        previous = previous_nodes.get(node_id)
        if previous is None:
            created.append(node_id)
        elif canonical_hash(node) == canonical_hash(dict(previous)):
            unchanged.append(node_id)
        else:
            updated.append(node_id)

    removed = sorted(set(previous_nodes) - seen_ids)
    summary = {
        "created": created,
        "updated": updated,
        "removed": removed,
        "unchanged": unchanged,
        "uuid_assigned": uuid_assigned,
    }
    return normalized, summary


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def link_payload(record: MaterialLinkRecord, uuid_to_id: Mapping[str, str]) -> dict[str, Any]:
    """把一行 material_link 还原成 node-link 的 link 对象。"""

    link: dict[str, Any] = {
        "source": uuid_to_id.get(
            record.source_material_uuid, record.source_material_uuid
        ),
        "target": uuid_to_id.get(
            record.target_material_uuid, record.target_material_uuid
        ),
        "source_uuid": record.source_material_uuid,
        "target_uuid": record.target_material_uuid,
    }
    if record.link_type:
        link["type"] = record.link_type
    if record.source_handle:
        link["sourceHandle"] = record.source_handle
    if record.target_handle:
        link["targetHandle"] = record.target_handle
    for key, value in record.extra_json.items():
        link.setdefault(key, value)
    return link


class GraphService(MaterialsRepository):
    """管理图快照，并从物料权威生成实时拓扑。

    ``database`` 通常接收 :class:`MaterialsService` 实例，以共享
    ``materials.db`` 的连接和写锁；独立测试也可传入数据库路径。
    """

    def __init__(
        self,
        database: "str | Path | MaterialsRepository",
        materials: "Optional[MaterialsService]" = None,
    ):
        super().__init__(database)
        if materials is None:
            from unilabos.server.services.materials.core import MaterialsService

            if isinstance(database, MaterialsService):
                materials = database
        self._materials = materials

    # ── 读形态 ──────────────────────────────────────────────

    @staticmethod
    def _read(record: LabGraphRecord) -> dict[str, Any]:
        return {
            "uuid": record.uuid,
            "name": record.name,
            "description": record.description,
            "tags": list(record.tags),
            "meta_data": dict(record.meta_data),
            "payload": record.payload,
            "revision": record.revision,
            "create_time": record.create_time,
            "update_time": record.update_time,
        }

    @staticmethod
    def _summary(record: LabGraphRecord) -> dict[str, Any]:
        nodes = record.payload.get("nodes")
        return {
            "uuid": record.uuid,
            "name": record.name,
            "description": record.description,
            "tags": list(record.tags),
            "meta_data": dict(record.meta_data),
            "revision": record.revision,
            "create_time": record.create_time,
            "update_time": record.update_time,
            "node_count": len(nodes) if isinstance(nodes, list) else 0,
        }

    def _resolve(self, identity: str) -> LabGraphRecord:
        identity = (identity or "").strip()
        if not identity:
            raise GraphError("invalid_input", "graph 标识不能为空")
        record = MaterialsRepository.get_graph(self, identity)
        if record is None:
            record = self.find_graph_by_name(identity)
        if record is None:
            raise GraphError("not_found", f"graph 不存在: {identity}")
        return record

    # ── CRUD ───────────────────────────────────────────────

    def upsert_graph(
        self,
        *,
        name: str,
        payload: Any,
        uuid: Optional[str] = None,
        tags: Sequence[Any] = (),
        description: Optional[str] = None,
        meta_data: Optional[Mapping[str, Any]] = None,
        device_site_templates: Optional[Mapping[str, Sequence[Any]]] = None,
    ) -> dict[str, Any]:
        """创建/更新图快照（唯一入口）：先做节点身份对账再落库。

        草稿图（节点/Site 无 uuid）由本方法发号；``device_site_templates``
        提供时设备节点的模板 Site 也在此实例化。返回值携带 ``summary``
        （created/updated/removed/unchanged 节点 id 与 uuid_assigned 计数）。
        与既有快照内容一致时不递增 revision。
        """

        name = (name or "").strip()
        if not name:
            raise GraphError("invalid_input", "graph 名称不能为空")
        validated = validate_graph_payload(payload)
        graph_uuid = (uuid or "").strip() or graph_uuid_for_name(name)

        with self.write():
            # 含软删除记录：同名/同 uuid 重建走复活而不是主键冲突。
            existing = MaterialsRepository.get_graph(self, graph_uuid, include_deleted=True)
            if existing is None:
                # uuid 未命中时回退名称唯一键，避免显式 uuid 与既有同名记录冲突。
                existing = self.find_graph_by_name(name)

            previous_payload = existing.payload if existing is not None and existing.deleted_at is None else None
            normalized, summary = reconcile_graph_payload(
                validated,
                previous_payload,
                existing.uuid if existing is not None else graph_uuid,
                device_site_templates=device_site_templates,
            )

            now = _now()
            if existing is not None:
                if previous_payload is not None and canonical_hash(previous_payload) == canonical_hash(normalized):
                    # 内容一致：不递增 revision，直接复用现存快照。
                    result = self._read(existing)
                    result["summary"] = summary
                    return result
                record = existing.model_copy(
                    update={
                        "update_time": now,
                        "deleted_at": None,
                        "name": name,
                        "tags": list(tags),
                        "description": description,
                        "meta_data": dict(meta_data or {}),
                        "payload": normalized,
                        "revision": existing.revision + 1,
                    }
                )
                self.update_graph(record)
            else:
                record = LabGraphRecord(
                    uuid=graph_uuid,
                    create_time=now,
                    update_time=now,
                    name=name,
                    tags=list(tags),
                    description=description,
                    meta_data=dict(meta_data or {}),
                    payload=normalized,
                    revision=1,
                )
                self.insert_graph(record)
        result = self._read(record)
        result["summary"] = summary
        return result

    def get_graph(self, identity: str) -> dict[str, Any]:
        return self._read(self._resolve(identity))

    def get_payload(self, identity: str) -> dict[str, Any]:
        return self._resolve(identity).payload

    def list_graphs(
        self,
        *,
        page: int = 1,
        page_size: int = 100,
        name: str = "",
    ) -> dict[str, Any]:
        records, total = MaterialsRepository.list_graphs(self, 
            page=page, page_size=page_size, name=name
        )
        return {
            "items": [self._summary(record) for record in records],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def delete_graph(self, identity: str) -> None:
        record = self._resolve(identity)
        with self.write():
            self.soft_delete_graph(record.uuid, deleted_at=_now())

    # ── 当前态导出 ──────────────────────────────────────────

    def live_payload(self) -> dict[str, Any]:
        """从物料权威实时序列化当前拓扑（node-link JSON）。

        与快照 payload 的差异：快照是上传/启动时刻的存档（可回滚、可分发），
        这里给的是运行演化后的真实实验室——节点来自 material 聚合树，
        边来自 material_link 表。
        """

        if self._materials is None:
            raise GraphError(
                "unsupported", "当前进程未装配物料服务，无法导出实时拓扑"
            )
        from unilabos.resources.adapters.plr_materials import (
            material_tree_to_resource_tree,
        )

        nodes: list[dict[str, Any]] = []
        uuid_to_id: dict[str, str] = {}
        for aggregate in self._materials.list_materials(roots_only=True):
            tree_read = self._materials.get_tree(
                aggregate.material.material_uuid
            )
            tree_set = material_tree_to_resource_tree(tree_read)
            for node in tree_set.all_nodes:
                nodes.append(node.res_content.model_dump(by_alias=True))
                uuid_to_id[node.res_content.uuid] = node.res_content.id
        links = [
            link_payload(record, uuid_to_id)
            for record in MaterialsRepository.list_links(self)
        ]
        return {"nodes": nodes, "links": links}


__all__ = [
    "GRAPH_NAMESPACE",
    "GraphError",
    "GraphService",
    "graph_uuid_for_name",
    "link_payload",
    "reconcile_graph_payload",
    "validate_graph_payload",
]
