import { useEffect, useState } from 'react'
import { RefreshCw, Copy, Check, FileDown, Send } from 'lucide-react'
import { api, type Company } from '../api'
import { money, num, days, date, dateTime, ago, STATUS_TITLES } from '../format'
import { Btn, Select, Row, Section, Spinner, Empty, Tag, HealthTag, Bar } from '../ui'

/** Правая колонка: всё о выбранном клиенте и все действия над ним. */
export default function CompanyCard({
  id, onChanged,
}: {
  id: number
  onChanged: () => void
}) {
  const [c, setC] = useState<Company | null>(null)
  const [plan, setPlan] = useState('')
  const [copied, setCopied] = useState('')
  const [report, setReport] = useState('')
  const [custom, setCustom] = useState(false)
  const [months, setMonths] = useState(3)
  const [amount, setAmount] = useState('')

  const load = () => api.get(`/api/companies/${id}`).then((r) => { setC(r); setPlan(r.plan) })
  useEffect(() => { setC(null); load().catch(() => {}) }, [id])

  const act = async (action: string, body?: unknown) => {
    const r = await api.post(`/api/companies/${id}/${action}`, body)
    setC(r)
    onChanged()
  }

  const copy = (text: string, what: string) => {
    navigator.clipboard?.writeText(text)
    setCopied(what)
    setTimeout(() => setCopied(''), 1500)
  }

  if (!c) return <Spinner />

  const expired = c.days_left !== null && c.days_left <= 0

  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="mb-1 flex items-start justify-between gap-3">
        <h2 className="text-xl font-medium tracking-tight">{c.title}</h2>
        <button onClick={() => load()} className="text-white/30 hover:text-white/70">
          <RefreshCw size={15} />
        </button>
      </div>
      <div className="mb-5 flex flex-wrap gap-2">
        <HealthTag health={c.health} />
        <Tag color="#9CA3AF">{STATUS_TITLES[c.status] || c.status}</Tag>
        <Tag color="#E9A178">{c.plan_title}</Tag>
      </div>

      <Section title="Подписка">
        <div className="rounded-xl border border-line bg-panel px-4 py-1">
          <Row label="Оплачено до">
            <span className={expired ? 'text-bad' : ''}>{date(c.expires_at)}</span>
          </Row>
          <Row label="Осталось">
            <span className={c.days_left !== null && c.days_left <= 7 ? 'text-bad' : ''}>
              {c.days_left === null ? 'бессрочно' : days(c.days_left)}
            </span>
          </Row>
          <Row label="Цена тарифа">{money(c.price_kzt)}</Row>
          <Row label="Подключён">{date(c.created_at)}</Row>
          {c.contact_email && <Row label="Почта">{c.contact_email}</Row>}
        </div>

        {/* Продление сроками: цена подставляется из прайса, но её можно
            поправить — договорённости бывают индивидуальными. */}
        <div className="mt-3 grid grid-cols-2 gap-2">
          {[1, 3, 6, 12].map((m) => {
            const price = (c.prices || {})[m]
            return (
              <Btn key={m} size="sm" tone={m === 3 ? 'accent' : 'ghost'}
                   onClick={() => act('term', { months: m })}>
                <span className="flex flex-col items-center leading-tight">
                  <span>{m === 1 ? 'Месяц' : m === 12 ? 'Год' : `${m} месяца`}</span>
                  {price ? <span className="text-[10px] opacity-60">{money(price)}</span> : null}
                </span>
              </Btn>
            )
          })}
        </div>
        <button onClick={() => setCustom(!custom)}
                className="mt-2 w-full py-1 text-[11px] text-white/30 hover:text-white/60">
          Своя сумма или срок
        </button>
        {custom && (
          <div className="mt-2 space-y-2 rounded-xl border border-line bg-panel p-3">
            <div className="grid grid-cols-2 gap-2">
              <Select value={months} onChange={(e) => setMonths(Number(e.target.value))}>
                {[1, 3, 6, 12].map((m) => <option key={m} value={m}>{m} мес</option>)}
              </Select>
              <Input type="number" placeholder="Сумма, ₸" value={amount}
                     onChange={(e) => setAmount(e.target.value)} />
            </div>
            <Btn size="sm" full tone="accent"
                 onClick={() => act('term', { months, amount_kzt: Number(amount || 0) })}>
              Продлить и записать оплату
            </Btn>
          </div>
        )}
      </Section>

      <Section title="Объём">
        <div className="rounded-xl border border-line bg-panel p-4">
          <div className="mb-1 flex items-baseline justify-between text-[12px]">
            <span className="text-white/40">Тренировки в периоде</span>
            <span>{num(c.sessions_used)} из {num(c.session_limit)}</span>
          </div>
          <Bar used={c.sessions_used} total={c.session_limit} />

          <div className="mb-1 mt-4 flex items-baseline justify-between text-[12px]">
            <span className="text-white/40">Места</span>
            <span>{c.seats_taken} из {c.seats}</span>
          </div>
          <Bar used={c.seats_taken} total={c.seats} tone="#8AA0C8" />

          <div className="mt-4 grid grid-cols-3 gap-3 border-t border-line pt-3 text-center">
            <div>
              <div className="text-lg font-light">{num(c.sessions_total)}</div>
              <div className="text-[11px] text-white/35">всего</div>
            </div>
            <div>
              <div className="text-lg font-light">{c.conversion === null ? '—' : `${c.conversion}%`}</div>
              <div className="text-[11px] text-white/35">конверсия</div>
            </div>
            <div>
              <div className="text-lg font-light">{money(c.spend_kzt)}</div>
              <div className="text-[11px] text-white/35">расход</div>
            </div>
          </div>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2">
          <Btn size="sm" onClick={() => act('sessions', { n: 50 })}>+50 тренировок</Btn>
          <Btn size="sm" onClick={() => act('seats', { n: 5 })}>+5 мест</Btn>
        </div>
      </Section>

      <Section title="Тариф">
        <div className="flex gap-2">
          <Select value={plan} onChange={(e) => setPlan(e.target.value)}>
            <option value="trial">Пилот</option>
            <option value="start">Старт</option>
            <option value="team">Команда</option>
            <option value="dept">Отдел</option>
          </Select>
          <Btn onClick={() => act('plan', { plan })} disabled={plan === c.plan}>Сменить</Btn>
        </div>
      </Section>

      <Section title="Ниша">
        {c.profile ? (
          <div className="rounded-xl border border-line bg-panel px-4 py-1">
            <Row label="Профиль">{c.profile.title}</Row>
            <Row label="Типов клиентов">{c.profile.statuses}</Row>
            <Row label="Запросов">{c.profile.requests}</Row>
            <Row label="Собран">{date(c.profile.created_at)}</Row>
          </div>
        ) : (
          <Empty>Бриф не пройден — клиент не может тренироваться</Empty>
        )}
      </Section>

      <Section title="Отдел">
        {!c.team?.length ? <Empty>Менеджеров пока нет</Empty> : (
          <div className="overflow-hidden rounded-xl border border-line">
            {c.team.map((m) => (
              <div key={m.telegram_id}
                   className="flex items-center justify-between gap-3 border-b border-line/60 bg-panel px-3.5 py-2.5 last:border-0">
                <div className="min-w-0">
                  <div className="truncate text-[13px]">
                    {m.full_name || m.username || m.telegram_id}
                    {m.role === 'owner' && <span className="ml-2 text-[11px] text-acc">руководитель</span>}
                    {!m.active && <span className="ml-2 text-[11px] text-bad">отключён</span>}
                  </div>
                  <div className="text-[11px] text-white/35">
                    {m.total} тренировок · заходил {ago(m.last_seen_at)}
                  </div>
                </div>
                <div className="shrink-0 text-[13px] text-white/60">
                  {m.total ? `${Math.round((m.won / m.total) * 100)}%` : '—'}
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Ссылки">
        <div className="space-y-2">
          {[
            { label: 'Код активации', value: c.activation_code },
            { label: 'Код приглашения', value: c.invite_code },
          ].map((x) => (
            <button key={x.label} onClick={() => copy(x.value, x.label)}
              className="flex w-full items-center justify-between gap-3 rounded-lg border border-line bg-panel px-3.5 py-2.5 text-left hover:bg-white/[0.04]">
              <span className="text-[12px] text-white/40">{x.label}</span>
              <span className="flex items-center gap-2 font-mono text-[13px]">
                {x.value}
                {copied === x.label ? <Check size={13} className="text-ok" /> : <Copy size={13} className="text-white/30" />}
              </span>
            </button>
          ))}
          <Btn size="sm" full onClick={() => act('rotate')}>Перевыпустить приглашение</Btn>
        </div>
      </Section>

      <Section title="Поступления">
        {!c.payments?.length ? <Empty>Оплат ещё не было</Empty> : (
          <div className="overflow-hidden rounded-xl border border-line">
            {c.payments.map((p) => (
              <div key={p.id}
                   className="flex items-baseline justify-between gap-3 border-b border-line/60
                              bg-panel px-3.5 py-2.5 text-[13px] last:border-0">
                <span>{money(p.amount_kzt)}</span>
                <span className="text-[12px] text-white/40">{p.months} мес</span>
                <span className="text-[12px] text-white/30">{date(p.created_at)}</span>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Отчёт для клиента">
        <div className="grid grid-cols-2 gap-2">
          <Btn size="sm" onClick={async () => {
            const r = await api.post(`/api/companies/${id}/report`, {})
            setReport(r.text)
          }}>Показать</Btn>
          <Btn size="sm" tone="accent" onClick={async () => {
            await api.post(`/api/companies/${id}/report`, { send: true })
            setReport('Отчёт отправлен руководителю в Telegram.')
          }}><Send size={13} />Отправить</Btn>
        </div>
        <a href={`/api/export/sessions?company_id=${id}`}
           className="mt-2 flex items-center justify-center gap-1.5 rounded-lg border border-line
                      bg-white/[0.04] px-3 py-2 text-[12px] text-white/70 hover:bg-white/[0.09]">
          <FileDown size={13} />Выгрузить тренировки
        </a>
        {report && (
          <pre className="mt-3 max-h-72 overflow-y-auto whitespace-pre-wrap rounded-xl border
                          border-line bg-black/30 p-3 text-[12px] leading-relaxed text-white/70">
            {report}
          </pre>
        )}
      </Section>

      <Section title="Опасная зона">
        <div className="grid grid-cols-2 gap-2">
          {c.status === 'suspended' ? (
            <Btn size="sm" tone="accent" onClick={() => act('resume')}>Возобновить</Btn>
          ) : (
            <Btn size="sm" tone="danger" onClick={() => act('suspend')}>Приостановить</Btn>
          )}
          <Btn size="sm" tone="danger" onClick={() => act('reset')}>Обнулить период</Btn>
        </div>
      </Section>

      <Section title="История">
        {!c.history?.length ? <Empty>Изменений не было</Empty> : (
          <div className="space-y-1.5">
            {c.history.map((h) => (
              <div key={h.id} className="flex items-baseline justify-between gap-3 text-[12px]">
                <span className="text-white/60">{h.action}</span>
                <span className="shrink-0 text-white/30">{dateTime(h.created_at)}</span>
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  )
}
