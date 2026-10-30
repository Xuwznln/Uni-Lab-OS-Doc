import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { loadHumanInteractions, replyHumanInteraction } from '../lib/edgeClient'
import type { HumanInteraction } from '../lib/edgeClient'
import { Button, Panel } from './ui'

function InteractionForm({ request, connected, onNotify }: { request: HumanInteraction; connected: boolean; onNotify: (message: string) => void }) {
  const args = request.arguments
  const [choice, setChoice] = useState('')
  const [text, setText] = useState(args.text_default ?? args.default ?? '')
  const [numbers, setNumbers] = useState((args.number_defaults ?? args.defaults ?? []).map(String))
  const [points, setPoints] = useState<number[][]>([])
  const [corners, setCorners] = useState<number[][]>([])
  const [drawingCorners, setDrawingCorners] = useState(false)
  const [size, setSize] = useState({ width: 1, height: 1 })
  const imageRef = useRef<HTMLImageElement>(null)
  const queryClient = useQueryClient()
  const form = request.kind === 'review' ? args.forms?.[args.options?.indexOf(choice) ?? -1] : request.kind
  const expired = Date.now() >= Date.parse(request.deadline_at)
  const labels = args.number_labels ?? args.labels ?? []
  const mutation = useMutation({
    mutationFn: async () => {
      if (!connected || expired) throw new Error('交互已超时或 Edge 未连接')
      const values = numbers.map(Number)
      if (numbers.some(v => v.trim() === '') || values.some(v => !Number.isFinite(v))) throw new Error('请填写有效数值')
      const coordinates = { x: points.map(p => p[0]), y: points.map(p => p[1]), corner_x: corners.map(p => p[0]), corner_y: corners.map(p => p[1]) }
      let reply: Record<string, string | number[]>
      if (request.kind === 'review') reply = { choice, text, values, ...(form === 'sketch' ? coordinates : { x: [], y: [], corner_x: [], corner_y: [] }) }
      else if (request.kind === 'choose') reply = { choice }
      else if (request.kind === 'text') reply = { text }
      else if (request.kind === 'numbers') reply = { values }
      else reply = coordinates
      return replyHumanInteraction(request.uuid, reply)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['human-interactions', request.task_uuid] })
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] })
      onNotify('人工回复已保存，工作流将继续。')
    },
    onError: error => onNotify(error instanceof Error ? error.message : '人工回复失败'),
  })
  return <Panel className="human-interaction-panel">
    <div style={{ padding: 20, display: 'grid', gap: 12 }}>
      <strong>等待人工处理</strong>
      <p style={{ whiteSpace: 'pre-wrap' }}>{args.prompt}</p>
      <small>截止时间：{new Date(request.deadline_at).toLocaleString()} · 当前作业 {request.job_uuid.slice(0, 8)}</small>
      {args.options && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>{args.options.map((option, index) => <Button key={option} tone={choice === option ? 'primary' : undefined} disabled={!connected || expired || mutation.isPending} onClick={() => setChoice(option)}>{args.labels?.[index] || option}</Button>)}</div>}
      {(form === 'text' || (form === 'numbers' && request.kind === 'review')) && <label>文本 / 条带标识<input aria-label="人工文本输入" value={text} onChange={event => setText(event.target.value)} /></label>}
      {form === 'numbers' && <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>{labels.map((label, index) => <label key={label}>{label}<input type="number" step="any" aria-label={label} value={numbers[index] ?? ''} onChange={event => setNumbers(old => old.map((value, i) => i === index ? event.target.value : value))} /></label>)}</div>}
      {args.image && <div style={{ position: 'relative', width: 'min(100%, 850px)', alignSelf: 'start' }}>
        <img ref={imageRef} alt="本次作业预览" src={args.image} style={{ width: '100%', display: 'block' }} onLoad={() => { const img = imageRef.current; if (img) setSize({ width: img.naturalWidth, height: img.naturalHeight }) }} />
        {form === 'sketch' && <svg role="img" aria-label="点击绘制闭合轮廓或四角" viewBox={`0 0 ${size.width} ${size.height}`} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', cursor: 'crosshair', touchAction: 'none' }} onPointerDown={event => {
          const rect = event.currentTarget.getBoundingClientRect()
          const point = [(event.clientX - rect.left) * size.width / rect.width, (event.clientY - rect.top) * size.height / rect.height]
          if (drawingCorners) setCorners(old => old.length < 4 ? [...old, point] : old)
          else setPoints(old => [...old, point])
        }}>
          <polygon points={points.map(p => p.join(',')).join(' ')} fill="#29b6f633" stroke="#00e5ff" strokeWidth={Math.max(2, size.width / 350)} />
          {corners.map((point, index) => <g key={index}><circle cx={point[0]} cy={point[1]} r={size.width / 100} fill="#ffb300" /><text x={point[0]} y={point[1]} fontSize={size.width / 35}>{index + 1}</text></g>)}
        </svg>}
      </div>}
      {form === 'sketch' && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Button onClick={() => setDrawingCorners(false)} tone={!drawingCorners ? 'primary' : undefined}>绘制闭合轮廓（{points.length} 点）</Button>
        <Button onClick={() => setDrawingCorners(true)} tone={drawingCorners ? 'primary' : undefined}>标板四角（左上→右上→右下→左下）</Button>
        <Button onClick={() => { setPoints([]); setCorners([]) }}>清空手绘</Button>
      </div>}
      <Button tone="primary" disabled={!connected || expired || mutation.isPending || (!!args.options && !choice) || (form === 'sketch' && (points.length < 3 || (corners.length !== 0 && corners.length !== 4)))} onClick={() => mutation.mutate()}>{mutation.isPending ? '提交中…' : '提交本次回复'}</Button>
    </div>
  </Panel>
}

export function HumanInteractionPanel({ taskUuid, connected, onNotify }: { taskUuid: string; connected: boolean; onNotify: (message: string) => void }) {
  const requests = useQuery({ queryKey: ['human-interactions', taskUuid], queryFn: () => loadHumanInteractions(taskUuid), enabled: connected && !!taskUuid, refetchInterval: 1000 })
  return <>{requests.data?.filter(item => item.status === 'pending').map(request => <InteractionForm key={request.uuid} request={request} connected={connected} onNotify={onNotify} />)}</>
}
