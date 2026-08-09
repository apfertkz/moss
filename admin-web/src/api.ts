/**
 * Обращения к панели.
 *
 * Куку ставит сервер, поэтому здесь нет ни токенов, ни заголовков — только
 * credentials, иначе браузер не пошлёт её на запрос из скрипта.
 *
 * Ответ 401 означает, что сессия кончилась. Ловим это в одном месте и
 * сообщаем приложению: иначе каждый экран пришлось бы учить показывать
 * форму входа.
 */

let onUnauthorized: () => void = () => {}

export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

async function request(path: string, options: RequestInit = {}) {
  const res = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  if (res.status === 401) {
    onUnauthorized()
    throw new Error('Требуется вход')
  }

  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data?.error || `Ошибка ${res.status}`)
  return data
}

export const api = {
  get: (path: string) => request(path),
  post: (path: string, body?: unknown) =>
    request(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
}

/* ——— типы ответов ——— */

export type Company = {
  id: number
  title: string
  plan: string
  plan_title: string
  price_kzt: number
  status: string
  seats: number
  seats_taken: number
  session_limit: number
  sessions_used: number
  sessions_total: number
  conversion: number | null
  spend_kzt: number
  expires_at: string | null
  days_left: number | null
  created_at: string
  activation_code: string
  invite_code: string
  contact_email: string | null
  health: 'hot' | 'warm' | 'cold' | 'setup' | 'suspended'
  team?: TeamMember[]
  history?: LogEntry[]
  profile?: { title: string; statuses: number; requests: number; created_at: string } | null
}

export type TeamMember = {
  telegram_id: number
  full_name: string | null
  username: string | null
  role: string
  active: boolean
  total: number
  won: number
  last_seen_at: string | null
}

export type LogEntry = {
  id: number
  actor: string
  action: string
  company_id: number | null
  company_title?: string | null
  telegram_id: number | null
  details: Record<string, unknown> | null
  created_at: string
}

export type User = {
  telegram_id: number
  full_name: string | null
  username: string | null
  role: string
  active: boolean
  company_id: number
  company_title: string
  sessions: number
  conversion: number | null
  last_seen_at: string | null
  joined_at: string | null
}

export type Guest = {
  telegram_id: number
  full_name: string | null
  username: string | null
  sessions: number
  won: number
  joined_at: string | null
  last_at: string | null
}
