import { useEffect, useState } from 'react'
import { api } from '../api'
import { money, dateTime } from '../format'
import { Spinner, Section, Empty, Row } from '../ui'

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
            <div key={key} className="flex items-center justify-between gap-4 border-b border-line/60 bg-panel px-4 py-3 last:border-0">
              <div>
                <div className="text-[14px]">{p.title}</div>
                <div className="text-[11px] text-white/35">{key}</div>
              </div>
              <div className="flex gap-6 text-right text-[13px]">
                <div><div className="text-white/40 text-[11px]">мест</div>{p.seats}</div>
                <div><div className="text-white/40 text-[11px]">тренировок</div>{p.session_limit}</div>
                <div className="w-28"><div className="text-white/40 text-[11px]">цена</div>{money(p.price_kzt)}</div>
              </div>
            </div>
          ))}
        </div>
        <p className="mt-2 text-[12px] text-white/30">
          Пока правятся в коде. Перенос в базу — в следующем этапе.
        </p>
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
