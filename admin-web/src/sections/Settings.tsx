import { useEffect, useState } from 'react'
import { api } from '../api'
import { money, dateTime } from '../format'
import { Spinner, Section, Empty, Row, Btn, Input } from '../ui'

/** Ячейка цены: правится прямо в таблице, сохраняется по Enter или уходу фокуса. */
function PriceCell({ planKey, months, value, onSaved }: {
  planKey: string
  months: number
  value: number | undefined
  onSaved: (prices: any) => void
}) {
  const [v, setV] = useState(value ?? '')
  const [busy, setBusy] = useState(false)

  const save = async () => {
    if (String(v) === String(value ?? '')) return
    setBusy(true)
    try {
      const r = await api.post('/api/prices', {
        key: planKey, months, price_kzt: Number(v || 0),
      })
      onSaved(r.prices)
    } finally {
      setBusy(false)
    }
  }

  return (
    <input
      value={v}
      onChange={(e) => setV(e.target.value.replace(/\D/g, ''))}
      onBlur={save}
      onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      placeholder="—"
      className={`w-full rounded-lg border border-transparent bg-transparent px-2 py-1.5
                  text-right text-[13px] hover:border-line focus:border-acc/50 focus:bg-black/30
                  ${busy ? 'opacity-40' : ''}`}
    />
  )
}

/** Строка тарифа: правится на месте, без перехода на отдельный экран. */
function PlanRow({ planKey, plan, onSaved }: {
  planKey: string
  plan: any
  onSaved: (plans: any) => void
}) {
  const [edit, setEdit] = useState(false)
  const [v, setV] = useState(plan)

  const save = async () => {
    const r = await api.post('/api/plans', {
      key: planKey,
      title: v.title,
      price_kzt: Number(v.price_kzt),
      seats: Number(v.seats),
      session_limit: Number(v.session_limit),
    })
    onSaved(r.plans)
    setEdit(false)
  }

  if (edit) {
    return (
      <div className="space-y-2 border-b border-line/60 bg-panel p-4 last:border-0">
        <Input value={v.title} onChange={(e) => setV({ ...v, title: e.target.value })} />
        <div className="grid grid-cols-3 gap-2">
          <Input type="number" value={v.price_kzt}
                 onChange={(e) => setV({ ...v, price_kzt: e.target.value })} />
          <Input type="number" value={v.seats}
                 onChange={(e) => setV({ ...v, seats: e.target.value })} />
          <Input type="number" value={v.session_limit}
                 onChange={(e) => setV({ ...v, session_limit: e.target.value })} />
        </div>
        <div className="flex gap-2">
          <Btn size="sm" tone="accent" onClick={save}>Сохранить</Btn>
          <Btn size="sm" onClick={() => { setV(plan); setEdit(false) }}>Отмена</Btn>
        </div>
      </div>
    )
  }

  return (
    <button onClick={() => setEdit(true)}
      className="flex w-full items-center justify-between gap-4 border-b border-line/60 bg-panel
                 px-4 py-3 text-left last:border-0 hover:bg-white/[0.04]">
      <div>
        <div className="text-[14px]">{plan.title}</div>
        <div className="text-[11px] text-white/35">{planKey}</div>
      </div>
      <div className="flex gap-6 text-right text-[13px]">
        <div><div className="text-[11px] text-white/40">мест</div>{plan.seats}</div>
        <div><div className="text-[11px] text-white/40">тренировок</div>{plan.session_limit}</div>
        <div className="w-28"><div className="text-[11px] text-white/40">цена</div>{money(plan.price_kzt)}</div>
      </div>
    </button>
  )
}

/** Тарифы, курс, журнал действий. */
export default function Settings() {
  const [s, setS] = useState<any>(null)
  const [log, setLog] = useState<any[] | null>(null)
  const [health, setHealth] = useState<any>(null)

  useEffect(() => {
    api.get('/api/settings').then(setS).catch(() => {})
    api.get('/api/log').then(setLog).catch(() => setLog([]))
    api.get('/api/health').then(setHealth).catch(() => {})
  }, [])

  if (!s) return <Spinner />

  return (
    <div className="max-w-3xl p-6">
      <Section title="Тарифы">
        <div className="overflow-hidden rounded-xl border border-line">
          {Object.entries(s.plans).map(([key, p]: [string, any]) => (
            <PlanRow key={key} planKey={key} plan={p}
                     onSaved={(plans) => setS({ ...s, plans })} />
          ))}
        </div>
        <p className="mt-2 text-[12px] text-white/30">
          Изменения применяются сразу. Уже подключённым клиентам места и лимиты
          не пересчитываются — это отдельное решение по каждому.
        </p>
      </Section>

      <Section title="Цены по срокам">
        <div className="overflow-x-auto rounded-xl border border-line">
          <table className="w-full min-w-[520px] text-left text-[13px]">
            <thead>
              <tr className="border-b border-line text-[11px] uppercase tracking-[0.1em] text-white/35">
                <th className="px-4 py-3 font-normal">Тариф</th>
                {(s.terms || [1, 3, 6, 12]).map((m: number) => (
                  <th key={m} className="px-4 py-3 text-right font-normal">
                    {m === 1 ? 'месяц' : m === 12 ? 'год' : `${m} мес`}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(s.plans).map(([key, p]: [string, any]) => (
                <tr key={key} className="border-b border-line/60 bg-panel last:border-0">
                  <td className="px-4 py-2.5">{p.title}</td>
                  {(s.terms || [1, 3, 6, 12]).map((m: number) => (
                    <td key={m} className="px-2 py-2">
                      <PriceCell planKey={key} months={m}
                                 value={(s.prices?.[key] || {})[m]}
                                 onSaved={(prices) => setS({ ...s, prices })} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[12px] text-white/30">
          Скидка за срок: три месяца −10%, полгода −15%, год −25%.
          Пустая ячейка означает, что срок считается по месячной цене без скидки.
        </p>
      </Section>

      <Section title="Выгрузки">
        <div className="grid gap-2 sm:grid-cols-3">
          {[
            { href: '/api/export/companies', title: 'Клиенты' },
            { href: '/api/export/money', title: 'Деньги за 30 дней' },
            { href: '/api/export/sessions', title: 'Тренировки за 90 дней' },
          ].map((x) => (
            <a key={x.href} href={x.href}
               className="rounded-xl border border-line bg-panel px-4 py-3 text-center text-[13px]
                          text-white/70 hover:bg-white/[0.04] hover:text-white">
              {x.title}
            </a>
          ))}
        </div>
      </Section>

      <Section title="Система">
        <div className="rounded-xl border border-line bg-panel px-4 py-1">
          <Row label="База данных">
            {health?.db ? <span className="text-ok">доступна</span> : <span className="text-bad">недоступна</span>}
          </Row>
          <Row label="Курс доллара">{s.usd_kzt} ₸</Row>
          <Row label="Демо-тренировок на гостя">{s.demo_limit}</Row>
        </div>
      </Section>

      <Section title="Журнал действий">
        {!log?.length ? <Empty>Действий пока не было</Empty> : (
          <div className="overflow-hidden rounded-xl border border-line">
            {log.map((l) => (
              <div key={l.id} className="flex items-baseline justify-between gap-4 border-b border-line/60 bg-panel px-4 py-2.5 text-[12px] last:border-0">
                <span className="text-white/70">{l.action}</span>
                <span className="flex-1 truncate text-white/40">{l.company_title || ''}</span>
                <span className="shrink-0 text-white/30">{dateTime(l.created_at)}</span>
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  )
}
