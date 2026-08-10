import { useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { api } from './api'
import { Btn, Input } from './ui'

/**
 * Один вход на двоих.
 *
 * Руководитель компании вводит свой Telegram ID и пароль — и попадает в
 * свой кабинет. Владелец продукта оставляет поле логина пустым: тогда
 * пароль проверяется по настройке сервиса и следом приходит код в Telegram.
 *
 * Почему не две отдельные страницы: адрес у панели один, его дают клиенту
 * в письме, и человек не должен выбирать «какая из двух форм моя».
 */
export default function Login({ onDone }: { onDone: () => void }) {
  const [stage, setStage] = useState<'password' | 'code'>('password')
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [token, setToken] = useState('')
  const [error, setError] = useState('')

  const submitPassword = async () => {
    setError('')
    try {
      const r = await api.post('/api/login', { login: login.trim(), password })
      if (r.role === 'owner') { onDone(); return }
      setToken(r.token)
      setStage('code')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const submitCode = async () => {
    setError('')
    try {
      await api.post('/api/verify', { token, code })
      onDone()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-5">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-acc/15 text-acc">
            <ShieldCheck size={20} />
          </span>
          <div>
            <div className="text-lg font-medium tracking-tight">MOSS SALE</div>
            <div className="text-[12px] text-white/40">личный кабинет</div>
          </div>
        </div>

        {stage === 'password' ? (
          <div className="space-y-3">
            <Input
              autoFocus
              inputMode="numeric"
              placeholder="Telegram ID"
              value={login}
              onChange={(e) => setLogin(e.target.value.replace(/\D/g, ''))}
              onKeyDown={(e) => e.key === 'Enter' && submitPassword()}
            />
            <Input
              type="password"
              placeholder="Пароль"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submitPassword()}
            />
            <Btn tone="accent" full onClick={submitPassword}>Войти</Btn>
            <p className="pt-1 text-[12px] leading-relaxed text-white/30">
              Логин и первый пароль пришли вам в Telegram при подключении компании.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-[13px] leading-relaxed text-white/50">
              Код отправлен в Telegram. Действует пять минут.
            </p>
            <Input
              autoFocus
              inputMode="numeric"
              placeholder="Код из шести цифр"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              onKeyDown={(e) => e.key === 'Enter' && submitCode()}
            />
            <Btn tone="accent" full onClick={submitCode}>Подтвердить</Btn>
            <button
              onClick={() => { setStage('password'); setCode(''); setError('') }}
              className="w-full py-1 text-[12px] text-white/35 hover:text-white/60"
            >
              Начать заново
            </button>
          </div>
        )}

        {error && <div className="mt-3 text-[13px] text-bad">{error}</div>}
      </div>
    </div>
  )
}
