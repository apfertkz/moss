/** Форматирование: деньги, даты, склонения. */

export const money = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : Math.round(n).toLocaleString('ru-RU') + ' ₸'

export const num = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : Math.round(n).toLocaleString('ru-RU')

export function date(v: string | null | undefined) {
  if (!v) return '—'
  const d = new Date(v)
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: 'short', year: '2-digit' })
}

export function dateTime(v: string | null | undefined) {
  if (!v) return '—'
  const d = new Date(v)
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

/** «3 дня», «1 день», «5 дней» — иначе интерфейс читается как машинный. */
export function days(n: number | null | undefined) {
  if (n === null || n === undefined) return 'бессрочно'
  const a = Math.abs(n) % 100
  const b = a % 10
  if (a > 10 && a < 20) return `${n} дней`
  if (b === 1) return `${n} день`
  if (b > 1 && b < 5) return `${n} дня`
  return `${n} дней`
}

export function ago(v: string | null | undefined) {
  if (!v) return 'никогда'
  const diff = (Date.now() - new Date(v).getTime()) / 86400000
  if (diff < 1) return 'сегодня'
  if (diff < 2) return 'вчера'
  return `${days(Math.floor(diff))} назад`
}

export const STATUS_TITLES: Record<string, string> = {
  active: 'Работает',
  pending_setup: 'Настройка',
  suspended: 'Приостановлен',
}

export const HEALTH: Record<string, { title: string; color: string }> = {
  hot: { title: 'Активно тренируется', color: '#5FBF7F' },
  warm: { title: 'Тренируется мало', color: '#E5B95C' },
  cold: { title: 'Не тренируется', color: '#E2574C' },
  setup: { title: 'Не прошёл бриф', color: '#E5B95C' },
  suspended: { title: 'Приостановлен', color: '#6B7280' },
}
