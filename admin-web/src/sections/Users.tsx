import { useEffect, useState } from 'react'
import { api, type User } from '../api'
import { ago, num } from '../format'
import { Btn, Input, Spinner, Empty, Tag } from '../ui'

/** Пользователи всех компаний: поиск, отключение, роль, разбор тренировки. */
export default function Users({ onOpenCompany }: { onOpenCompany: (id: number) => void }) {
  const [rows, setRows] = useState<User[] | null>(null)
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const [session, setSession] = useState<any>(null)

  const load = () => {
    setRows(null)
    api.get(`/api/users?q=${encodeURIComponent(q)}`).then(setRows).catch(() => setRows([]))
  }
  useEffect(() => { load() }, [])

  const act = async (tg: number, action: string, body?: unknown) => {
    await api.post(`/api/users/${tg}/${action}`, body)
    load()
  }

  const showSession = async (tg: number) => {
    if (open === tg) { setOpen(null); return }
    setOpen(tg)
    setSession(null)
    setSession(await api.get(`/api/users/${tg}/session`))
  }

  return (
    <div className="p-6">
      <div className="mb-4 flex gap-2">
        <Input placeholder="Имя, юзернейм или id" value={q}
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => e.key === 'Enter' && load()} />
        <Btn onClick={load}>Найти</Btn>
      </div>

      {rows === null ? <Spinner /> : rows.length === 0 ? <Empty>Никого не нашлось</Empty> : (
        <div className="overflow-hidden rounded-xl border border-line">
          {rows.map((u) => (
            <div key={u.telegram_id} className="border-b border-line/60 bg-panel last:border-0">
              <div className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[14px]">
                      {u.full_name || u.username || u.telegram_id}
                    </span>
                    {u.role === 'owner' && <Tag color="#E9A178">руководитель</Tag>}
                    {!u.active && <Tag color="#E2574C">отключён</Tag>}
                  </div>
                  <button onClick={() => onOpenCompany(u.company_id)}
                          className="text-[12px] text-white/40 hover:text-acc">
                    {u.company_title}
                  </button>
                </div>

                <div className="text-right text-[12px] text-white/40">
                  <div>{num(u.sessions)} тренировок{u.conversion !== null ? ` · ${u.conversion}%` : ''}</div>
                  <div>заходил {ago(u.last_seen_at)}</div>
                </div>

                <div className="flex gap-1.5">
                  <Btn size="sm" onClick={() => showSession(u.telegram_id)}>Разбор</Btn>
                  {u.active
                    ? <Btn size="sm" tone="danger" onClick={() => act(u.telegram_id, 'disable')}>Отключить</Btn>
                    : <Btn size="sm" onClick={() => act(u.telegram_id, 'enable')}>Включить</Btn>}
                  <Btn size="sm" onClick={() => act(u.telegram_id, 'role',
                        { role: u.role === 'owner' ? 'manager' : 'owner' })}>
                    {u.role === 'owner' ? 'В менеджеры' : 'В руководители'}
                  </Btn>
                </div>
              </div>

              {open === u.telegram_id && (
                <div className="border-t border-line bg-black/20 px-4 py-3">
                  {!session ? <Spinner /> : !session.session ? (
                    <div className="text-[13px] text-white/35">Тренировок ещё не было</div>
                  ) : (
                    <>
                      <div className="mb-3 flex flex-wrap gap-2 text-[12px] text-white/40">
                        <span>{session.session.status_title}</span>
                        <span>·</span>
                        <span>{session.session.psychotype_id}</span>
                        <span>·</span>
                        <span className={session.session.result === 'won' ? 'text-ok' : 'text-bad'}>
                          {session.session.result === 'won' ? 'сделка закрыта' : 'провалена'}
                        </span>
                      </div>
                      <div className="space-y-1.5">
                        {session.messages.map((m: any, i: number) => (
                          <div key={i} className={m.role === 'manager' ? 'text-right' : ''}>
                            <span className={`inline-block max-w-[80%] rounded-xl px-3 py-1.5 text-[13px] ${
                              m.role === 'manager' ? 'bg-acc/20 text-[#F6DFCE]'
                                : m.role === 'system' ? 'bg-transparent text-white/30 italic'
                                : 'bg-white/[0.07] text-white/85'}`}>
                              {m.text}
                            </span>
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
      )}
    </div>
  )
}
