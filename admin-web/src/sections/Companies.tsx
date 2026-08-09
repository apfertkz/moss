import { useEffect, useState } from 'react'
import { Plus } from 'lucide-react'
import { api, type Company } from '../api'
import { money, num, days, date, STATUS_TITLES } from '../format'
import { Btn, Input, Select, Spinner, Empty, Tag, HealthTag, Bar } from '../ui'

const FILTERS = [
  { key: 'all', title: 'Все' },
  { key: 'active', title: 'Работают' },
  { key: 'pending_setup', title: 'Настройка' },
  { key: 'suspended', title: 'Приостановлены' },
]

/** Список клиентов. Выбор строки открывает карточку в правой колонке. */
export default function Companies({
  selected, onSelect, reloadKey, onCreated,
}: {
  selected: number | null
  onSelect: (id: number) => void
  reloadKey: number
  onCreated: (id: number) => void
}) {
  const [rows, setRows] = useState<Company[] | null>(null)
  const [status, setStatus] = useState('all')
  const [q, setQ] = useState('')
  const [adding, setAdding] = useState(false)
  const [title, setTitle] = useState('')
  const [plan, setPlan] = useState('trial')
  const [link, setLink] = useState('')

  const load = () => {
    setRows(null)
    const params = new URLSearchParams()
    if (status !== 'all') params.set('status', status)
    if (q) params.set('q', q)
    api.get(`/api/companies?${params}`).then(setRows).catch(() => setRows([]))
  }

  useEffect(() => { load() }, [status, reloadKey])

  const create = async () => {
    if (!title.trim()) return
    const r = await api.post('/api/companies', { title, plan })
    setLink(r.link)
    setTitle('')
    load()
    onCreated(r.id)
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-line p-4">
        <div className="mb-3 flex gap-2">
          <Input placeholder="Поиск по названию или почте" value={q}
                 onChange={(e) => setQ(e.target.value)}
                 onKeyDown={(e) => e.key === 'Enter' && load()} />
          <Btn onClick={() => setAdding(!adding)}><Plus size={14} />Клиент</Btn>
        </div>

        <div className="flex flex-wrap gap-1.5">
          {FILTERS.map((f) => (
            <button key={f.key} onClick={() => setStatus(f.key)}
              className={`rounded-full border px-3 py-1.5 text-[12px] transition-colors ${
                status === f.key
                  ? 'border-acc/50 bg-acc/15 text-white'
                  : 'border-line bg-white/[0.02] text-white/45 hover:text-white'
              }`}>
              {f.title}
            </button>
          ))}
        </div>

        {adding && (
          <div className="mt-3 space-y-2 rounded-xl border border-line bg-panel p-3">
            <Input placeholder="Название компании" value={title}
                   onChange={(e) => setTitle(e.target.value)} />
            <Select value={plan} onChange={(e) => setPlan(e.target.value)}>
              <option value="trial">Пилот — бесплатно</option>
              <option value="start">Старт — 49 000 ₸</option>
              <option value="team">Команда — 99 000 ₸</option>
              <option value="dept">Отдел — 199 000 ₸</option>
            </Select>
            <Btn tone="accent" full onClick={create}>Создать и получить ссылку</Btn>
            {link && (
              <div className="rounded-lg border border-acc/30 bg-acc/10 p-2.5">
                <div className="mb-1 text-[11px] text-white/50">Ссылка для клиента</div>
                <div className="select-all break-all text-[12px] text-acc">{link}</div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto">
        {rows === null ? <Spinner /> : rows.length === 0 ? (
          <div className="p-4"><Empty>Никого не нашлось</Empty></div>
        ) : rows.map((c) => (
          <button key={c.id} onClick={() => onSelect(c.id)}
            className={`w-full border-b border-line/60 px-4 py-3.5 text-left transition-colors ${
              selected === c.id ? 'bg-acc/[0.08]' : 'hover:bg-white/[0.03]'
            }`}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="truncate text-[14px] font-medium">{c.title}</span>
              <span className="shrink-0 text-[12px] text-white/40">{c.plan_title}</span>
            </div>

            <div className="mt-2 flex flex-wrap items-center gap-2">
              <HealthTag health={c.health} />
              {c.status !== 'active' && (
                <Tag color="#9CA3AF">{STATUS_TITLES[c.status] || c.status}</Tag>
              )}
              {c.days_left !== null && c.days_left <= 7 && (
                <Tag color="#E2574C">осталось {days(c.days_left)}</Tag>
              )}
            </div>

            <div className="mt-2.5 flex items-center gap-3 text-[11px] text-white/35">
              <span>{c.seats_taken}/{c.seats} мест</span>
              <span>{num(c.sessions_used)}/{num(c.session_limit)} тренировок</span>
              {c.conversion !== null && <span>конверсия {c.conversion}%</span>}
              <span className="ml-auto">{money(c.spend_kzt)}</span>
            </div>
            <div className="mt-2"><Bar used={c.sessions_used} total={c.session_limit} /></div>
          </button>
        ))}
      </div>
    </div>
  )
}
