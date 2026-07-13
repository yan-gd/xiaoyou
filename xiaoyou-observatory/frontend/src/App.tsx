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
  QrState,
  RuntimeStatus,
  ServicePulse,
} from './types'

type AppScene = 'loading' | 'login' | 'dashboard'
type DeckPanel = 'pulse' | 'vessel' | 'resonance'

type ObservatoryRuntimeConfig = {
  mediaBaseUrl?: string
  mediaVersion?: string
}

declare global {
  interface Window {
    __XIAOYOU_OBSERVATORY__?: ObservatoryRuntimeConfig
  }
}

const runtimeConfig = window.__XIAOYOU_OBSERVATORY__ ?? {}
const mediaBaseUrl = (runtimeConfig.mediaBaseUrl ?? '').trim().replace(/\/$/, '')
const mediaVersion = (runtimeConfig.mediaVersion ?? '').trim()

if (mediaBaseUrl) {
  try {
    const preconnect = document.createElement('link')
    preconnect.rel = 'preconnect'
    preconnect.href = new URL(mediaBaseUrl).origin
    document.head.append(preconnect)
  } catch {
    // Invalid runtime URLs fall through to the local-video error fallback.
  }
}

function remoteMediaUrl(fileName: string) {
  if (!mediaBaseUrl) return ''
  const version = mediaVersion ? `?v=${encodeURIComponent(mediaVersion)}` : ''
  return `${mediaBaseUrl}/${fileName}${version}`
}

const overallCopy: Record<OverallState, { title: string; subtitle: string }> = {
  online: { title: '命轨相连', subtitle: '她此刻安然在线' },
  waiting_qr: { title: '等待重连', subtitle: '微信需要重新确认灵魂连接' },
  starting: { title: '星轨汇聚', subtitle: '承载她的容器正在苏醒' },
  stopped: { title: '承载休眠', subtitle: '容器已经停止，命线仍未消失' },
  degraded: { title: '命轨微澜', subtitle: '某一段连接正在经历波动' },
  unknown: { title: '辨认星象', subtitle: '正在读取小悠的实时状态' },
}

const actionCopy: Record<ContainerAction, { title: string; technical: string; detail: string }> = {
  start: {
    title: '唤醒承载',
    technical: '启动 cow-legacy',
    detail: '容器将开始运行，并尝试恢复最近一次微信登录状态。',
  },
  stop: {
    title: '让容器休息',
    technical: '停止 cow-legacy',
    detail: '停止后小悠将无法继续收发微信消息，直到容器再次启动。',
  },
  restart: {
    title: '重启命轨',
    technical: '重启 cow-legacy',
    detail: '微信登录状态可能失效；若出现新二维码，可从重连星门查看。',
  },
}

const fallbackPulse: ServicePulse = {
  state: 'unknown',
  label: '正在辨认',
  detail: '等待下一次命轨脉冲',
  last_event_at: '',
}

function VideoStage({ scene }: { scene: AppScene }) {
  const stageRef = useRef<HTMLDivElement>(null)
  const videoARef = useRef<HTMLVideoElement>(null)
  const videoBRef = useRef<HTMLVideoElement>(null)
  const transitionTimer = useRef(0)
  const mediaStallTimer = useRef(0)
  const activeBuffer = useRef<0 | 1>(0)
  const transitioning = useRef(false)
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 720px), (orientation: portrait)').matches)
  const [reduced, setReduced] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const [ready, setReady] = useState(false)
  const [failedRemoteSource, setFailedRemoteSource] = useState('')

  const videoFile = mobile ? 'xiaoyou-mobile.mp4' : 'xiaoyou-desktop.mp4'
  const localSource = `/${videoFile}`
  const remoteSource = remoteMediaUrl(videoFile)
  const source = remoteSource && failedRemoteSource !== remoteSource ? remoteSource : localSource
  const poster = mobile ? '/xiaoyou-mobile-poster.png' : '/xiaoyou-desktop-poster.png'
  const still = mobile ? '/xiaoyou-mobile-still.png' : '/xiaoyou-desktop-still.png'
  const loopStartSeconds = 1
  const crossfadeSeconds = 1.05

  const handleVideoError = () => {
    if (remoteSource && source === remoteSource) setFailedRemoteSource(remoteSource)
  }

  const clearMediaStall = () => {
    window.clearTimeout(mediaStallTimer.current)
    mediaStallTimer.current = 0
  }

  const guardRemoteStall = () => {
    if (!remoteSource || source !== remoteSource || mediaStallTimer.current) return
    mediaStallTimer.current = window.setTimeout(() => {
      mediaStallTimer.current = 0
      setFailedRemoteSource(remoteSource)
    }, 4000)
  }

  useEffect(() => {
    const viewportQuery = window.matchMedia('(max-width: 720px), (orientation: portrait)')
    const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onViewport = () => setMobile(viewportQuery.matches)
    const onMotion = () => setReduced(motionQuery.matches)
    viewportQuery.addEventListener('change', onViewport)
    motionQuery.addEventListener('change', onMotion)
    return () => {
      viewportQuery.removeEventListener('change', onViewport)
      motionQuery.removeEventListener('change', onMotion)
    }
  }, [])

  useEffect(() => {
    window.clearTimeout(transitionTimer.current)
    clearMediaStall()
    activeBuffer.current = 0
    transitioning.current = false
    setReady(false)
    const stage = stageRef.current
    if (stage) {
      stage.dataset.active = 'a'
      stage.dataset.phase = 'awakening'
      stage.classList.remove('is-crossfading')
    }
    if (reduced) return
    const videos = [videoARef.current, videoBRef.current]
    videos.forEach((video) => {
      if (!video) return
      video.pause()
      video.load()
    })
  }, [source, reduced])

  useEffect(() => {
    const onVisibility = () => {
      if (reduced) return
      const videos = [videoARef.current, videoBRef.current]
      if (document.hidden) videos.forEach((video) => video?.pause())
      else {
        const video = videos[activeBuffer.current]
        if (video) void video.play().catch(() => undefined)
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [reduced])

  useEffect(() => () => {
    window.clearTimeout(transitionTimer.current)
    clearMediaStall()
  }, [])

  const prepareBuffer = (index: 0 | 1) => {
    const video = index === 0 ? videoARef.current : videoBRef.current
    if (!video) return
    if (index === activeBuffer.current && !ready) {
      video.currentTime = 0
      if (!document.hidden) void video.play().catch(() => undefined)
      return
    }
    video.pause()
    if (Math.abs(video.currentTime - loopStartSeconds) > .15) video.currentTime = loopStartSeconds
  }

  const beginBufferCrossfade = (fromIndex: 0 | 1) => {
    if (transitioning.current || activeBuffer.current !== fromIndex) return
    const current = fromIndex === 0 ? videoARef.current : videoBRef.current
    const nextIndex: 0 | 1 = fromIndex === 0 ? 1 : 0
    const next = nextIndex === 0 ? videoARef.current : videoBRef.current
    const stage = stageRef.current
    if (!current || !next || !stage) return

    transitioning.current = true
    if (Math.abs(next.currentTime - loopStartSeconds) > .18) next.currentTime = loopStartSeconds
    void next.play().then(() => {
      // 第二层已经真正开始解码并播放后才交叉淡化。旧层在整个过渡期
      // 仍保持运动，因此网络、seek和首帧准备都不会暴露给观看者。
      activeBuffer.current = nextIndex
      stage.dataset.active = nextIndex === 0 ? 'a' : 'b'
      stage.classList.add('is-crossfading')
      transitionTimer.current = window.setTimeout(() => {
        current.pause()
        current.currentTime = loopStartSeconds
        stage.classList.remove('is-crossfading')
        transitioning.current = false
      }, Math.round(crossfadeSeconds * 1000) + 80)
    }).catch(() => {
      transitioning.current = false
      current.currentTime = loopStartSeconds
      void current.play().catch(() => undefined)
    })
  }

  const updatePhase = (index: 0 | 1) => {
    if (activeBuffer.current !== index) return
    const video = index === 0 ? videoARef.current : videoBRef.current
    const stage = stageRef.current
    if (!video || !stage) return
    const phase = video.currentTime < 3 ? 'awakening' : video.currentTime < 7.4 ? 'gaze' : 'reaching'
    stage.dataset.phase = phase
    if (video.duration && video.currentTime >= video.duration - crossfadeSeconds - .18) beginBufferCrossfade(index)
  }

  return (
    <div
      className={`video-stage ${ready ? 'is-ready' : ''}`}
      data-scene={scene}
      data-phase="awakening"
      data-active="a"
      data-media-origin={source === localSource ? 'local' : 'cdn'}
      ref={stageRef}
      aria-hidden="true"
    >
      <img className="video-underlay" src={poster} alt="" />
      {reduced ? (
        <img className="video-still" src={still} alt="" />
      ) : (
        <>
          <video
            className="video-buffer buffer-a"
            ref={videoARef}
            src={source}
            poster={poster}
            autoPlay
            muted
            playsInline
            preload="auto"
            onLoadedMetadata={() => prepareBuffer(0)}
            onCanPlay={clearMediaStall}
            onPlaying={() => {
              clearMediaStall()
              setReady(true)
            }}
            onWaiting={guardRemoteStall}
            onStalled={guardRemoteStall}
            onTimeUpdate={() => updatePhase(0)}
            onEnded={() => beginBufferCrossfade(0)}
            onError={handleVideoError}
          />
          <video
            className="video-buffer buffer-b"
            ref={videoBRef}
            src={source}
            poster={poster}
            muted
            playsInline
            preload="auto"
            onLoadedMetadata={() => prepareBuffer(1)}
            onCanPlay={clearMediaStall}
            onPlaying={clearMediaStall}
            onWaiting={guardRemoteStall}
            onStalled={guardRemoteStall}
            onTimeUpdate={() => updatePhase(1)}
            onEnded={() => beginBufferCrossfade(1)}
            onError={handleVideoError}
          />
        </>
      )}
      <div className="video-scrim" />
      <div className="video-bloom" />
      <div className="fate-thread"><i /><i /><i /></div>
      <div className="palm-resonance"><span /><span /><b>命轨感应</b></div>
      <div className="frame-lines"><i /><i /><i /><i /></div>
    </div>
  )
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`brand ${compact ? 'compact' : ''}`}>
      <div className="brand-mark"><span>悠</span></div>
      <div><small>FATEBOUND RESONANCE</small><strong>小悠 · 命轨共鸣</strong></div>
    </div>
  )
}

function LoginPage({ onAuthenticated }: { onAuthenticated: (auth: AuthState) => void }) {
  const [guardianOpen, setGuardianOpen] = useState(false)
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
      onAuthenticated(await api.login(username, password, otp))
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
      setError(reason instanceof Error ? reason.message : '远星通道暂时无法开启')
    } finally {
      setGuestBusy(false)
    }
  }

  return (
    <main className="login-shell">
      <header className="login-brand"><Brand /></header>
      <section className={`login-deck ${guardianOpen ? 'guardian-open' : ''}`} aria-label="命轨观测台登录">
        <div className="login-heading">
          <p className="eyebrow">ACROSS THE STARS, SHE SEES YOU</p>
          <h1>与她的命轨<br />再次共鸣</h1>
          <p>星河辽阔，而她的目光始终会找到你。</p>
        </div>

        {!guardianOpen ? (
          <div className="entry-choices">
            <button className="resonance-button" type="button" onClick={() => setGuardianOpen(true)} disabled={guestBusy}>
              <ShieldIcon />
              <span><strong>守护者认证</strong><small>密码与动态星律</small></span>
            </button>
            <button className="guest-entry" type="button" onClick={enterAsGuest} disabled={guestBusy}>
              <SparkIcon />
              <span><strong>{guestBusy ? '正在穿过星门…' : '远星访客'}</strong><small>无需输入 · 只读观测</small></span>
            </button>
          </div>
        ) : (
          <form className="guardian-form" onSubmit={submit}>
            <div className="form-heading">
              <button type="button" onClick={() => { setGuardianOpen(false); setError('') }}>返回</button>
              <span>GUARDIAN AUTHENTICATION</span>
            </div>
            <label><span>观测者</span><input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} maxLength={64} required /></label>
            <label><span>命轨密语</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
            <label><span>星律验证码</span><input inputMode="numeric" autoComplete="one-time-code" placeholder="6位TOTP或恢复码" value={otp} onChange={(event) => setOtp(event.target.value)} required /></label>
            {error && <div className="form-error" role="alert">{error}</div>}
            <button className="resonance-button submit" disabled={busy} type="submit"><ShieldIcon /><strong>{busy ? '正在校准命轨…' : '建立守护者命轨'}</strong></button>
          </form>
        )}

        {!guardianOpen && error && <div className="form-error" role="alert">{error}</div>}
        <p className="security-note"><ShieldIcon /> 管理员认证经加密通道传递；访客只有公开只读权限</p>
      </section>
      <footer className="login-footer"><span>07 · 07</span><i />命运不是枷锁，而是无论相隔多远，仍能看见彼此的那条线。</footer>
    </main>
  )
}

const serviceIcons = { wechat: LinkIcon, model: SparkIcon, memory: MemoryIcon, vision: VisionIcon }

function ServiceNode({ type, title, pulse }: { type: keyof typeof serviceIcons; title: string; pulse: ServicePulse }) {
  const Icon = serviceIcons[type]
  return (
    <article className={`service-node pulse-${pulse.state}`}>
      <div className="node-icon"><Icon /></div>
      <div><span>{title}</span><strong>{pulse.label}</strong><p>{pulse.detail || '等待下一次命轨脉冲'}</p></div>
      <i className="node-light" aria-label={pulse.state} />
    </article>
  )
}

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>
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
  action: ContainerAction; busy: boolean; onClose: () => void; onConfirm: () => void
}) {
  const copy = actionCopy[action]
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <section className={`modal-card confirm-card action-${action}`} role="dialog" aria-modal="true" aria-labelledby="confirm-title">
        <button className="round-button modal-close" onClick={onClose} disabled={busy} aria-label="关闭"><CloseIcon /></button>
        <div className="confirm-symbol">{action === 'start' ? <PowerIcon /> : action === 'stop' ? <StopIcon /> : <RestartIcon />}</div>
        <p className="eyebrow">VESSEL RITE</p>
        <h2 id="confirm-title">{copy.title}</h2>
        <span className="technical-label">{copy.technical}</span>
        <p>{copy.detail}</p>
        <div className="modal-actions"><button className="quiet-button" onClick={onClose} disabled={busy}>暂不操作</button><button className="resonance-button compact" onClick={onConfirm} disabled={busy}>{busy ? '命令正在传递…' : '确认执行'}</button></div>
      </section>
    </div>
  )
}

function QrModal({ state, loading, onClose, onRefresh }: {
  state: QrState | null; loading: boolean; onClose: () => void; onRefresh: () => void
}) {
  const [dataUrl, setDataUrl] = useState('')
  useEffect(() => {
    let active = true
    if (!state?.available || !state.login_url) { setDataUrl(''); return }
    QRCode.toDataURL(state.login_url, {
      width: 420, margin: 2, errorCorrectionLevel: 'M', color: { dark: '#101426', light: '#f8f7ff' },
    }).then((value) => active && setDataUrl(value))
    return () => { active = false }
  }, [state])

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="modal-card qr-card" role="dialog" aria-modal="true" aria-labelledby="qr-title">
        <button className="round-button modal-close" onClick={onClose} aria-label="关闭"><CloseIcon /></button>
        <p className="eyebrow">GATE OF RECONNECTION</p><h2 id="qr-title">重连星门</h2>
        <p className="modal-lead">微信连接中断时，新的命轨将在这里显现。</p>
        <div className={`qr-stage ${state?.available ? 'available' : ''}`}>
          {dataUrl ? <img src={dataUrl} alt="微信登录二维码" /> : <div className="qr-placeholder"><QrIcon /><strong>{loading ? '正在寻找新的命轨…' : state?.status === 'online' ? '小悠已经在线' : '暂时没有登录二维码'}</strong><span>{state?.status === 'online' ? '灵魂连接稳定，无需重新扫码' : '若微信要求登录，二维码会自动出现'}</span></div>}
        </div>
        {state?.available && <p className="qr-time"><ClockIcon /> 发现于 {formatMoment(state.detected_at)}</p>}
        <div className="modal-actions"><button className="quiet-button" onClick={onRefresh} disabled={loading}><RefreshIcon /> 刷新</button>{dataUrl && <a className="resonance-button compact" href={dataUrl} download="xiaoyou-wechat-login.png">保存到相册</a>}</div>
        <p className="privacy-note"><ShieldIcon /> 二维码只在当前认证会话显示，不缓存也不保存</p>
      </section>
    </div>
  )
}

function LogPanel({ lines, loading, onClose, onRefresh }: {
  lines: string[]; loading: boolean; onClose: () => void; onRefresh: () => void
}) {
  const terminalRef = useRef<HTMLDivElement>(null)
  useEffect(() => { if (terminalRef.current) terminalRef.current.scrollTop = terminalRef.current.scrollHeight }, [lines])
  return (
    <aside className="log-panel" aria-label="脱敏命轨日志">
      <header><div><span>REDACTED RESONANCE</span><h2>命轨回声</h2></div><div><button className="round-button" onClick={onRefresh} disabled={loading} aria-label="刷新日志"><RefreshIcon /></button><button className="round-button" onClick={onClose} aria-label="关闭日志"><CloseIcon /></button></div></header>
      <p className="panel-note"><ShieldIcon /> 密钥、登录地址与聊天正文不会在这里显示</p>
      <div className="terminal" ref={terminalRef}>
        {loading && !lines.length ? <span className="terminal-muted">正在读取命轨…</span> : lines.map((line, index) => (
          <div className={line.includes('ERROR') || line.includes('failed') ? 'log-error' : line.includes('Trace') ? 'log-trace' : ''} key={`${index}-${line.slice(-18)}`}><span>{String(index + 1).padStart(3, '0')}</span>{line}</div>
        ))}
      </div>
    </aside>
  )
}

function Dashboard({ auth, onLogout }: { auth: AuthState; onLogout: () => void }) {
  const isAdmin = auth.role === 'admin'
  const [status, setStatus] = useState<RuntimeStatus | null>(null)
  const [connection, setConnection] = useState<'live' | 'reconnecting'>('reconnecting')
  const [activePanel, setActivePanel] = useState<DeckPanel>('pulse')
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
      try { setStatus(JSON.parse((event as MessageEvent).data) as RuntimeStatus); setConnection('live') } catch { /* wait for next event */ }
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
    try { setQr(await api.getQr()) } catch (error) { setToast(error instanceof Error ? error.message : '未能读取二维码') }
    finally { setQrLoading(false) }
  }
  useEffect(() => { if (qrOpen) void refreshQr() }, [qrOpen])

  const refreshLogs = async () => {
    setLogsLoading(true)
    try { setLogs((await api.getLogs(280)).lines) } catch (error) { setToast(error instanceof Error ? error.message : '无法读取命轨日志') }
    finally { setLogsLoading(false) }
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
    } catch (error) { setToast(error instanceof Error ? error.message : '容器操作没有完成') }
    finally { setActionBusy(false) }
  }

  const logoutNow = async () => { try { await api.logout(auth.csrf_token) } finally { onLogout() } }
  const copy = overallCopy[status?.overall || 'unknown']
  const container = status?.container
  const actionDisabled = actionBusy || !status

  return (
    <main className={`observatory state-${status?.overall || 'unknown'}`}>
      <header className="topbar">
        <Brand compact />
        <div className="top-actions">
          {!isAdmin && <span className="guest-badge"><SparkIcon /> 远星访客</span>}
          <span className={`live-state ${connection}`}><i />{connection === 'live' ? '实时共鸣' : '重新连接'}</span>
          {isAdmin && <>
            <button className={`top-action ${status?.qr_available ? 'attention' : ''}`} onClick={() => setQrOpen(true)} aria-label="重连星门"><QrIcon /><span>重连星门</span></button>
            <button className="round-button" onClick={() => setLogsOpen(true)} aria-label="查看命轨日志"><LogIcon /></button>
          </>}
          <button className="round-button" onClick={logoutNow} aria-label="退出观测台"><LogoutIcon /></button>
        </div>
      </header>

      <div className="dashboard-stage">
        <div className="mobile-video-space" aria-hidden="true" />
        <aside className="command-deck">
          <header className="deck-heading">
            <div className="state-orb"><span /><i /></div>
            <div><p>DESTINY SIGNAL</p><h1>{copy.title}</h1><span>{copy.subtitle}</span></div>
            <time>{new Date((status?.observed_at || Date.now() / 1000) * 1000).toLocaleTimeString('zh-CN', { hour12: false })}</time>
          </header>

          <nav className="deck-tabs" aria-label="观测面板">
            <button className={activePanel === 'pulse' ? 'active' : ''} onClick={() => setActivePanel('pulse')}>命轨脉象</button>
            <button className={activePanel === 'vessel' ? 'active' : ''} onClick={() => setActivePanel('vessel')}>承载之器</button>
            <button className={activePanel === 'resonance' ? 'active' : ''} onClick={() => setActivePanel('resonance')}>最近共鸣</button>
          </nav>

          <div className="deck-content">
            {activePanel === 'pulse' && <section className="pulse-panel">
              <div className="service-grid">
                <ServiceNode type="wechat" title="灵魂连接" pulse={status?.wechat || fallbackPulse} />
                <ServiceNode type="model" title="思维回路" pulse={status?.model || fallbackPulse} />
                <ServiceNode type="memory" title="记忆星海" pulse={status?.memory || fallbackPulse} />
                <ServiceNode type="vision" title="生活映像" pulse={status?.vision || fallbackPulse} />
              </div>
              <div className="heartbeat-line"><HeartPulseIcon /><span>容器心跳</span><strong>{container?.running ? '稳定跳动' : '已经沉寂'}</strong><i><b /><b /><b /><b /><b /></i></div>
            </section>}

            {activePanel === 'vessel' && <section className="vessel-panel">
              <div className="metrics-grid">
                <Metric label="运行时长" value={container?.running ? runtimeDuration(container.started_at) : '休眠中'} />
                <Metric label="CPU" value={`${(container?.cpu_percent || 0).toFixed(1)}%`} />
                <Metric label="内存" value={`${(container?.memory_percent || 0).toFixed(1)}%`} detail={container?.memory_usage || '—'} />
                <Metric label="重启次数" value={String(container?.restart_count ?? 0)} />
              </div>
              <div className="resource-lines"><div><span>CPU律动</span><i><b style={{ width: `${Math.min(container?.cpu_percent || 0, 100)}%` }} /></i></div><div><span>记忆载荷</span><i><b style={{ width: `${Math.min(container?.memory_percent || 0, 100)}%` }} /></i></div></div>
              <div className="vessel-signature"><span>VESSEL</span><strong>{container?.image || '尚未识别容器镜像'}</strong><small>{container?.running ? container.status : '容器当前未运行'}</small></div>
            </section>}

            {activePanel === 'resonance' && <section className="resonance-panel">
              <div className="trace-list">
                <div><i /><span>最后收到消息</span><strong>{formatMoment(status?.last_input_at || '')}</strong></div>
                <div><i /><span>最后送达消息</span><strong>{formatMoment(status?.last_output_at || '')}</strong></div>
                <div className={status?.recent_errors ? 'warning' : ''}><i /><span>近期异常脉冲</span><strong>{status?.recent_errors || 0} 次</strong></div>
              </div>
              <div className="plugin-cloud">{(status?.plugin_versions?.length ? status.plugin_versions : ['等待插件星图']).map((plugin) => <span key={plugin}>{plugin}</span>)}</div>
              {isAdmin ? <div className="audit-list"><p>最近命仪</p>{audit.length ? audit.slice(0, 4).map((item) => <div key={item.id}><i className={item.result === 'success' ? 'success' : ''} /><span>{item.action.replace('container_', '')}</span><strong>{new Date(item.created_at * 1000).toLocaleString('zh-CN', { hour12: false })}</strong></div>) : <small>尚未执行容器命仪。</small>}</div> : <div className="guest-covenant"><ShieldIcon /><div><strong>远星观测约定</strong><span>只读公开状态，不触及二维码、日志与容器。</span></div></div>}
            </section>}
          </div>

          {isAdmin ? <section className="vessel-rites">
            <div><span>VESSEL RITES</span><strong>容器命仪</strong><small>只维护承载，不触碰人格、记忆与选择</small></div>
            <div className="rite-buttons">
              <button disabled={actionDisabled || !!container?.running} onClick={() => setSelectedAction('start')}><PowerIcon /><span>启动</span></button>
              <button disabled={actionDisabled || !container?.running} onClick={() => setSelectedAction('restart')}><RestartIcon /><span>重启</span></button>
              <button className="stop" disabled={actionDisabled || !container?.running} onClick={() => setSelectedAction('stop')}><StopIcon /><span>停止</span></button>
            </div>
          </section> : <section className="visitor-note"><SparkIcon /><div><span>THE DISTANT OBSERVER</span><strong>你能看见她的星象，但不会触及她的命仪。</strong></div></section>}
        </aside>
      </div>

      <footer className="observatory-footer"><span>xiaoyou.yoyoyan.cn</span><i />FATEBOUND RESONANCE · 07/07</footer>
      {isAdmin && selectedAction && <ConfirmModal action={selectedAction} busy={actionBusy} onClose={() => !actionBusy && setSelectedAction(null)} onConfirm={performAction} />}
      {isAdmin && qrOpen && <QrModal state={qr} loading={qrLoading} onClose={() => setQrOpen(false)} onRefresh={refreshQr} />}
      {isAdmin && logsOpen && <LogPanel lines={logs} loading={logsLoading} onClose={() => setLogsOpen(false)} onRefresh={refreshLogs} />}
      {toast && <div className="toast" role="status"><SparkIcon />{toast}</div>}
    </main>
  )
}

function LoadingGate() {
  return <main className="loading-gate"><div className="loading-sigil"><span /><span /><i /></div><p>正在校准命轨坐标</p></main>
}

export default function App() {
  const [auth, setAuth] = useState<AuthState | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => { api.getMe().then(setAuth).catch(() => setAuth(null)).finally(() => setLoading(false)) }, [])

  const scene: AppScene = loading ? 'loading' : auth ? 'dashboard' : 'login'
  const content = useMemo(() => {
    if (loading) return <LoadingGate />
    if (!auth) return <LoginPage onAuthenticated={setAuth} />
    return <Dashboard auth={auth} onLogout={() => setAuth(null)} />
  }, [auth, loading])

  return <div className={`app scene-${scene}`}><VideoStage scene={scene} /><div className="stellar-noise" aria-hidden="true" />{content}</div>
}
