import { useState } from 'react'
import { KeyRound } from 'lucide-react'
import { api } from '../api'
import { Btn, Input } from '../ui'

/**
 * Окно смены первого пароля.
 *
 * Закрыть его нельзя намеренно: временный пароль ушёл в переписку и там
 * останется навсегда, поэтому пока он действует, кабинет открыт всякому,
 * кто эту переписку увидит. Ни крестика, ни клавиши Esc, ни фона под
 * кликом — только два совпадающих поля.
 */
export default function ChangePassword({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState('')

  const mismatch = repeat.length > 0 && password !== repeat
  const ready = password.length >= 8 && password === repeat

  const save = async () => {
    setError('')
    try {
      await api.post('/api/my/password', { password, repeat })
      onDone()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/95 px-5">
      <div className="w-full max-w-sm rounded-2xl border border-line bg-panel p-6">
        <div className="mb-5 flex items-center gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-acc/15 text-acc">
            <KeyRound size={18} />
          </span>
          <div>
            <div className="text-[15px] font-medium tracking-tight">Придумайте пароль</div>
            <div className="text-[12px] text-white/40">Временный больше не понадобится</div>
          </div>
        </div>

        <p className="mb-4 text-[13px] leading-relaxed text-white/45">
          Пароль, который мы прислали в Telegram, остался в переписке. Замените
          его — и кабинет будет доступен только вам.
        </p>

        <div className="space-y-3">
          <Input
            type="password"
            autoFocus
            placeholder="Новый пароль, от 8 символов"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <Input
            type="password"
            placeholder="Ещё раз"
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && ready && save()}
          />
          {mismatch && <div className="text-[12px] text-bad">Пароли не совпадают</div>}
          <Btn tone="accent" full disabled={!ready} onClick={save}>Сохранить и войти</Btn>
          {error && <div className="text-[13px] text-bad">{error}</div>}
        </div>
      </div>
    </div>
  )
}
