import { useEffect, useState } from 'react'
import {
  LayoutDashboard, Users as UsersIcon, BarChart3, Send,
  CreditCard, LogOut, Sparkles, Copy, RefreshCw, Download,
} from 'lucide-react'
import { api } from '../api'
import { num, ago, days as plural, date } from '../format'
import { Btn, Input, Stat, Section, Empty, Spinner, Row, Bar } from '../ui'
import ChangePassword from './ChangePassword'
import { Mark } from '../Logo'

/**
 * Кабинет руководителя.
 *
 * Тот же самый интерфейс, что у владельца продукта, но разделов меньше и
 * все данные приходят с адресов /api/my/*, где компания берётся из куки.
 * Прятать лишнее только в меню было бы обманом: кнопки нет, а данные
 * по-прежнему отдаются тому, кто наберёт адрес руками.
 */

const NAV = [
  { key: 'dash', title: 'Отдел', short: 'Отдел', icon: LayoutDashboard },
  { key: 'team', title: 'Менеджеры', short: 'Люди', icon: UsersIcon },
  { key: 'reports', title: 'Отчёты', short: 'Отчёты', icon: BarChart3 },
  { key: 'broadcast', title: 'Сообщение отделу', short: 'Письмо', icon: Send },
  { key: 'plan', title: 'Подписка', short: 'Тариф', icon: CreditCard },
]

type Me = {
  telegram_id: number
  must_change: boolean
  company: any
  segments: Record<string, string>
}

export default function OwnerApp({ onLogout }: { onLogout: () => void }) {
  const [me, setMe] = useState<Me | null>(null)
  const [tab, setTab] = useState('dash')

  const load = () => api.get('/api/my/me').then(setMe).catch(() => {})
  useEffect(() => { load() }, [])

  if (!me) return <div className="min-h-screen bg-ink" />
  if (me.must_change) return <ChangePassword onDone={load} />

  const centre = () => {
    switch (tab) {
      case 'dash': return <Dash me={me} />
      case 'team': return <Team />
      case 'reports': return <OwnerReports />
      case 'broadcast': return <OwnerBroadcast segments={me.segments} />
      case 'plan': return <Plan me={me} />
      default: return null
    }
  }

  return (
    <div className="flex h-screen overflow-hidden bg-ink">
      <nav className="hidden w-[68px] shrink-0 flex-col items-center border-r border-line py-4 lg:flex lg:w-56 lg:items-stretch lg:px-3">
        <div className="mb-6 flex items-center gap-2.5 px-1 lg:px-2">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-acc/15 text-acc">
            <Mark className="h-[17px] w-auto" />
          </span>
          <span className="hidden min-w-0 lg:block">
            <span className="block truncate text-[14px] font-medium tracking-tight">
              {me.company?.title || 'aisaty'}
            </span>
            <span className="block text-[11px] text-white/35">кабинет руководителя</span>
          </span>
        </div>

        <div className="flex flex-1 flex-col gap-1">
          {NAV.map((n) => {
            const Icon = n.icon
            const on = tab === n.key
            return (
              <button key={n.key} onClick={() => setTab(n.key)} title={n.title}
                className={`flex items-center gap-3 rounded-lg px-2.5 py-2.5 text-left text-[13px] transition-colors ${
                  on ? 'bg-acc/15 text-white' : 'text-white/45 hover:bg-white/[0.04] hover:text-white'
                }`}>
                <Icon size={17} className={on ? 'text-acc' : ''} />
                <span className="hidden lg:block">{n.title}</span>
              </button>
            )
          })}
        </div>

        <button
          onClick={async () => { await api.post('/api/logout'); onLogout() }}
          className="flex items-center gap-3 rounded-lg px-2.5 py-2.5 text-[13px] text-white/35 hover:text-white">
          <LogOut size={17} />
          <span className="hidden lg:block">Выйти</span>
        </button>
      </nav>

      <main className="flex-1 overflow-y-auto pb-24 lg:pb-0">
        {/* Шапка на телефоне: бокового рельса там нет, а понимать,
            чей это кабинет, всё равно нужно. */}
        <header className="sticky top-0 z-30 flex items-center gap-2.5 border-b border-line
                           bg-ink/90 px-4 py-3 backdrop-blur lg:hidden">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-acc/15 text-acc">
            <Mark className="h-[17px] w-auto" />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-[14px] font-medium tracking-tight">
              {me.company?.title || 'aisaty'}
            </span>
            <span className="block text-[11px] text-white/35">кабинет руководителя</span>
          </span>
          <button
            onClick={async () => { await api.post('/api/logout'); onLogout() }}
            className="ml-auto shrink-0 p-2 text-white/35">
            <LogOut size={17} />
          </button>
        </header>
        {centre()}
      </main>

      {/* Нижняя навигация: на 390 пикселях боковой рельс съедал бы шестую
          часть ширины, а до неё ещё надо дотянуться большим пальцем. */}
      <nav className="fixed inset-x-0 bottom-0 z-40 flex border-t border-line
                      bg-ink/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
        {NAV.map((n) => {
          const Icon = n.icon
          const on = tab === n.key
          return (
            <button key={n.key} onClick={() => setTab(n.key)}
              className={`flex flex-1 flex-col items-center gap-1 py-2.5 text-[10px] leading-tight
                ${on ? 'text-acc' : 'text-white/40'}`}>
              <Icon size={19} />
              <span className="max-w-full truncate px-0.5">{n.short}</span>
            </button>
          )
        })}
      </nav>
    </div>
  )
}

/* ——— Отдел ——— */

function Dash({ me }: { me: Me }) {
  const [d, setD] = useState<any>(null)
  const [invite, setInvite] = useState('')
  const c = me.company

  useEffect(() => {
    api.get('/api/my/summary?days=30').then(setD).catch(() => {})
    api.get('/api/my/invite').then((r) => setInvite(r.link)).catch(() => {})
  }, [])

  return (
    <div className="p-4 sm:p-6">
      {/* На телефоне название уже стоит в шапке — второй раз не повторяем. */}
      <h2 className="mb-5 hidden text-[22px] font-light tracking-tight lg:block">{c?.title}</h2>

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Тренировок за месяц" value={num(d?.sessions)} />
        <Stat label="Конверсия отдела" value={d ? `${d.conversion}%` : '—'} />
        <Stat label="Тренировались" value={d ? `${d.people} из ${c?.seats_taken}` : '—'} />
        <Stat label="Осталось дней"
              value={c?.days_left === null ? '∞' : num(c?.days_left)}
              tone={c?.days_left !== null && c?.days_left <= 7 ? 'bad' : undefined} />
      </div>

      <Section title="Кто как тренируется">
        {!d?.team?.length ? <Empty>За месяц тренировок не было</Empty> : (
          <div className="space-y-2">
            {d.team.map((t: any) => (
              <div key={t.telegram_id}
                   className="rounded-xl border border-line bg-panel px-4 py-3
                              sm:flex sm:items-center sm:gap-4">
                <div className="flex items-baseline justify-between gap-3 sm:block sm:min-w-0 sm:flex-1">
                  <div className="min-w-0">
                    <div className="truncate text-[13px]">{t.full_name || t.telegram_id}</div>
                    <div className="truncate text-[11px] text-white/35">
                      {t.username ? '@' + t.username : t.telegram_id}
                    </div>
                  </div>
                  {/* На телефоне процент стоит рядом с именем, иначе строка
                      растягивается на три уровня и список перестаёт читаться. */}
                  <div className="shrink-0 text-right text-[13px] sm:hidden">
                    {t.conversion}% <span className="text-[11px] text-white/30">из {t.sessions}</span>
                  </div>
                </div>
                <div className="mt-2.5 sm:mt-0 sm:w-28">
                  <Bar used={t.conversion} total={100}
                       tone={t.conversion >= 50 ? '#5FBF7F' : t.conversion >= 25 ? '#E5B95C' : '#E2574C'} />
                </div>
                <div className="hidden w-28 text-right text-[13px] sm:block">
                  {t.conversion}% <span className="text-[11px] text-white/30">из {t.sessions}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {invite && (
        <Section title="Пригласить менеджера">
          <div className="rounded-xl border border-line bg-panel p-4">
            <div className="mb-3 text-[12px] leading-relaxed text-white/45">
              Отправьте ссылку сотруднику. Он перейдёт в бота и сразу окажется
              в вашем отделе — вводить ничего не нужно.
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded-lg border border-line bg-black/30 px-3 py-2 text-[12px] text-white/70">
                {invite}
              </code>
              <Btn size="sm" onClick={() => navigator.clipboard?.writeText(invite)}>
                <Copy size={13} /> Копировать
              </Btn>
              <Btn size="sm" onClick={async () => {
                const r = await api.post('/api/my/invite'); setInvite(r.link)
              }}>
                <RefreshCw size={13} /> Отозвать старую
              </Btn>
            </div>
          </div>
        </Section>
      )}
    </div>
  )
}

/* ——— Менеджеры ——— */

function Team() {
  const [rows, setRows] = useState<any[] | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const [session, setSession] = useState<any>(null)

  useEffect(() => { api.get('/api/my/team').then(setRows).catch(() => setRows([])) }, [])

  const show = async (tg: number) => {
    setOpen(tg); setSession(null)
    setSession(await api.get(`/api/my/team/${tg}/session`).catch(() => null))
  }

  if (!rows) return <Spinner />
  if (!rows.length) return <div className="p-4 sm:p-6"><Empty>В отделе пока никого нет</Empty></div>

  return (
    <div className="p-4 sm:p-6">
      <div className="space-y-2">
        {rows.map((u) => (
          <div key={u.telegram_id} className="rounded-xl border border-line bg-panel">
            <button onClick={() => (open === u.telegram_id ? setOpen(null) : show(u.telegram_id))}
                    className="flex w-full items-center gap-4 px-4 py-3 text-left">
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px]">{u.full_name || u.telegram_id}</div>
                <div className="text-[11px] text-white/35">
                  {u.role === 'owner' ? 'Руководитель' : 'Менеджер'} · был {ago(u.last_seen_at)}
                </div>
              </div>
              <div className="text-right text-[13px]">
                {u.conversion === null ? '—' : `${u.conversion}%`}
                <span className="ml-1 text-[11px] text-white/30">из {u.sessions}</span>
              </div>
            </button>

            {open === u.telegram_id && (
              <div className="border-t border-line px-4 py-3">
                {!session ? <Spinner /> : !session.session ? (
                  <div className="text-[12px] text-white/35">Тренировок ещё не было</div>
                ) : (
                  <>
                    <div className="mb-2 text-[11px] uppercase tracking-[0.12em] text-white/35">
                      Последняя тренировка · {date(session.session.finished_at)}
                    </div>
                    <div className="max-h-72 space-y-2 overflow-y-auto">
                      {(session.messages || []).map((m: any, i: number) => (
                        <div key={i} className={`max-w-[85%] rounded-2xl px-3 py-2 text-[12px] leading-relaxed ${
                          m.role === 'user'
                            ? 'ml-auto bg-acc/15 text-white'
                            : 'bg-white/[0.05] text-white/75'}`}>
                          {m.content}
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

/* ——— Отчёты ——— */

function OwnerReports() {
  const [d, setD] = useState<any>(null)
  const [types, setTypes] = useState<any[] | null>(null)
  const [report, setReport] = useState('')
  const [days, setDays] = useState(30)

  useEffect(() => {
    setD(null)
    api.get(`/api/my/summary?days=${days}`).then(setD).catch(() => {})
  }, [days])

  useEffect(() => {
    api.get('/api/my/psychotypes').then(setTypes).catch(() => setTypes([]))
    api.get('/api/my/report').then((r) => setReport(r.text)).catch(() => {})
  }, [])

  if (!d) return <Spinner />
  const max = Math.max(1, ...d.daily.map((x: any) => x.n))

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        {[7, 30, 90].map((n) => (
          <button key={n} onClick={() => setDays(n)}
            className={`rounded-full border px-3 py-1.5 text-[12px] ${
              days === n ? 'border-acc/50 bg-acc/15 text-white'
                         : 'border-line bg-white/[0.02] text-white/45 hover:text-white'}`}>
            {n} дней
          </button>
        ))}
        <a href="/api/my/export" download
           className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-line
                      bg-white/[0.04] px-3 py-1.5 text-[12px] text-white/70 hover:text-white">
          <Download size={13} /> Выгрузить тренировки
        </a>
      </div>

      <Section title="Тренировки по дням">
        {d.daily.length === 0 ? <Empty>Данных пока нет</Empty> : (
          <div className="rounded-xl border border-line bg-panel p-4">
            <div className="flex h-40 items-end gap-1">
              {d.daily.map((x: any, i: number) => (
                <div key={i} className="flex-1" title={`${x.n} тренировок, ${x.won} закрыто`}>
                  <div className="w-full rounded-t bg-white/10" style={{ height: `${(x.n / max) * 140}px` }}>
                    <div className="w-full rounded-t bg-acc"
                         style={{ height: `${(x.won / Math.max(1, x.n)) * 100}%` }} />
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-3 flex gap-4 text-[11px] text-white/35">
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-acc" />закрытые</span>
              <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-white/10" />всего</span>
            </div>
          </div>
        )}
      </Section>

      <Section title="С какими клиентами отдел справляется хуже">
        {!types?.length ? <Empty>Данных пока нет</Empty> : (
          <div className="space-y-2">
            {types.map((t, i) => (
              <div key={i} className="rounded-xl border border-line bg-panel px-4 py-3
                                       sm:flex sm:items-center sm:gap-4">
                <div className="flex items-baseline justify-between gap-3 sm:block sm:min-w-0 sm:flex-1">
                  <div className="min-w-0">
                    <div className="truncate text-[13px]">{t.status || '—'}</div>
                    <div className="truncate text-[11px] text-white/35">{t.psychotype}</div>
                  </div>
                  <div className="shrink-0 text-right text-[13px] sm:hidden">
                    {t.conversion}% <span className="text-[11px] text-white/30">из {t.n}</span>
                  </div>
                </div>
                <div className="mt-2.5 sm:mt-0 sm:w-32">
                  <Bar used={t.conversion} total={100}
                       tone={t.conversion >= 50 ? '#5FBF7F' : t.conversion >= 25 ? '#E5B95C' : '#E2574C'} />
                </div>
                <div className="hidden w-24 text-right text-[13px] sm:block">
                  {t.conversion}% <span className="text-[11px] text-white/30">из {t.n}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {report && (
        <Section title="Сводка словами">
          <pre className="whitespace-pre-wrap rounded-xl border border-line bg-panel p-4
                          text-[13px] leading-relaxed text-white/75">{report}</pre>
        </Section>
      )}
    </div>
  )
}

/* ——— Сообщение отделу ——— */

function OwnerBroadcast({ segments }: { segments: Record<string, string> }) {
  const [segment, setSegment] = useState('all')
  const [text, setText] = useState('')
  const [count, setCount] = useState<number | null>(null)
  const [result, setResult] = useState<any>(null)

  useEffect(() => {
    setCount(null)
    api.post('/api/my/broadcast/preview', { segment })
      .then((r) => setCount(r.count)).catch(() => {})
  }, [segment])

  return (
    <div className="max-w-2xl p-4 sm:p-6">
      <Section title="Кому">
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(segments).map(([k, v]) => (
            <button key={k} onClick={() => setSegment(k)}
              className={`rounded-full border px-3 py-1.5 text-[12px] ${
                segment === k ? 'border-acc/50 bg-acc/15 text-white'
                              : 'border-line bg-white/[0.02] text-white/45 hover:text-white'}`}>
              {v}
            </button>
          ))}
        </div>
        <div className="mt-2 text-[12px] text-white/40">
          {count === null ? 'считаем…' : `получателей: ${count}`}
        </div>
      </Section>

      <Section title="Текст">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={7}
          placeholder="Сообщение придёт вашим сотрудникам в Telegram от имени бота"
          className="w-full rounded-xl border border-line bg-black/30 px-3.5 py-3 text-[13px]
                     leading-relaxed text-white placeholder:text-white/25 focus:border-acc/50"
        />
        <div className="mt-3 flex flex-wrap gap-2">
          <Btn size="sm" onClick={async () => setResult(
            await api.post('/api/my/broadcast/send', { text, test: true }))}>
            Сначала себе
          </Btn>
          <Btn tone="accent" size="sm" disabled={!text.trim() || !count}
               onClick={async () => setResult(
                 await api.post('/api/my/broadcast/send', { text, segment }))}>
            Отправить {count ? `(${count})` : ''}
          </Btn>
        </div>
        {result && (
          <div className="mt-3 text-[13px] text-white/60">
            Доставлено {result.sent} из {result.total}
            {result.failed ? `, не дошло ${result.failed}` : ''}
          </div>
        )}
      </Section>
    </div>
  )
}

/* ——— Подписка ——— */

function Plan({ me }: { me: Me }) {
  const c = me.company
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [msg, setMsg] = useState('')

  if (!c) return <Spinner />

  return (
    <div className="max-w-2xl p-4 sm:p-6">
      <Section title="Подписка">
        <div className="rounded-xl border border-line bg-panel px-4 py-1">
          <Row label="Тариф">{c.plan_title}</Row>
          <Row label="Мест">{c.seats_taken} из {c.seats}</Row>
          <Row label="Тренировок в месяце">{num(c.sessions_used)} из {num(c.session_limit)}</Row>
          <Row label="Действует до">
            {c.expires_at ? date(c.expires_at) : 'бессрочно'}
            {c.days_left !== null && (
              <span className="ml-2 text-white/35">осталось {plural(c.days_left)}</span>
            )}
          </Row>
        </div>
        <div className="mt-3 rounded-xl border border-line bg-panel p-4">
          <Bar used={c.sessions_used} total={c.session_limit} />
          <div className="mt-2 text-[12px] text-white/40">
            Продлить или расширить отдел — напишите нам в Telegram, включим в тот же день.
          </div>
        </div>
      </Section>

      <Section title="Пароль от кабинета">
        <div className="space-y-2 rounded-xl border border-line bg-panel p-4">
          <Input type="password" placeholder="Новый пароль" value={password}
                 onChange={(e) => setPassword(e.target.value)} />
          <Input type="password" placeholder="Ещё раз" value={repeat}
                 onChange={(e) => setRepeat(e.target.value)} />
          <Btn size="sm" disabled={password.length < 8 || password !== repeat}
               onClick={async () => {
                 setMsg('')
                 try {
                   await api.post('/api/my/password', { password, repeat })
                   setPassword(''); setRepeat(''); setMsg('Пароль изменён')
                 } catch (e) { setMsg((e as Error).message) }
               }}>
            Сменить
          </Btn>
          {msg && <div className="text-[12px] text-white/50">{msg}</div>}
          <div className="text-[12px] leading-relaxed text-white/35">
            Логин — ваш Telegram ID: <code className="text-white/60">{me.telegram_id}</code>
          </div>
        </div>
      </Section>
    </div>
  )
}
