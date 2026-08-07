# Sites 与 HostNode 改名交接清单

本文用于区分已经在 Uni-Lab-OS 内完成的兼容改造，以及需要云端、微前端或产品规则继续确认的事项。

## 先固定两个概念

- `host_node` 作为 **设备类型/注册表键** 保持不变：`@device(id="host_node")`、
  `class="host_node"`、注册表索引、`HostNode` 类名、WebSocket 事件名
  `host_node_ready` 和状态对象名 `host_node_info` 都是协议/角色语义，不是实例 ID。
- `BasicConfig.host_node_name` 是 **可改的运行时实例 ID**：资源根 `id/name`、
  ROS node/action namespace、工作流 `device_name/resource_name`、HostLink 公告字段
  `host_node_id` 与默认路由目标都使用它。默认值仍是 `host_node`。

启动示例：

```bash
unilab --host_node_name west_lab -g graph.json
```

也可在 `local_config.py` 设置 `BasicConfig.host_node_name`，或通过
`UNILABOS_BASICCONFIG_HOST_NODE_NAME` 设置。名字必须符合 ROS node 规则：以字母或
下划线开头，且只含 ASCII 字母、数字、下划线。改名当前需要重启进程。

## 已完成：HostNode

- [x] 启动参数、配置文件、环境变量三条入口已汇总到同一个运行时名称；保留默认值兼容。
- [x] HostNode 资源根的 `id/name` 随实例名变化，`class/registry_name` 仍固定为 `host_node`。
- [x] HostNode 自身的 ROS action client 路径改为 `/devices/<host_node_name>/...`。
- [x] BaseDevice 内 Host 角色判断改用稳定 `registry_name`，不再比较 `device_id/node_name`。
- [x] graph 导入按稳定 `class=host_node` 识别 Host 根，并兼容默认名及配置名的旧无 class 快照。
- [x] graph/直接构造 HostNode 时会拒绝与普通设备或资源重名，避免静默生成两个相同 ID。
- [x] 远端资源合并不再把固定 ID `host_node` 当作唯一 Host 判据。
- [x] `--use_remote_resource` 启动探测查询最终配置的 HostNode ID。
- [x] WebSocket 资源变更/设备管理缺省目标使用当前 HostNode ID；`host_node_ready.data`
  新增 `host_node_id`，事件名保持不变。
- [x] HostLink hello 与 `/api/v1/hostlink/peers` 新增 `host_node_id`；机器 `host_id`
  继续独立使用 `machine_name`。
- [x] 本地状态页/API 的 `host_node_info` 新增 `host_node_id`；对象名保留角色语义。
- [x] 自动生成的 `create_resource` 工作流节点使用当前 HostNode 名称。
- [x] HTTP、组网、最佳实践和 graph 文档中的默认名示例已标注替换规则。
- [x] 默认名与改名契约均有单测；HostLink 已覆盖改名后的资源查询。

## 已完成：sites 根字段

- [x] `ResourceDict.sites` 已提升为与 `barcode` 同级的可选根字段；`None` 与空列表语义分开。
- [x] 每个 site 至少补齐 `uuid/index/label`，其余位置、尺寸、可见性、占用信息等键原样保留。
- [x] 老 `config.sites` 通过唯一漏斗提升到根字段；根字段优先，并清除 `config` 中的重复副本。
- [x] 标准 PyLabRobot `Carrier`、项目 `ItemizedCarrier` 与 `PRCXI9300Deck` 均支持
  PLR → ResourceTreeSet → JSON/ROS → ResourceTreeSet → PLR 往返。
- [x] graph 白名单、legacy graph 转换、ROS `Resource` 消息兼容层、HostNode 已存在节点合并均已覆盖。
- [x] 微后端物料兼容查询允许从 `resource_template.spec_json.resource.sites`
  投影根级 `sites`，不会被字段白名单丢弃。
- [x] site UUID、非连续 index、序列化幂等与微后端 HTTP 查询均有回归测试。

## 需要确认/由云端或微前端继续处理

### HostNode

- [ ] **正式后端数据迁移**：现有物料根、设备记录和索引若以字面值 `host_node` 为主键，
  需要决定“原地改名”“旧名 alias”或“创建新 Host 根”。推荐短期保留旧名 alias，避免历史工作流失效。
- [ ] **云端调度协议**：接收 `host_node_ready` 时读取新增的 `data.host_node_id`，并以
  `devices[].device_id` 为真实目标；不要继续写死 `host_node`。事件名建议保持不变。
- [ ] **历史工作流迁移**：已保存节点中的 `device_name/resource_name/footer` 可能仍指向
  `host_node`。需要确定加载时动态 alias，还是批量迁移快照。
- [ ] **全网唯一性**：本地 graph 已拒绝与普通设备/资源重名，但 HostLink/云端尚未跨进程校验
  HostNode ID。需要确定以实验室、Host 分区还是整个网络为唯一域。
- [ ] **Host 身份口径**：当前 HostLink `host_id/host_name = machine_name`，
  `host_node_id = BasicConfig.host_node_name`。确认两者保持分离；不建议自动绑成同一字段。
- [ ] **热改名**：当前名称仅在启动时解析，运行中改名需要重启。若需要在线改名，必须设计
  ROS endpoint 重建、进行中 job 锁、资源根迁移与 slave 重发现，不应只改内存字符串。
- [ ] **正式后端查询**：正式后端 `/lab/material?id=<host_node_name>` 及写入 API 需确认支持任意
  HostNode ID；微后端库存库本身不自动创建 HostNode 根记录。
- [ ] **外部脚本/监控**：部署脚本、ROS CLI、告警规则若仍请求 `/devices/host_node/...`，
  需改成读取启动配置或 HostLink `/peers.host_node_id`。

### Sites

- [ ] **site UUID 的权威生成方**：当前 Edge 对无 UUID 的旧数据用随机 UUID 补齐，并在后续
  序列化中保留；若原始旧数据每次都重新导入且未持久化，UUID 会重新生成。确认由 Edge、微后端
  还是正式后端首次分配并回写。
- [ ] **表设计**：当前 SQLite 没有独立 site 表。定义存在
  `resource_template.spec_json.resource.sites`，占位关系存在
  `resource_relation(parent_uuid, slot_id=sites.label, child_uuid)`。若每个载架实例的 site
  定义可不同，或需要按 site UUID 查询/加锁，应新增实例级 site 表，而不是继续只存模板 JSON。
- [ ] **关系键选择**：当前 `resource_relation.slot_id` 使用 `sites.label`；`sites.uuid` 尚未进入
  relation 表。确认 label 是否永久不可变；若 label 可改，relation 应持久化 `site_uuid`，label 只作显示。
- [ ] **`occupied_by` 口径**：PLR 序列化当前输出占用资源 `name`，库存关系事实源是
  `child_uuid`。确认前后端统一使用资源 UUID、逻辑 ID 还是 name；推荐协议使用 UUID，UI 再解析名称。
- [ ] **模板态与实例态拆分**：位置/尺寸/content_type 更像模板字段，visible/occupied_by 可能是
  实例运行态。确认哪些字段可按实例覆盖、如何版本化和同步。
- [ ] **冲突规则**：需要后端明确同一个 site 是否只允许一个 child、移动时的 compare-and-set/
  version 规则，以及删除载架/site 时如何处理占用资源。
- [ ] **微前端类型与交互**：前端需要把 `sites` 当根字段读取，使用 `uuid` 做编辑 key，
  使用 `label` 显示/选位；保存时不得再写回 `config.sites`。
- [ ] **正式后端 DTO/ORM**：确认正式后端的物料查询、更新和 WebSocket payload 不会过滤根级
  `sites`，并决定是否需要 site 独立表及迁移脚本。

## 发布前检查

- [ ] 用一个非默认 HostNode 名启动真实 Host + 至少一个 Slave，验证 ROS action、HostLink 查询、
  微前端设备列表、调度下发和结果回收。
- [ ] 用含空 site、占用 site、非连续 index 和旧版无 UUID site 的真实 graph 做一次持久化重启。
- [ ] 对已有正式后端实验室数据做一份只读迁移预演，确认不会出现两个 Host 根或历史工作流失联。
- [ ] 云端与 Edge 对 `host_node_id/site.uuid/occupied_by` 口径确认后，补跨仓协议 fixture。
