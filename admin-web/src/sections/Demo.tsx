import { useEffect, useState } from 'react'
import { api, type Guest } from '../api'
import { dateTime, ago } from '../format'
import { Btn, Input, Spinner, Empty, Tag } from '../ui'

/**
 * Очередь после демо — единственный источник тёплых лидов.
 * Кнопка «выдать пилот» создаёт компанию и сама отправляет человеку ссылку.
 */
export default function Demo() {
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
    <div className="p-6">
      <p className="mb-4 max-w-2xl text-[13px] leading-relaxed text-white/45">
        Люди, прошедшие демо после сайта. Выдать пилот — значит создать компанию
        и отправить человеку ссылку прямо в Telegram.
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
    </div>
  )
}
