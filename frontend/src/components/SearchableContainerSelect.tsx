import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import { Check, ChevronDown, Search } from 'lucide-react'
import './SearchableContainerSelect.css'

export type ContainerSelectOption = { value: string; label: string; description?: string }

type SearchableContainerSelectProps = {
  id?: string
  label: string
  value: string
  options: ContainerSelectOption[]
  placeholder?: string
  disabled?: boolean
  onChange: (value: string) => void
}

export function SearchableContainerSelect({ id, label, value, options, placeholder = '请选择容器', disabled = false, onChange }: SearchableContainerSelectProps) {
  const generatedId = useId()
  const controlId = id || `container-select-${generatedId}`
  const listId = `${controlId}-options`
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const activeOptionRef = useRef<HTMLLIElement>(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeValue, setActiveValue] = useState<string | null>(null)
  const [menuPosition, setMenuPosition] = useState<CSSProperties>({})
  const selected = options.find((option) => option.value === value)
  const search = query.trim().toLocaleLowerCase()
  const filtered = options.filter((option) => `${option.label} ${option.description || ''}`.toLocaleLowerCase().includes(search))
  const matchedIndex = filtered.findIndex((option) => option.value === activeValue)
  const activeIndex = matchedIndex >= 0 ? matchedIndex : filtered.length ? 0 : -1
  const expanded = open && !disabled

  const close = (restoreFocus = false) => {
    setOpen(false)
    if (restoreFocus) triggerRef.current?.focus()
  }
  const show = (fromEnd = false) => {
    if (disabled) return
    setQuery('')
    setActiveValue(selected?.value || (fromEnd ? options.at(-1)?.value : options[0]?.value) || null)
    setOpen(true)
  }
  const choose = (nextValue: string) => {
    if (disabled) return
    onChange(nextValue)
    close(true)
  }

  useEffect(() => {
    if (disabled) setOpen(false)
  }, [disabled])

  useEffect(() => {
    if (!expanded) return
    searchRef.current?.focus()
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown, true)
    return () => document.removeEventListener('pointerdown', onPointerDown, true)
  }, [expanded])

  useLayoutEffect(() => {
    if (!expanded) return
    // 固定定位避开表单滚动区裁剪，并随视口与外层滚动调整。
    const reposition = (event?: Event) => {
      if (event?.target instanceof Node && menuRef.current?.contains(event.target)) return
      const rect = triggerRef.current?.getBoundingClientRect()
      if (!rect) return
      const below = window.innerHeight - rect.bottom - 12
      const above = rect.top - 12
      const placeAbove = below < 200 && above > below
      const available = placeAbove ? above : below
      setMenuPosition({
        left: Math.max(8, rect.left),
        width: Math.min(rect.width, window.innerWidth - 16),
        maxHeight: Math.max(120, Math.min(340, available)),
        ...(placeAbove ? { bottom: window.innerHeight - rect.top + 6 } : { top: rect.bottom + 6 }),
      })
    }
    reposition()
    window.addEventListener('resize', reposition)
    document.addEventListener('scroll', reposition, true)
    return () => {
      window.removeEventListener('resize', reposition)
      document.removeEventListener('scroll', reposition, true)
    }
  }, [expanded])

  useEffect(() => {
    if (expanded) activeOptionRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [expanded, activeIndex, query])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.nativeEvent.isComposing || disabled) return
    if (event.key === 'Escape' && expanded) {
      event.preventDefault()
      event.stopPropagation()
      close(true)
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      event.stopPropagation()
      if (!expanded) show(event.key === 'ArrowUp')
      else if (filtered.length) {
        const direction = event.key === 'ArrowDown' ? 1 : -1
        setActiveValue(filtered[(activeIndex + direction + filtered.length) % filtered.length].value)
      }
    } else if (event.key === 'Enter' && expanded) {
      event.preventDefault()
      event.stopPropagation()
      if (activeIndex >= 0) choose(filtered[activeIndex].value)
    }
  }

  return <div className="searchable-container-select" ref={rootRef} onKeyDown={onKeyDown} onBlur={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) close()
  }}>
    <button ref={triggerRef} id={controlId} type="button" role="combobox" aria-label={label} aria-haspopup="listbox" aria-expanded={expanded} aria-controls={expanded ? listId : undefined} disabled={disabled} className="container-select-trigger" onClick={() => expanded ? close() : show()}>
      <span className={selected ? 'container-select-value' : 'container-select-placeholder'}>{selected?.label || placeholder}</span>
      <ChevronDown size={16} aria-hidden="true" />
    </button>
    {expanded ? <div ref={menuRef} className="container-select-menu" style={menuPosition}>
      <div className="container-select-search">
        <Search size={15} aria-hidden="true" />
        <input ref={searchRef} type="search" role="searchbox" aria-label={`搜索${label}`} aria-controls={listId} aria-activedescendant={activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined} autoComplete="off" placeholder="搜索容器名称或位置" value={query} onChange={(event) => {
          setQuery(event.target.value)
          setActiveValue(null)
        }} />
      </div>
      <ul id={listId} role="listbox" aria-label={`${label}选项`} className="container-select-options">
        {filtered.map((option, index) => <li key={option.value} id={`${listId}-${index}`} role="option" aria-selected={option.value === value} ref={index === activeIndex ? activeOptionRef : undefined} className={index === activeIndex ? 'container-select-option is-active' : 'container-select-option'} onMouseMove={() => setActiveValue(option.value)} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(option.value)}>
          <span className="container-select-option-copy"><span>{option.label}</span>{option.description ? <small>{option.description}</small> : null}</span>
          {option.value === value ? <Check size={16} aria-hidden="true" /> : null}
        </li>)}
      </ul>
      {!filtered.length ? <p className="container-select-empty" role="status">{options.length ? '未找到匹配容器' : '暂无可选容器'}</p> : <p className="container-select-count" role="status">{search ? `找到 ${filtered.length} 个容器` : `共 ${options.length} 个容器`}</p>}
    </div> : null}
  </div>
}
