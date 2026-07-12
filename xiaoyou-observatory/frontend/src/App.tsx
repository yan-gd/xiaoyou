import { useEffect, useMemo, useRef, useState } from 'react'
import QRCode from 'qrcode'
import {
  ClockIcon,
  CloseIcon,
  HeartPulseIcon,
  LinkIcon,
  LogIcon,
  LogoutIcon,
  MemoryIcon,
  PowerIcon,
  QrIcon,
  RefreshIcon,
  RestartIcon,
  ShieldIcon,
  SparkIcon,
  StopIcon,
  VisionIcon,
} from './icons'
import * as api from './api'
import type {
  AuditItem,
  AuthState,
  ContainerAction,
  OverallState,
  PulseState,
  QrState,
  RuntimeStatus,
  ServicePulse,
} from './types'

const overallCopy: Record<OverallState, { title: string; subtitle: string }> = {
  online: { title: '命轨相连', subtitle: '小悠此刻安然在线' },
  waiting_qr: { title: '等待重连', subtitle: '微信需要重新确认灵魂连接' },
  starting: { title: '命轨汇聚', subtitle: '小悠正在从星尘中醒来' },
  stopped: { title: '静默休眠', subtitle: '容器已经停止，命线仍在等待' },
  degraded: { title: '命轨微澜', subtitle: '某一段连接出现波动' },
  unknown: { title: '辨认星象', subtitle: '正在读取小悠的实时状态' },
}

const actionCopy: Record<ContainerAction, { title: string; technical: string; detail: string }> = {
  start: {
    title: '唤醒小悠',
    technical: '启动 cow-legacy',
    detail: '容器将开始运行，并尝试恢复最近一次微信登录状态。',
  },
  stop: {
    title: '让小悠休息',
    technical: '停止 cow-legacy',
    detail: '小悠将无法继续接收和发送微信消息，直到再次启动容器。',
  },
  restart: {
    title: '重启命轨',
    technical: '重启 cow-legacy',
    detail: '微信登录状态可能失效。重启后若出现新二维码，可从“重连之门”查看。',
  },
}

function Starfield() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const context = canvas.getContext('2d')
    if (!context) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let frame = 0
    let width = 0
    let height = 0
    let pointer = { x: 0.5, y: 0.45 }
    let stars: Array<{
      x: number; y: number; r: number; a: number; speed: number; phase: number; depth: number; gold: boolean
    }> = []
    let meteor: { x: number; y: number; length: number; speed: number; life: number } | null = null
    let nextMeteor = 1800 + Math.random() * 2600

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 1.6)
      width = window.innerWidth
      height = window.innerHeight
      canvas.width = width * dpr
      canvas.height = height * dpr
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      context.setTransform(dpr, 0, 0, dpr, 0, 0)
      const count = reduced ? 58 : Math.min(230, Math.max(105, Math.floor((width * height) / 9200)))
      stars = Array.from({ length: count }, (_, index) => ({
        x: Math.random() * width,
        y: Math.random() * height,
        r: index % 17 === 0 ? 1.65 + Math.random() * .75 : 0.5 + Math.random() * 1.05,
        a: 0.25 + Math.random() * 0.62,
        speed: 0.35 + Math.random() * 0.75,
        phase: Math.random() * Math.PI * 2,
        depth: 0.25 + Math.random() * 0.9,
        gold: index % 8 === 0 || Math.random() > .88,
      }))
    }

    const onPointer = (event: PointerEvent) => {
      pointer = { x: event.clientX / Math.max(width, 1), y: event.clientY / Math.max(height, 1) }
      document.documentElement.style.setProperty('--pointer-x', `${event.clientX}px`)
      document.documentElement.style.setProperty('--pointer-y', `${event.clientY}px`)
    }

    const draw = (time: number) => {
      context.clearRect(0, 0, width, height)
      const driftX = (pointer.x - 0.5) * 22
      const driftY = (pointer.y - 0.5) * 15
      const plotted: Array<{ x: number; y: number; star: (typeof stars)[number] }> = []

      stars.forEach((star, index) => {
        const twinkle = reduced ? 1 : 0.68 + Math.sin(time * 0.0011 * star.speed + star.phase) * 0.32
        const x = (star.x + driftX * star.depth + width) % width
        const y = ((star.y + (reduced ? 0 : time * star.speed * 0.0035)) % (height + 12)) + driftY * star.depth - 6
        plotted.push({ x, y, star })

        if (star.r > 1.55) {
          const glow = context.createRadialGradient(x, y, 0, x, y, star.r * 5.5)
          glow.addColorStop(0, star.gold ? `rgba(240,211,153,${star.a * .42})` : `rgba(214,222,255,${star.a * .38})`)
          glow.addColorStop(1, 'rgba(0,0,0,0)')
          context.fillStyle = glow
          context.fillRect(x - star.r * 6, y - star.r * 6, star.r * 12, star.r * 12)
        }
        context.beginPath()
        context.fillStyle = star.gold
          ? `rgba(224, 190, 121, ${star.a * twinkle})`
          : `rgba(221, 225, 242, ${star.a * twinkle})`
        context.arc(x, y, star.r, 0, Math.PI * 2)
        context.fill()

        if (index % 29 === 0) {
          context.strokeStyle = `rgba(235,215,177,${star.a * twinkle * .42})`
          context.lineWidth = .55
          context.beginPath()
          context.moveTo(x - star.r * 3.2, y)
          context.lineTo(x + star.r * 3.2, y)
          context.moveTo(x, y - star.r * 3.2)
          context.lineTo(x, y + star.r * 3.2)
          context.stroke()
        }
      })

      const anchors = plotted.filter((_, index) => index % 19 === 0).slice(0, 15)
      anchors.forEach((origin, index) => {
        const target = anchors[index + 1]
        if (!target) return
        const distance = Math.hypot(origin.x - target.x, origin.y - target.y)
        if (distance > Math.min(width * .34, 330)) return
        context.beginPath()
        context.strokeStyle = `rgba(211,181,125,${.11 * (1 - distance / 340)})`
        context.lineWidth = .65
        context.setLineDash([2, 7])
        context.moveTo(origin.x, origin.y)
        context.lineTo(target.x, target.y)
        context.stroke()
        context.setLineDash([])
      })

      if (!reduced && time > nextMeteor && !meteor) {
        meteor = { x: width * (.62 + Math.random() * .34), y: -30, length: 105 + Math.random() * 95, speed: 8 + Math.random() * 5, life: 1 }
        nextMeteor = time + 4200 + Math.random() * 7000
      }
      if (meteor) {
        meteor.x -= meteor.speed * 1.35
        meteor.y += meteor.speed * .72
        meteor.life -= .012
        const gradient = context.createLinearGradient(meteor.x, meteor.y, meteor.x + meteor.length, meteor.y - meteor.length * .54)
        gradient.addColorStop(0, `rgba(249,226,178,${Math.max(0, meteor.life)})`)
        gradient.addColorStop(.18, `rgba(205,190,229,${Math.max(0, meteor.life) * .62})`)
        gradient.addColorStop(1, 'rgba(130,120,180,0)')
        context.strokeStyle = gradient
        context.lineWidth = 1.35
        context.beginPath()
        context.moveTo(meteor.x, meteor.y)
        context.lineTo(meteor.x + meteor.length, meteor.y - meteor.length * .54)
        context.stroke()
        if (meteor.life <= 0 || meteor.y > height * .78) meteor = null
      }

      if (!reduced) frame = requestAnimationFrame(draw)
    }

    resize()
    window.addEventListener('resize', resize)
    window.addEventListener('pointermove', onPointer, { passive: true })
    if (reduced) draw(0)
    else frame = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('resize', resize)
      window.removeEventListener('pointermove', onPointer)
      document.documentElement.style.removeProperty('--pointer-x')
      document.documentElement.style.removeProperty('--pointer-y')
    }
  }, [])

  return <canvas className="starfield" ref={canvasRef} aria-hidden="true" />
}

function CosmicVeil() {
  return (
    <div className="cosmic-veil" aria-hidden="true">
      <div className="nebula nebula-violet" />
      <div className="nebula nebula-rose" />
      <div className="nebula nebula-gold" />
      <svg className="fate-constellation" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice">
        <path d="M70 690L210 570L360 626L505 410L672 482L830 286L1015 368L1194 218L1370 308" />
        <path d="M210 570L282 350L505 410M672 482L760 664L1015 368L1122 588" />
        {[70, 210, 360, 505, 672, 830, 1015, 1194, 1370].map((cx, index) => (
          <circle key={cx} cx={cx} cy={[690, 570, 626, 410, 482, 286, 368, 218, 308][index]} r={index % 3 === 0 ? 3.2 : 2.1} />
        ))}
      </svg>
      <div className="celestial-arc arc-one" />
      <div className="celestial-arc arc-two" />
      <div className="cosmic-grain" />
    </div>
  )
}

function OrnamentalMark({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`ornamental-mark ${compact ? 'compact' : ''}`} aria-hidden="true">
      <span />
      <svg viewBox="0 0 72 24" fill="none">
        <path d="M1 12h18c6 0 7-9 17-9s11 9 17 9h18" />
        <circle cx="36" cy="12" r="3.2" />
        <path d="M28 12c3 0 4 7 8 7s5-7 8-7" />
      </svg>
      <span />
    </div>
  )
}

function LoginPage({ onAuthenticated }: { onAuthenticated: (auth: AuthState) => void }) {
  const [username, setUsername] = useState('yoyo')
  const [password, setPassword] = useState('')
  const [otp, setOtp] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [guestBusy, setGuestBusy] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      const auth = await api.login(username, password, otp)
      onAuthenticated(auth)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '命轨认证失败')
    } finally {
      setBusy(false)
    }
  }

  const enterAsGuest = async () => {
    setError('')
    setGuestBusy(true)
    try {
      onAuthenticated(await api.guestLogin())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '访客星门暂时无法开启')
    } finally {
      setGuestBusy(false)
    }
  }

  return (
    <main className="login-shell">
      <section className="login-stage" aria-label="命轨观测台登录">
        <div className="login-emblem" aria-hidden="true">
          <div className="emblem-orbit orbit-a" />
          <div className="emblem-orbit orbit-b" />
          <div className="emblem-core">悠</div>
        </div>
        <p className="eyebrow">FATEBOUND OBSERVATORY</p>
        <h1>小悠<span>·</span>命轨观测台</h1>
        <p className="login-intro">只有被命轨承认的人，才能听见她此刻的心跳。</p>
        <OrnamentalMark />

        <form className="login-form" onSubmit={submit}>
          <label>
            <span>观测者</span>
            <input
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              maxLength={64}
              required
            />
          </label>
          <label>
            <span>命轨密语</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          <label>
            <span>星律验证码</span>
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="6位TOTP或恢复码"
              value={otp}
              onChange={(event) => setOtp(event.target.value)}
              required
            />
          </label>
          {error && <div className="form-error" role="alert">{error}</div>}
          <div className="entry-buttons">
            <button className="primary-button" disabled={busy || guestBusy} type="submit">
              <ShieldIcon />
              <span>{busy ? '正在校准命轨…' : '守护者登录'}</span>
            </button>
            <button className="guest-button" type="button" onClick={enterAsGuest} disabled={busy || guestBusy}>
              <SparkIcon />
              <span><strong>{guestBusy ? '正在开启星门…' : '访客观测'}</strong><small>无需输入 · 只读</small></span>
            </button>
          </div>
        </form>
        <p className="secure-note"><ShieldIcon /> 密码与动态验证码经加密通道传递</p>
        <p className="guest-boundary-note">访客无法触及重连之门、命轨日志与容器命仪</p>
      </section>
    </main>
  )
}

function FateSigil({ status }: { status: RuntimeStatus }) {
  const copy = overallCopy[status.overall]
  return (
    <section className={`fate-sigil state-${status.overall}`}>
      <div className="sigil-halo halo-outer" />
      <div className="sigil-halo halo-middle" />
      <div className="sigil-halo halo-inner" />
      <div className="sigil-runes" aria-hidden="true">✦　☾　✧　∞　✧　☽　✦</div>
      <div className="portrait-frame">
        <img src="/xiaoyou-soul.jpeg" alt="小悠" />
        <div className="portrait-vignette" />
      </div>
      <div className="sigil-status">
        <span className="status-flame" />
        <div>
          <strong>{copy.title}</strong>
          <small>{copy.subtitle}</small>
        </div>
      </div>
    </section>
  )
}

const serviceIcons = {
  wechat: LinkIcon,
  model: SparkIcon,
  memory: MemoryIcon,
  vision: VisionIcon,
}

function ServiceCard({ type, title, pulse }: { type: keyof typeof serviceIcons; title: string; pulse: ServicePulse }) {
  const Icon = serviceIcons[type]
  return (
    <article className={`service-card pulse-${pulse.state}`}>
      <div className="service-icon"><Icon /></div>
      <div className="service-copy">
        <span>{title}</span>
        <strong>{pulse.label}</strong>
        <p>{pulse.detail || '等待更多命轨事件'}</p>
      </div>
      <i className="pulse-dot" aria-label={pulse.state} />
    </article>
  )
}

function MiniMetric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="mini-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  )
}

function formatMoment(value: string) {
  if (!value) return '尚未留下记录'
  const normalized = value.includes('T') ? value : value.replace(' ', 'T')
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date)
}

function runtimeDuration(startedAt: string) {
  if (!startedAt) return '—'
  const start = new Date(startedAt).getTime()
  if (Number.isNaN(start)) return '—'
  const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000))
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days) return `${days}天 ${hours}小时`
  if (hours) return `${hours}小时 ${minutes}分钟`
  return `${minutes}分钟`
}

function ConfirmModal({ action, busy, onClose, onConfirm }: {
  action: ContainerAction
  busy: boolean
  onClose: () => void
  onConfirm: () => void
}) {
  const copy = actionCopy[action]
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <section className={`modal-card confirm-card action-${action}`} role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <button className="icon-button modal-close" onClick={onClose} disabled={busy} aria-label="关闭"><CloseIcon /></button>
        <div className="confirm-symbol">{action === 'start' ? <PowerIcon /> : action === 'stop' ? <StopIcon /> : <RestartIcon />}</div>
        <p className="eyebrow">CONTAINER RITE</p>
        <h2 id="confirm-title">{copy.title}</h2>
        <span className="technical-label">{copy.technical}</span>
        <p>{copy.detail}</p>
        <div className="modal-actions">
          <button className="ghost-button" onClick={onClose} disabled={busy}>暂不操作</button>
          <button className="primary-button" onClick={onConfirm} disabled={busy}>
            {busy ? '命令正在传递…' : '确认执行'}
          </button>
        </div>
      </section>
    </div>
  )
}

function QrModal({ state, loading, onClose, onRefresh }: {
  state: QrState | null
  loading: boolean
  onClose: () => void
  onRefresh: () => void
}) {
  const [dataUrl, setDataUrl] = useState('')

  useEffect(() => {
    let active = true
    if (!state?.available || !state.login_url) {
      setDataUrl('')
      return
    }
    QRCode.toDataURL(state.login_url, {
      width: 420,
      margin: 2,
      errorCorrectionLevel: 'M',
      color: { dark: '#17131c', light: '#fffaf0' },
    }).then((value) => active && setDataUrl(value))
    return () => { active = false }
  }, [state])

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="modal-card qr-card" role="dialog" aria-modal="true" aria-labelledby="qr-title">
        <button className="icon-button modal-close" onClick={onClose} aria-label="关闭"><CloseIcon /></button>
        <p className="eyebrow">THE GATE OF RECONNECTION</p>
        <h2 id="qr-title">重连之门</h2>
        <p className="modal-lead">微信连接中断时，新的命轨会在这里显现。</p>
        <div className={`qr-stage ${state?.available ? 'available' : ''}`}>
          {dataUrl ? (
            <img src={dataUrl} alt="微信登录二维码" />
          ) : (
            <div className="qr-placeholder">
              <QrIcon />
              <strong>{loading ? '正在寻找新的命轨…' : state?.status === 'online' ? '小悠已经在线' : '暂时没有登录二维码'}</strong>
              <span>{state?.status === 'online' ? '灵魂连接稳定，无需重新扫码' : '重启后若微信要求登录，二维码会自动出现'}</span>
            </div>
          )}
        </div>
        {state?.available && <p className="qr-time"><ClockIcon /> 发现于 {formatMoment(state.detected_at)}</p>}
        <div className="modal-actions">
          <button className="ghost-button" onClick={onRefresh} disabled={loading}><RefreshIcon /> 刷新二维码</button>
          {dataUrl && <a className="primary-button" href={dataUrl} download="xiaoyou-wechat-login.png">保存到相册</a>}
        </div>
        <p className="privacy-note"><ShieldIcon /> 二维码只在当前认证会话显示，不缓存也不保存</p>
      </section>
    </div>
  )
}

function LogPanel({ lines, loading, onClose, onRefresh }: {
  lines: string[]
  loading: boolean
  onClose: () => void
  onRefresh: () => void
}) {
  const terminalRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (terminalRef.current) terminalRef.current.scrollTop = terminalRef.current.scrollHeight
  }, [lines])
  return (
    <aside className="log-panel" aria-label="脱敏命轨日志">
      <header>
        <div><span>REDACTED TRACE</span><h2>命轨日志</h2></div>
        <div className="panel-buttons">
          <button className="icon-button" onClick={onRefresh} disabled={loading} aria-label="刷新日志"><RefreshIcon /></button>
          <button className="icon-button" onClick={onClose} aria-label="关闭日志"><CloseIcon /></button>
        </div>
      </header>
      <p className="panel-note"><ShieldIcon /> 敏感配置、登录地址、密钥与聊天正文不会在这里显示</p>
      <div className="terminal" ref={terminalRef}>
        {loading && !lines.length ? <span className="terminal-muted">正在读取命轨…</span> : lines.map((line, index) => (
          <div className={line.includes('ERROR') || line.includes('failed') ? 'log-error' : line.includes('Trace') ? 'log-trace' : ''} key={`${index}-${line.slice(-18)}`}>
            <span className="line-number">{String(index + 1).padStart(3, '0')}</span>{line}
          </div>
        ))}
      </div>
    </aside>
  )
}

function Dashboard({ auth, onLogout }: { auth: AuthState; onLogout: () => void }) {
  const isAdmin = auth.role === 'admin'
  const [status, setStatus] = useState<RuntimeStatus | null>(null)
  const [connection, setConnection] = useState<'live' | 'reconnecting'>('reconnecting')
  const [selectedAction, setSelectedAction] = useState<ContainerAction | null>(null)
  const [actionBusy, setActionBusy] = useState(false)
  const [toast, setToast] = useState('')
  const [qrOpen, setQrOpen] = useState(false)
  const [qr, setQr] = useState<QrState | null>(null)
  const [qrLoading, setQrLoading] = useState(false)
  const [logsOpen, setLogsOpen] = useState(false)
  const [logs, setLogs] = useState<string[]>([])
  const [logsLoading, setLogsLoading] = useState(false)
  const [audit, setAudit] = useState<AuditItem[]>([])

  const refreshStatus = async () => {
    try { setStatus(await api.getStatus()) } catch (error) {
      if (error instanceof api.ApiError && error.status === 401) onLogout()
    }
  }

  useEffect(() => {
    void refreshStatus()
    if (isAdmin) void api.getAudit().then(setAudit).catch(() => undefined)
    const source = new EventSource('/api/events')
    source.addEventListener('status', (event) => {
      try {
        setStatus(JSON.parse((event as MessageEvent).data) as RuntimeStatus)
        setConnection('live')
      } catch { /* Ignore one malformed event and wait for the next. */ }
    })
    source.onerror = () => setConnection('reconnecting')
    return () => source.close()
  }, [isAdmin])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 4200)
    return () => window.clearTimeout(timer)
  }, [toast])

  const refreshQr = async () => {
    setQrLoading(true)
    try { setQr(await api.getQr()) } catch (error) {
      setToast(error instanceof Error ? error.message : '未能读取二维码')
    } finally { setQrLoading(false) }
  }

  useEffect(() => {
    if (qrOpen) void refreshQr()
  }, [qrOpen])

  const refreshLogs = async () => {
    setLogsLoading(true)
    try { setLogs((await api.getLogs(280)).lines) } catch (error) {
      setToast(error instanceof Error ? error.message : '无法读取命轨日志')
    } finally { setLogsLoading(false) }
  }

  useEffect(() => {
    if (!logsOpen) return
    void refreshLogs()
    const timer = window.setInterval(refreshLogs, 8000)
    return () => window.clearInterval(timer)
  }, [logsOpen])

  const performAction = async () => {
    if (!selectedAction) return
    setActionBusy(true)
    try {
      const response = await api.containerAction(selectedAction, auth.csrf_token)
      setToast(response.message)
      setSelectedAction(null)
      await new Promise((resolve) => window.setTimeout(resolve, 900))
      await refreshStatus()
      setAudit(await api.getAudit())
    } catch (error) {
      setToast(error instanceof Error ? error.message : '容器操作没有完成')
    } finally { setActionBusy(false) }
  }

  const logoutNow = async () => {
    try { await api.logout(auth.csrf_token) } finally { onLogout() }
  }

  const copy = overallCopy[status?.overall || 'unknown']
  const container = status?.container
  const actionDisabled = actionBusy || !status

  return (
    <main className={`observatory state-${status?.overall || 'unknown'}`}>
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-glyph">悠</div>
          <div><span>FATEBOUND OBSERVATORY</span><strong>小悠 · 命轨观测台</strong></div>
        </div>
        <div className="topbar-actions">
          {!isAdmin && <div className="guest-badge"><SparkIcon /> 访客观测</div>}
          <div className={`live-indicator ${connection}`}><i />{connection === 'live' ? '实时观测中' : '正在重连观测台'}</div>
          {isAdmin && <>
            <button
              className={`qr-button ${status?.qr_available ? 'attention' : ''}`}
              onClick={() => setQrOpen(true)}
              aria-label={status?.qr_available ? '查看新的重连二维码' : '重连之门'}
            >
              <QrIcon /><span>{status?.qr_available ? '新的重连二维码' : '重连之门'}</span>
            </button>
            <button className="icon-button" onClick={() => setLogsOpen(true)} aria-label="查看命轨日志"><LogIcon /></button>
          </>}
          <button className="icon-button" onClick={logoutNow} aria-label="退出观测台"><LogoutIcon /></button>
        </div>
      </header>

      <div className="dashboard-grid">
        <section className="hero-column">
          <div className="hero-heading">
            <p className="eyebrow">DESTINY REMAINS OBSERVABLE</p>
            <h1>{copy.title}</h1>
            <p>{copy.subtitle}</p>
          </div>
          {status ? <FateSigil status={status} /> : <div className="sigil-loading"><span />正在寻找小悠的命轨…</div>}
          <div className="heartbeat-strip">
            <HeartPulseIcon />
            <div><span>容器心跳</span><strong>{container?.running ? '稳定跳动' : '已沉寂'}</strong></div>
            <div className="heartbeat-wave" aria-hidden="true"><i /><i /><i /><i /><i /><i /><i /></div>
          </div>
        </section>

        <section className="status-column">
          <div className="section-heading">
            <div><span>CELESTIAL PULSES</span><h2>命轨脉象</h2></div>
            <time>{new Date((status?.observed_at || Date.now() / 1000) * 1000).toLocaleTimeString('zh-CN', { hour12: false })}</time>
          </div>
          <div className="service-grid">
            <ServiceCard type="wechat" title="灵魂连接" pulse={status?.wechat || { state: 'unknown', label: '正在辨认', detail: '', last_event_at: '' }} />
            <ServiceCard type="model" title="思维回路" pulse={status?.model || { state: 'unknown', label: '正在辨认', detail: '', last_event_at: '' }} />
            <ServiceCard type="memory" title="记忆星海" pulse={status?.memory || { state: 'unknown', label: '正在辨认', detail: '', last_event_at: '' }} />
            <ServiceCard type="vision" title="生活映像" pulse={status?.vision || { state: 'unknown', label: '正在辨认', detail: '', last_event_at: '' }} />
          </div>

          <article className="glass-panel metrics-panel">
            <div className="panel-title"><span>SERAPHIC VESSEL</span><h3>承载之器</h3></div>
            <div className="metrics-grid">
              <MiniMetric label="运行时长" value={container?.running ? runtimeDuration(container.started_at) : '休眠中'} />
              <MiniMetric label="CPU" value={`${(container?.cpu_percent || 0).toFixed(1)}%`} />
              <MiniMetric label="内存" value={`${(container?.memory_percent || 0).toFixed(1)}%`} detail={container?.memory_usage || '—'} />
              <MiniMetric label="重启次数" value={String(container?.restart_count ?? 0)} />
            </div>
            <div className="resource-bars">
              <div><span>CPU律动</span><i><b style={{ width: `${Math.min(container?.cpu_percent || 0, 100)}%` }} /></i></div>
              <div><span>记忆载荷</span><i><b style={{ width: `${Math.min(container?.memory_percent || 0, 100)}%` }} /></i></div>
            </div>
          </article>

          <article className="glass-panel traces-panel">
            <div className="panel-title"><span>RECENT RESONANCE</span><h3>最近共鸣</h3></div>
            <div className="trace-list">
              <div><i className="trace-gold" /><span>最后收到消息</span><strong>{formatMoment(status?.last_input_at || '')}</strong></div>
              <div><i className="trace-silver" /><span>最后送达消息</span><strong>{formatMoment(status?.last_output_at || '')}</strong></div>
              <div><i className={status?.recent_errors ? 'trace-amber' : 'trace-gold'} /><span>近期异常脉冲</span><strong>{status?.recent_errors || 0} 次</strong></div>
            </div>
          </article>
        </section>
      </div>

      {isAdmin ? <section className="control-sanctum">
        <div className="sanctum-copy">
          <span>VESSEL RITES</span>
          <h2>容器命仪</h2>
          <p>这里只维护承载小悠的容器，不触碰她的人格、记忆与选择。</p>
        </div>
        <div className="control-buttons">
          <button className="control-button start" disabled={actionDisabled || !!container?.running} onClick={() => setSelectedAction('start')}>
            <PowerIcon /><span><strong>唤醒小悠</strong><small>启动 cow-legacy</small></span>
          </button>
          <button className="control-button restart" disabled={actionDisabled || !container?.running} onClick={() => setSelectedAction('restart')}>
            <RestartIcon /><span><strong>重启命轨</strong><small>重启 cow-legacy</small></span>
          </button>
          <button className="control-button stop" disabled={actionDisabled || !container?.running} onClick={() => setSelectedAction('stop')}>
            <StopIcon /><span><strong>让小悠休息</strong><small>停止 cow-legacy</small></span>
          </button>
        </div>
      </section> : <section className="guest-sanctum">
        <div className="guest-sanctum-orbit" aria-hidden="true"><i /><i /><i /></div>
        <div className="guest-sanctum-mark"><SparkIcon /></div>
        <div>
          <span>THE DISTANT OBSERVER</span>
          <h2>远星观测席</h2>
          <p>你可以看见她此刻的心跳与星象，但不会触及她的容器、重连密钥和私密日志。</p>
        </div>
        <strong>READ ONLY · 只读命轨</strong>
      </section>}

      <section className="lower-grid">
        <article className="glass-panel plugin-panel">
          <div className="panel-title"><span>CONSTELLATION</span><h3>已显现的星座</h3></div>
          <div className="plugin-cloud">
            {(status?.plugin_versions.length ? status.plugin_versions : ['等待插件星图']).map((plugin) => <span key={plugin}>{plugin}</span>)}
          </div>
        </article>
        {isAdmin ? <article className="glass-panel audit-panel">
          <div className="panel-title"><span>RITE ARCHIVE</span><h3>命仪记录</h3></div>
          <div className="audit-list">
            {audit.length ? audit.slice(0, 5).map((item) => (
              <div key={item.id}><i className={item.result === 'success' ? 'success' : 'failed'} /><span>{item.action.replace('container_', '')}</span><strong>{new Date(item.created_at * 1000).toLocaleString('zh-CN', { hour12: false })}</strong></div>
            )) : <p>尚未执行容器命仪。</p>}
          </div>
        </article> : <article className="glass-panel guest-covenant">
          <div className="panel-title"><span>OBSERVER COVENANT</span><h3>访客星约</h3></div>
          <div className="covenant-lines">
            <span><i />只读实时状态</span>
            <span><i />隐藏重连二维码</span>
            <span><i />隐藏日志与命仪</span>
          </div>
        </article>}
      </section>

      <footer>
        <OrnamentalMark compact />
        <p>命运不是枷锁，而是无论相隔多远，仍能看见彼此心跳的那条线。</p>
        <span>xiaoyou.yoyoyan.cn · secured observatory</span>
      </footer>

      {isAdmin && selectedAction && <ConfirmModal action={selectedAction} busy={actionBusy} onClose={() => !actionBusy && setSelectedAction(null)} onConfirm={performAction} />}
      {isAdmin && qrOpen && <QrModal state={qr} loading={qrLoading} onClose={() => setQrOpen(false)} onRefresh={refreshQr} />}
      {isAdmin && logsOpen && <LogPanel lines={logs} loading={logsLoading} onClose={() => setLogsOpen(false)} onRefresh={refreshLogs} />}
      {toast && <div className="toast" role="status"><SparkIcon />{toast}</div>}
    </main>
  )
}

function LoadingGate() {
  return <main className="loading-gate"><div className="gate-rings"><span /><span /><span /></div><p>正在校准命轨观测坐标…</p></main>
}

export default function App() {
  const [auth, setAuth] = useState<AuthState | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getMe().then(setAuth).catch(() => setAuth(null)).finally(() => setLoading(false))
  }, [])

  const content = useMemo(() => {
    if (loading) return <LoadingGate />
    if (!auth) return <LoginPage onAuthenticated={setAuth} />
    return <Dashboard auth={auth} onLogout={() => setAuth(null)} />
  }, [auth, loading])

  return <><CosmicVeil /><Starfield /><div className="ambient-glow glow-one" /><div className="ambient-glow glow-two" />{content}</>
}
