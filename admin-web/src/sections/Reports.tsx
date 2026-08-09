import { useEffect, useState } from 'react'
import { api } from '../api'
import { num } from '../format'
import { Stat, Spinner, Empty, Section } from '../ui'

/** Сводка за период и разбивка по психотипам — где отделы ломаются. */
export default function Reports() {
  const [d, setD] = useState<any>(null)
  const [types, setTypes] = useState<any[] | null>(null)
  const [days, setDays] = useState(30)

  useEffect(() => {
    setD(null)
    api.get(`/api/summary?days=${days}`).then(setD).catch(() => {})
    api.get('/api/psychotypes').then(setTypes).catch(() => setTypes([]))
  }, [days])

  if (!d) return <Spinner />

  const max = Math.max(1, ...d.daily.map((x: any) => x.n))

  return (
    <div className="p-6">
      <div className="mb-4 flex gap-1.5">
        {[7, 30, 90].map((n) => (
          <button key={n} onClick={() => setDays(n)}
            className={`rounded-full border px-3 py-1.5 text-[12px] ${
              days === n ? 'border-acc/50 bg-acc/15 text-white'
                         : 'border-line bg-white/[0.02] text-white/45 hover:text-white'}`}>
            {n} дней
          </button>
        ))}
      </div>

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Новых клиентов" value={num(d.new_companies)} />
        <Stat label="Тренировок" value={num(d.sessions)} />
        <Stat label="Людей тренировалось" value={num(d.people)} />
        <Stat label="Конверсия" value={`${d.conversion}%`} />
      </div>

      <Section title="Тренировки по дням">
        {d.daily.length === 0 ? <Empty>Данных пока нет</Empty> : (
          <div className="rounded-xl border border-line bg-panel p-4">
            <div className="flex h-40 items-end gap-1">
              {d.daily.map((x: any, i: number) => (
                <div key={i} className="group relative flex-1" title={`${x.n} тренировок, ${x.won} закрыто`}>
                  <div className="w-full rounded-t bg-white/10" style={{ height: `${(x.n / max) * 140}px` }}>
                    <div className="w-full rounded-t bg-acc"
                         style={{ height: `${(x.won / Math.max(1, x.n)) * 100}%` }} />
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-3 flex gap-4 text-[11px] text-white/35">
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-sm bg-acc" />закрытые
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-sm bg-white/10" />всего
              </span>
            </div>
          </div>
        )}
      </Section>

      <Section title="По типам клиентов">
        {!types?.length ? <Empty>Данных пока нет</Empty> : (
          <div className="space-y-2">
            {types.map((t, i) => (
              <div key={i} className="flex items-center gap-4 rounded-xl border border-line bg-panel px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px]">{t.status || '—'}</div>
                  <div className="text-[11px] text-white/35">{t.psychotype}</div>
                </div>
                <div className="w-32">
                  <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
                    <div className="h-full rounded-full"
                         style={{ width: `${t.conversion}%`,
                                  background: t.conversion >= 50 ? '#5FBF7F' : t.conversion >= 25 ? '#E5B95C' : '#E2574C' }} />
                  </div>
                </div>
                <div className="w-24 text-right text-[13px]">
                  {t.conversion}% <span className="text-[11px] text-white/30">из {t.n}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  )
}
