import { useState, type ReactNode } from 'react'
import { Loader2 } from 'lucide-react'
import { HEALTH } from './format'

/** Кнопка. Сама показывает ожидание, пока действие выполняется. */
export function Btn({
  children, onClick, tone = 'ghost', size = 'md', disabled, full,
}: {
  children: ReactNode
  onClick?: () => void | Promise<void>
  tone?: 'accent' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
  disabled?: boolean
  full?: boolean
}) {
  const [busy, setBusy] = useState(false)

  const tones = {
    accent: 'bg-acc text-ink hover:bg-acc/85 border-transparent',
    ghost: 'bg-white/[0.04] text-white/80 hover:bg-white/[0.09] hover:text-white border-line',
    danger: 'bg-bad/10 text-bad hover:bg-bad/20 border-bad/30',
  }[tone]

  const sizes = size === 'sm' ? 'px-2.5 py-1.5 text-[12px]' : 'px-3.5 py-2 text-[13px]'

  return (
    <button
      disabled={disabled || busy}
      onClick={async () => {
        if (!onClick) return
        setBusy(true)
        try { await onClick() } finally { setBusy(false) }
      }}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg border font-medium
        transition-colors disabled:opacity-40 ${tones} ${sizes} ${full ? 'w-full' : ''}`}
    >
      {busy && <Loader2 size={13} className="animate-spin" />}
      {children}
    </button>
  )
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded-lg border border-line bg-black/30 px-3 py-2 text-[13px]
        text-white placeholder:text-white/25 focus:border-acc/50 ${props.className || ''}`}
    />
  )
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={`w-full rounded-lg border border-line bg-black/30 px-3 py-2 text-[13px]
        text-white focus:border-acc/50 ${props.className || ''}`}
    />
  )
}

/** Метка статуса. Цвет несёт смысл, поэтому дублируем его словом. */
export function Tag({ children, color = '#9CA3AF' }: { children: ReactNode; color?: string }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium"
      style={{ background: `${color}1f`, color }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
      {children}
    </span>
  )
}

export function HealthTag({ health }: { health: string }) {
  const h = HEALTH[health] || HEALTH.cold
  return <Tag color={h.color}>{h.title}</Tag>
}

/** Плитка показателя. */
export function Stat({
  label, value, hint, tone,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: 'ok' | 'bad'
}) {
  const color = tone === 'ok' ? 'text-ok' : tone === 'bad' ? 'text-bad' : 'text-white'
  return (
    <div className="rounded-xl border border-line bg-panel p-4">
      <div className="text-[11px] uppercase tracking-[0.12em] text-white/35">{label}</div>
      <div className={`mt-1.5 text-2xl font-light tracking-tight ${color}`}>{value}</div>
      {hint && <div className="mt-1 text-[12px] text-white/40">{hint}</div>}
    </div>
  )
}

/** Строка «поле — значение» в карточке. */
export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line/60 py-2.5 last:border-0">
      <span className="shrink-0 text-[12px] text-white/40">{label}</span>
      <span className="text-right text-[13px] text-white/90">{children}</span>
    </div>
  )
}

export function Section({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-6">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-[11px] uppercase tracking-[0.14em] text-white/35">{title}</h3>
        {right}
      </div>
      {children}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line px-4 py-10 text-center text-[13px] text-white/30">
      {children}
    </div>
  )
}

export function Spinner() {
  return (
    <div className="flex items-center justify-center py-16 text-white/30">
      <Loader2 size={20} className="animate-spin" />
    </div>
  )
}

/** Полоса заполнения — для лимитов и мест. */
export function Bar({ used, total, tone }: { used: number; total: number; tone?: string }) {
  const share = total ? Math.min(100, (used / total) * 100) : 0
  const color = tone || (share > 90 ? '#E2574C' : share > 70 ? '#E5B95C' : '#E9A178')
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
      <div className="h-full rounded-full transition-[width] duration-500"
           style={{ width: `${share}%`, background: color }} />
    </div>
  )
}
