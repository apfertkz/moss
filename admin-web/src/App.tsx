import { useEffect, useState } from 'react'
import {
  LayoutDashboard, Building2, Users as UsersIcon, Sparkles,
  Wallet, BarChart3, Send, Settings as SettingsIcon, LogOut, X,
} from 'lucide-react'
import { Mark } from './Logo'
import { api, setUnauthorizedHandler } from './api'
import Login from './Login'
import Overview from './sections/Overview'
import Companies from './sections/Companies'
import CompanyCard from './sections/CompanyCard'
import Users from './sections/Users'
import Demo from './sections/Demo'
import Money from './sections/Money'
import Reports from './sections/Reports'
import Broadcast from './sections/Broadcast'
import Settings from './sections/Settings'
import OwnerApp from './owner/OwnerApp'

const NAV = [
  { key: 'overview', title: 'Обзор', icon: LayoutDashboard },
  { key: 'companies', title: 'Клиенты', icon: Building2 },
  { key: 'users', title: 'Пользователи', icon: UsersIcon },
  { key: 'demo', title: 'Демо', icon: Sparkles },
  { key: 'money', title: 'Деньги', icon: Wallet },
  { key: 'reports', title: 'Отчёты', icon: BarChart3 },
  { key: 'broadcast', title: 'Рассылка', icon: Send },
  { key: 'settings', title: 'Настройки', icon: SettingsIcon },
]

export default function App() {
  // null — ещё не спросили, '' — не вошли, иначе роль вошедшего.
  const [role, setRole] = useState<string | null | ''>(null)
  const [tab, setTab] = useState('overview')
  const [selected, setSelected] = useState<number | null>(null)
  const [reload, setReload] = useState(0)

  const who = () => api.get('/api/whoami')
    .then((r) => setRole(r.authorized ? r.role : ''))
    .catch(() => setRole(''))

  useEffect(() => {
    setUnauthorizedHandler(() => setRole(''))
    who()
  }, [])

  if (role === null) return <div className="min-h-screen bg-ink" />
  if (role === '') return <Login onDone={who} />
  if (role === 'owner') return <OwnerApp onLogout={() => setRole('')} />

  /** Открыть клиента из любого раздела — переключаемся и показываем карточку. */
  const openCompany = (id: number) => { setTab('companies'); setSelected(id) }

  const centre = () => {
    switch (tab) {
      case 'overview': return <Overview onOpen={openCompany} />
      case 'companies': return (
        <Companies selected={selected} onSelect={setSelected}
                   reloadKey={reload} onCreated={setSelected} />
      )
      case 'users': return <Users onOpenCompany={openCompany} />
      case 'demo': return <Demo />
      case 'money': return <Money onOpen={openCompany} />
      case 'reports': return <Reports />
      case 'broadcast': return <Broadcast />
      case 'settings': return <Settings />
      default: return null
    }
  }

  // Карточка справа имеет смысл только там, где есть кого выбирать.
  const showCard = tab === 'companies' && selected !== null

  return (
    <div className="flex h-screen overflow-hidden bg-ink">
      {/* ——— меню ——— */}
      <nav className="flex w-[68px] shrink-0 flex-col items-center border-r border-line py-4 lg:w-56 lg:items-stretch lg:px-3">
        <div className="mb-6 flex items-center gap-2.5 px-1 lg:px-2">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-acc/15 text-acc">
            <Mark className="h-[17px] w-auto" />
          </span>
          <span className="hidden text-[14px] font-medium tracking-tight lg:block">aisaty</span>
        </div>

        <div className="flex flex-1 flex-col gap-1">
          {NAV.map((n) => {
            const Icon = n.icon
            const on = tab === n.key
            return (
              <button key={n.key} onClick={() => setTab(n.key)} title={n.title}
                className={`flex items-center gap-3 rounded-lg px-2.5 py-2.5 text-[13px] transition-colors ${
                  on ? 'bg-acc/15 text-white' : 'text-white/45 hover:bg-white/[0.04] hover:text-white'
                }`}>
                <Icon size={17} className={on ? 'text-acc' : ''} />
                <span className="hidden lg:block">{n.title}</span>
              </button>
            )
          })}
        </div>

        <button
          onClick={async () => { await api.post('/api/logout'); setRole('') }}
          className="flex items-center gap-3 rounded-lg px-2.5 py-2.5 text-[13px] text-white/35 hover:text-white">
          <LogOut size={17} />
          <span className="hidden lg:block">Выйти</span>
        </button>
      </nav>

      {/* ——— список ——— */}
      <main className={`flex-1 overflow-y-auto ${showCard ? 'hidden md:block' : ''}`}>
        {centre()}
      </main>

      {/* ——— карточка ——— */}
      {showCard && (
        <aside className="w-full shrink-0 border-l border-line bg-black/20 md:w-[400px] lg:w-[440px]">
          <div className="flex items-center justify-end border-b border-line px-3 py-2 md:hidden">
            <button onClick={() => setSelected(null)} className="text-white/40">
              <X size={18} />
            </button>
          </div>
          <div className="h-[calc(100%-41px)] md:h-full">
            <CompanyCard id={selected!} onChanged={() => setReload((n) => n + 1)} />
          </div>
        </aside>
      )}
    </div>
  )
}
