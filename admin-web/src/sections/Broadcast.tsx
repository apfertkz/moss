import { useEffect, useState } from 'react'
import { Send, Eye } from 'lucide-react'
import { api } from '../api'
import { Btn, Select, Empty } from '../ui'

/**
 * Рассылка. Порядок намеренно жёсткий: выбрать сегмент, увидеть число
 * получателей, отправить тест себе и только потом всем. Массовая отправка
 * без предпросмотра — самый дорогой способ ошибиться в тексте.
 */
export default function Broadcast() {
  const [segments, setSegments] = useState<Record<string, string>>({})
  const [segment, setSegment] = useState('owners')
  const [text, setText] = useState('')
  const [count, setCount] = useState<number | null>(null)
  const [result, setResult] = useState<any>(null)
  const [tested, setTested] = useState(false)

  const preview = async (name = segment) => {
    const r = await api.post('/api/broadcast/preview', { segment: name })
    setSegments(r.segments)
    setCount(r.count)
  }
  useEffect(() => { preview() }, [])

  const change = (name: string) => { setSegment(name); setCount(null); preview(name) }

  const sendTest = async () => {
    const r = await api.post('/api/broadcast/send', { text, test: true })
    setTested(true)
    setResult({ ...r, test: true })
  }

  const send = async () => {
    if (!confirm(`Отправить ${count} получателям? Отменить будет нельзя.`)) return
    setResult(await api.post('/api/broadcast/send', { segment, text }))
    setTested(false)
  }

  return (
    <div className="max-w-2xl p-6">
      <div className="mb-4">
        <label className="mb-2 block text-[11px] uppercase tracking-[0.12em] text-white/35">Кому</label>
        <Select value={segment} onChange={(e) => change(e.target.value)}>
          {Object.entries(segments).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </Select>
        <div className="mt-2 text-[12px] text-white/40">
          {count === null ? 'считаю…' : `получателей: ${count}`}
        </div>
      </div>

      <div className="mb-4">
        <label className="mb-2 block text-[11px] uppercase tracking-[0.12em] text-white/35">Сообщение</label>
        <textarea
          value={text}
          onChange={(e) => { setText(e.target.value); setTested(false) }}
          rows={8}
          placeholder="Можно выделять *жирным* и _курсивом_"
          className="w-full rounded-lg border border-line bg-black/30 px-3 py-2.5 text-[13px]
                     leading-relaxed text-white placeholder:text-white/25 focus:border-acc/50"
        />
      </div>

      <div className="flex gap-2">
        <Btn onClick={sendTest} disabled={!text.trim()}><Eye size={14} />Тест себе</Btn>
        <Btn tone="accent" onClick={send} disabled={!text.trim() || !tested}>
          <Send size={14} />Отправить всем
        </Btn>
      </div>
      {!tested && text.trim() && (
        <div className="mt-2 text-[12px] text-white/35">
          Сначала отправьте тест себе — так включится кнопка рассылки.
        </div>
      )}

      {result && (
        <div className="mt-5 rounded-xl border border-line bg-panel p-4 text-[13px]">
          {result.test ? 'Тест отправлен вам в Telegram.' : (
            <div className="space-y-1">
              <div>Доставлено: <span className="text-ok">{result.sent}</span> из {result.total}</div>
              {result.blocked > 0 && <div className="text-white/50">Заблокировали бота: {result.blocked}</div>}
              {result.failed > 0 && <div className="text-bad">Ошибок: {result.failed}</div>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
