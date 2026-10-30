import type { ButtonHTMLAttributes, PropsWithChildren, ReactNode } from 'react'
import { ArrowRight, RefreshCw } from 'lucide-react'
import type { TaskPresentationStatus } from '../types'

export function Panel({ className = '', children }: PropsWithChildren<{ className?: string }>) {
  return <section className={`panel ${className}`}>{children}</section>
}

export function PanelHeader({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="panel-header">
      <div>
        <h2>{title}</h2>
        {description ? <p>{description}</p> : null}
      </div>
      {action}
    </div>
  )
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string
  title: string
  description: string
  actions?: ReactNode
}) {
  return (
    <header className="page-header">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  )
}

export function Button({
  tone = 'secondary',
  icon,
  children,
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: 'primary' | 'secondary' | 'ghost' | 'danger'
  icon?: ReactNode
}) {
  return (
    <button className={`button button-${tone} ${className}`} {...props}>
      {icon}
      <span>{children}</span>
    </button>
  )
}

export function RefreshButton({ onClick }: { onClick?: () => void }) {
  return (
    <Button icon={<RefreshCw size={16} />} onClick={onClick}>
      刷新状态
    </Button>
  )
}

const taskStatusLabels: Record<TaskPresentationStatus, string> = {
  running: '运行中',
  admission_blocked: '等待资源',
  succeeded: '已完成',
  failed: '失败',
  pending: '待运行',
  paused: '已暂停',
  canceling: '取消中',
  canceled: '已取消',
  timeout: '超时',
  intervention_required: '需要干预',
  execution_unknown: '结果待确认',
  unknown: '状态未知',
}

export function StatusBadge({ status }: { status: TaskPresentationStatus }) {
  return <span className={`status-badge status-${status}`}>{taskStatusLabels[status]}</span>
}

export function LinkButton({ children, onClick }: PropsWithChildren<{ onClick?: () => void }>) {
  return (
    <button className="link-button" onClick={onClick}>
      {children}
      <ArrowRight size={14} />
    </button>
  )
}

export function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  )
}
