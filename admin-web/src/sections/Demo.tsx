import { useEffect, useState } from 'react'
import { api, type Guest } from '../api'
import { dateTime, ago } from '../format'
import { Btn, Input, Spinner, Empty, Tag, Stat, Section } from '../ui'
import { money } from '../format'

/**
 * Два источника тёплых лидов рядом: демо в боте и демо на сайте.
 *
 * Вместе, а не в разных разделах: это одна воронка, и сравнивать их нужно
 * глазами. Заявка без контакта тоже показана — по ней видно нишу и то, на
 * каком шаге человек ушёл.
 */
export default function Demo() {
  return (
    <div className="p-4 sm:p-6">
      <SiteLeads />
      <BotQueue />
    </div>
  )
}

function SiteLeads() {
  const [d, setD] = useState<any>(null)
  const [open, setOpen] = useState<number | null>(null)

  useEffect(() => { api.get('/api/leads').then(setD).catch(() => setD({ leads: [] })) }, [])
  if (!d) return <Spinner />

  const st = d.stats || {}
  return (
    <Section title="Демо на сайте">
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Запусков за месяц" value={st.started ?? 0} />
        <Stat label="Дошли до разбора" value={`${st.to_end ?? 0}%`}
              hint={`${st.finished ?? 0} из ${st.started ?? 0}`} />
        <Stat label="Оставили контакт" value={st.leads ?? 0} />
        <Stat label="Потрачено сегодня" value={money(st.today_kzt)}
              hint={`потолок ${Math.round((d.daily_limit_usd || 0) * 540).toLocaleString('ru-RU')} ₸`} />
      </div>

      {!d.leads?.length ? <Empty>Демо на сайте ещё не запускали</Empty> : (
        <div className="space-y-2">
          {d.leads.map((l: any) => (
            <div key={l.id} className="rounded-xl border border-line bg-panel">
              <button onClick={() => setOpen(open === l.id ? null : l.id)}
                      className="flex w-full items-baseline gap-3 px-4 py-3 text-left">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px]">
                    {l.contact ? `${l.name || 'Без имени'} · ${l.contact}` : 'Без контакта'}
                  </div>
                  <div className="truncate text-[11px] text-white/35">
                    {l.niche || 'ниша не определилась'} · {l.turns} реплик · {ago(l.created_at)}
                  </div>
                </div>
                <span className="shrink-0 text-[11px] text-white/30">{money(l.cost_kzt)}</span>
                {l.contact && <Tag color="#5FBF7F">лид</Tag>}
              </button>
              {open === l.id && l.verdict && (
                <pre className="whitespace-pre-wrap border-t border-line px-4 py-3
                                text-[12.5px] leading-relaxed text-white/70">{l.verdict}</pre>
              )}
            </div>
          ))}
        </div>
      )}
    </Section>
  )
}

function BotQueue() {
  const [rows, setRows] = useState<Guest[] | null>(null)
  const [title, setTitle] = useState<Record<number, string>>({})
  const [done, setDone] = useState<Record<number, string>>({})

  const load = () => api.get('/api/demo').then(setRows).catch(() => setRows([]))

  // Обёртка обязательна: useEffect считает возвращённое значение функцией
  // уборки, а load возвращает промис — при уходе с раздела React пытался бы
  // его вызвать и валил всё приложение.
  useEffect(() => { load() }, [])

  const grant = async (tg: number) => {
    const r = await api.post(`/api/demo/${tg}/grant`, { title: title[tg] || '' })
    setDone((d) => ({ ...d, [tg]: r.link }))
    load()
  }

  if (rows === null) return <Spinner />

  return (
    <Section title="Демо в боте">
      <p className="mb-4 max-w-2xl text-[13px] leading-relaxed text-white/45">
        Гости, прошедшие тренировку в самом боте. Выдать пилот — значит создать
        компанию и отправить человеку ссылку прямо в Telegram.
      </p>

      {rows.length === 0 ? <Empty>Гостей пока не было</Empty> : (
        <div className="space-y-2">
          {rows.map((g) => (
            <div key={g.telegram_id} className="rounded-xl border border-line bg-panel p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-[14px]">
                    {g.full_name || g.username || g.telegram_id}
                    {g.username && <span className="ml-2 text-[12px] text-white/35">@{g.username}</span>}
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-[12px] text-white/40">
                    <span>{g.sessions} тренировок</span>
                    {g.won > 0 && <Tag color="#5FBF7F">{g.won} закрыл</Tag>}
                    <span>· пришёл {ago(g.joined_at)}</span>
                  </div>
                </div>

                <div className="flex gap-2">
                  <Input placeholder="Название компании" className="w-52"
                         value={title[g.telegram_id] || ''}
                         onChange={(e) => setTitle({ ...title, [g.telegram_id]: e.target.value })} />
                  <Btn tone="accent" onClick={() => grant(g.telegram_id)}>Выдать пилот</Btn>
                </div>
              </div>

              {done[g.telegram_id] && (
                <div className="mt-3 rounded-lg border border-ok/30 bg-ok/10 p-2.5 text-[12px]">
                  Ссылка отправлена: <span className="select-all text-ok">{done[g.telegram_id]}</span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Section>
  )
}
