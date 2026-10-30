import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  Clock3,
  Cpu,
  FlaskConical,
  PackageCheck,
  Play,
  Plus,
  ShieldCheck,
} from 'lucide-react'
import { stationOverview } from '../data/demo'
import type { ConnectionMode, PageId, WorkflowTask } from '../types'
import { Button, EmptyState, LinkButton, PageHeader, Panel, PanelHeader, StatusBadge } from '../components/ui'

export function OverviewPage({
  tasks,
  materialTotal,
  workflowLoaded,
  connection,
  onNavigate,
  onNotify,
}: {
  tasks: WorkflowTask[]
  materialTotal: number
  workflowLoaded: number
  connection: ConnectionMode
  onNavigate: (page: PageId) => void
  onNotify: (message: string) => void
}) {
  const runningCount = tasks.filter((task) => task.status === 'running').length
  const waitingCount = tasks.filter((task) => task.status === 'admission_blocked').length
  const succeededCount = tasks.filter((task) => task.status === 'succeeded').length
  const attentionTasks = tasks.filter((task) => [
    'admission_blocked', 'paused', 'failed', 'timeout', 'intervention_required', 'execution_unknown', 'unknown',
  ].includes(task.status))
  const kpis = [
    { label: '工作流就绪', value: String(workflowLoaded), note: 'Edge 已加载定义', icon: Cpu, tone: 'blue', percent: Math.min(100, workflowLoaded * 5) },
    { label: '运行任务', value: String(runningCount), note: `${waitingCount} 个等待资源`, icon: Activity, tone: 'violet', percent: tasks.length ? Math.round((runningCount / tasks.length) * 100) : 0 },
    { label: '物料库存', value: String(materialTotal), note: '权威物料实例', icon: PackageCheck, tone: 'teal', percent: Math.min(100, Math.round((materialTotal / 150) * 100)) },
    { label: '任务完成', value: String(succeededCount), note: `当前读取 ${tasks.length} 个任务`, icon: CheckCircle2, tone: 'green', percent: tasks.length ? Math.round((succeededCount / tasks.length) * 100) : 0 },
  ]
  return (
    <div className="page overview-page">
      <PageHeader
        eyebrow="LABORATORY COMMAND CENTER"
        title="实验室运营总览"
        description={connection === 'loading'
          ? '正在读取 Edge 任务与物料状态。'
          : connection === 'error'
            ? 'Edge 数据暂不可用，当前不对实验室运行状态作出判断。'
            : connection === 'demo'
              ? '当前为显式演示模式，内容不代表实验室真实运行状态。'
              : attentionTasks.length
                ? `Edge 任务与物料状态已汇总，当前有 ${attentionTasks.length} 项需要操作员关注。`
                : 'Edge 任务与物料状态已汇总，当前没有需要操作员处理的异常。'}
        actions={
          <>
            <Button icon={<Cpu size={16} />} onClick={() => onNavigate('materials')}>设备与库位</Button>
            <Button icon={<Clock3 size={16} />} onClick={() => onNotify('运行报告导出将在文件服务接入后开放')}>运行报告</Button>
            <Button tone="primary" icon={<Plus size={17} />} onClick={() => onNavigate('tasks')}>
              创建实验任务
            </Button>
          </>
        }
      />

      <section className="kpi-grid" aria-label="实验室关键指标">
        {kpis.map(({ label, value, note, icon: Icon, tone, percent }) => (
          <article className={`kpi-card kpi-${tone}`} key={label}>
            <div className="kpi-icon"><Icon size={20} /></div>
            <div className="kpi-copy"><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
            <div className="kpi-progress" aria-label={`${label} ${percent}%`}><span style={{ width: `${percent}%` }} /></div>
          </article>
        ))}
      </section>

      <section className="overview-layout">
        <Panel className="lab-map-card">
          <PanelHeader
            title="实验室工站布局"
            description="空间布局基线；设备遥测接入后叠加实时状态"
            action={<span className="live-badge">布局参考</span>}
          />
          <div className="lab-map">
            <div className="map-zone map-zone-a">原料区</div>
            <div className="map-zone map-zone-b">过程区</div>
            <div className="map-zone map-zone-c">检测区</div>
            <div className="map-route route-a" />
            <div className="map-route route-b" />
            {stationOverview.map((station) => (
              <button
                type="button"
                className="station station-layout"
                style={{ left: `${station.x}%`, top: `${station.y}%` }}
                key={station.code}
                onClick={() => onNotify(`${station.code} ${station.name}：设备详情接口待接入`)}
              >
                <span>{station.code}</span>
                <div><strong>{station.name}</strong><small>设备遥测待接入</small></div>
              </button>
            ))}
          </div>
          <div className="map-legend"><span><i className="legend-layout" />工站布局节点</span><small>当前不表达设备运行状态</small></div>
        </Panel>

        <Panel className="active-tasks-card">
          <PanelHeader
            title="活动任务"
            description="按风险与更新时间排序"
            action={<LinkButton onClick={() => onNavigate('tasks')}>查看全部</LinkButton>}
          />
          <div className="activity-list">
            {tasks.slice(0, 4).map((task) => (
              <button type="button" className="activity-row" key={task.uuid} onClick={() => onNavigate('tasks')}>
                <span className={`task-orb task-orb-${task.status}`}><Play size={12} fill="currentColor" /></span>
                <div><strong>{task.uuid}</strong><small>{task.sample} · {task.current}</small></div>
                <StatusBadge status={task.status} />
                <b>{task.progress}%</b>
              </button>
            ))}
          </div>
        </Panel>

        <Panel className="attention-card">
          <PanelHeader title="需要处理" description="不会被运行态淹没的异常" action={<span className="count-badge">{attentionTasks.length}</span>} />
          <div className="attention-list">
            {attentionTasks.slice(0, 2).map((task) => (
              <article className={`attention ${task.status === 'failed' || task.status === 'timeout' ? 'attention-danger' : 'attention-warning'}`} key={task.uuid}>
                <span>{task.status === 'failed' || task.status === 'timeout' ? <FlaskConical size={18} /> : <AlertTriangle size={18} />}</span>
                <div><strong>{task.uuid} · {task.current}</strong><p>{task.workflowName}，最近更新 {task.updatedAt}。</p></div>
                <button type="button" aria-label={`查看任务 ${task.uuid}`} onClick={() => onNavigate('tasks')}><ArrowUpRight size={16} /></button>
              </article>
            ))}
            {!attentionTasks.length ? (
              <EmptyState
                title={connection === 'connected' ? '当前没有异常' : '暂无权威异常数据'}
                description={connection === 'connected' ? '任务队列与物料准入状态正常。' : 'Edge 连接恢复后再判断任务与物料状态。'}
              />
            ) : null}
          </div>
          <div className="safety-strip safety-unavailable"><ShieldCheck size={17} /><span><strong>安全联锁状态未接入</strong><small>请以设备控制系统为准</small></span></div>
        </Panel>

        <Panel className="event-card">
          <PanelHeader title="最近任务状态" description="来自 Edge Workflow Task" action={<Activity size={18} />} />
          <div className="event-list">
            {tasks.slice(0, 4).map((task) => (
              <div className="event-row" key={task.uuid}>
                <time>{task.updatedAt}</time><span>TASK</span><p>{task.uuid} · {task.current}</p>
              </div>
            ))}
            {!tasks.length ? <EmptyState title="暂无任务状态" description={connection === 'connected' ? 'Edge 当前没有工作流任务。' : 'Edge 连接尚未就绪。'} /> : null}
          </div>
        </Panel>
      </section>
    </div>
  )
}
