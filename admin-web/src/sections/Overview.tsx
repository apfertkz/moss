import { useEffect, useState } from 'react'
import { api } from '../api'
import { money, num, days } from '../format'
import { Stat, Spinner, Section, Empty } from '../ui'

type Data = {
  companies: { active: number; pending: number; suspended: number; total: number }
  revenue_kzt: number
  spend_kzt: number
  margin_kzt: number
  sessions: { total: number; week: number; conversion: number }
  attention: {
    expiring: { id: number; title: string; days_left: number }[]
    stuck: { id: number; title: string }[]
    idle: { id: number; title: string; last_session: string | null }[]
  }
}

/**
 * Первый экран. Сверху деньги и объём, снизу три списка, требующие действия.
 * Именно они, а не графики: панель нужна, чтобы что-то сделать, а не смотреть.
 */
export default function Overview({ onOpen }: { onOpen: (id: number) => void }) {
  const [d, setD] = useState<Data | null>(null)

  useEffect(() => { api.get('/api/overview').then(setD).catch(() => {}) }, [])
  if (!d) return <Spinner />

  const lists = [
    { key: 'expiring', title: 'Истекает подписка', tone: '#E2574C',
      items: d.attention.expiring.map((x) => ({ id: x.id, title: x.title, note: days(x.days_left) })) },
    { key: 'stuck', title: 'Застряли на брифе', tone: '#E5B95C',
      items: d.attention.stuck.map((x) => ({ id: x.id, title: x.title, note: 'бриф не пройден' })) },
    { key: 'idle', title: 'Отдел не тренируется', tone: '#E5B95C',
      items: d.attention.idle.map((x) => ({ id: x.id, title: x.title, note: 'неделю тишина' })) },
  ]

  return (
    <div className="p-6">
      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Выручка в месяц" value={money(d.revenue_kzt)}
              hint={`${d.companies.active} активных`} />
        <Stat label="Расход за 30 дней" value={money(d.spend_kzt)} />
        <Stat label="Маржа" value={money(d.margin_kzt)}
              tone={d.margin_kzt >= 0 ? 'ok' : 'bad'} />
        <Stat label="Тренировок за неделю" value={num(d.sessions.week)}
              hint={`конверсия ${d.sessions.conversion}%`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {lists.map((l) => (
          <div key={l.key}>
            <Section title={l.title}>
              {l.items.length === 0 ? (
                <Empty>Пусто — это хорошо</Empty>
              ) : (
                <div className="overflow-hidden rounded-xl border border-line">
                  {l.items.map((it) => (
                    <button
                      key={it.id}
                      onClick={() => onOpen(it.id)}
                      className="flex w-full items-center justify-between gap-3 border-b border-line/60 bg-panel
                                 px-3.5 py-3 text-left last:border-0 hover:bg-white/[0.04]"
                    >
                      <span className="truncate text-[13px]">{it.title}</span>
                      <span className="shrink-0 text-[11px]" style={{ color: l.tone }}>{it.note}</span>
                    </button>
                  ))}
                </div>
              )}
            </Section>
          </div>
        ))}
      </div>

      <div className="mt-2 grid grid-cols-3 gap-3">
        <Stat label="Всего клиентов" value={num(d.companies.total)} />
        <Stat label="На настройке" value={num(d.companies.pending)} />
        <Stat label="Приостановлено" value={num(d.companies.suspended)} />
      </div>
    </div>
  )
}
