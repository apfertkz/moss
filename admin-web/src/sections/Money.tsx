import { useEffect, useState } from 'react'
import { api } from '../api'
import { money, num } from '../format'
import { Stat, Spinner, Empty, Section } from '../ui'

/** Расход и маржа по клиентам. Здесь видно, не убыточен ли тариф. */
export default function Money({ onOpen }: { onOpen: (id: number) => void }) {
  const [d, setD] = useState<any>(null)
  const [days, setDays] = useState(30)

  useEffect(() => {
    setD(null)
    api.get(`/api/money?days=${days}`).then(setD).catch(() => {})
  }, [days])

  if (!d) return <Spinner />
  const margin = d.total_revenue_kzt - d.total_spend_kzt

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
        <Stat label="Поступило за период" value={money(d.income?.amount_kzt)}
              hint={`${d.income?.count || 0} оплат`} />
        <Stat label="Выручка в месяц" value={money(d.mrr_kzt)}
              hint="длинные сроки поделены" />
        <Stat label="Расход" value={money(d.total_spend_kzt)} />
        <Stat label="Маржа" value={money(margin)} tone={margin >= 0 ? 'ok' : 'bad'} />
      </div>

      <Section title="По клиентам">
        {d.companies.length === 0 ? <Empty>Расхода не было</Empty> : (
          <div className="overflow-x-auto rounded-xl border border-line">
            <table className="w-full min-w-[720px] text-left text-[13px]">
              <thead>
                <tr className="border-b border-line text-[11px] uppercase tracking-[0.1em] text-white/35">
                  <th className="px-4 py-3 font-normal">Клиент</th>
                  <th className="px-4 py-3 font-normal">Тариф</th>
                  <th className="px-4 py-3 font-normal text-right">Тренировок</th>
                  <th className="px-4 py-3 font-normal text-right">Расход</th>
                  <th className="px-4 py-3 font-normal text-right">За тренировку</th>
                  <th className="px-4 py-3 font-normal text-right">Маржа</th>
                </tr>
              </thead>
              <tbody>
                {d.companies.map((c: any) => (
                  <tr key={c.id} onClick={() => onOpen(c.id)}
                      className="cursor-pointer border-b border-line/60 bg-panel last:border-0 hover:bg-white/[0.04]">
                    <td className="px-4 py-3">{c.title}</td>
                    <td className="px-4 py-3 text-white/45">{c.plan}</td>
                    <td className="px-4 py-3 text-right">{num(c.sessions)}</td>
                    <td className="px-4 py-3 text-right">{money(c.spend_kzt)}</td>
                    <td className="px-4 py-3 text-right text-white/60">
                      {c.per_session_kzt === null ? '—' : money(c.per_session_kzt)}
                    </td>
                    <td className={`px-4 py-3 text-right ${c.margin_kzt >= 0 ? 'text-ok' : 'text-bad'}`}>
                      {money(c.margin_kzt)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="По моделям">
        <div className="grid gap-2 sm:grid-cols-2">
          {d.by_model.map((m: any) => (
            <div key={m.model} className="flex items-center justify-between rounded-xl border border-line bg-panel px-4 py-3">
              <span className="truncate text-[13px] text-white/70">{m.model}</span>
              <span className="text-[13px]">{money(m.spend_kzt)}
                <span className="ml-2 text-[11px] text-white/30">{num(m.calls)} вызовов</span>
              </span>
            </div>
          ))}
        </div>
      </Section>
    </div>
  )
}
