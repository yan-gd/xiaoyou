import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import QRCode from 'qrcode'
import {
  ArrowClockwise,
  ArrowRight,
  ArrowsClockwise,
  Aperture,
  Brain,
  ChartLineUp,
  Clock,
  Coins,
  ShareNetwork,
  Cpu,
  Database,
  Eye,
  HardDrives,
  LinkSimple,
  Memory as MemoryDevice,
  Power as PowerPhosphor,
  QrCode,
  Scroll,
  ShieldCheck,
  SignOut,
  Sparkle,
  Stop as StopPhosphor,
} from '@phosphor-icons/react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  ClockIcon,
  CloseIcon,
  PowerIcon,
  QrIcon,
  RefreshIcon,
  RestartIcon,
  ShieldIcon,
  SparkIcon,
  StopIcon,
} from './icons'
import * as api from './api'
import type {
  AuditItem,
  AuthState,
  ContainerAction,
  MetricPoint,
  MetricsResponse,
  OverallState,
  QrState,
  RuntimeStatus,
  ServicePulse,
} from './types'

type AppScene = 'loading' | 'login' | 'dashboard'

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

const desktopVideoFiles = ['xiaoyou-desktop.mp4', 'xiaoyou3.mp4', 'xiaoyou4.mp4'] as const
const mobileVideoFiles = ['xiaoyou-mobile.mp4', 'xiaoyou3.mp4', 'xiaoyou4.mp4'] as const

function VideoStage({ scene }: { scene: AppScene }) {
  const stageRef = useRef<HTMLDivElement>(null)
  const videoARef = useRef<HTMLVideoElement>(null)
  const videoBRef = useRef<HTMLVideoElement>(null)
  const transitionTimer = useRef(0)
  const mediaStallTimers = useRef<[number, number]>([0, 0])
  const activeBuffer = useRef<0 | 1>(0)
  const bufferPlaylistPositions = useRef<[number, number]>([0, 1])
  const bufferReady = useRef<[boolean, boolean]>([false, false])
  const playlistRef = useRef<readonly string[]>(desktopVideoFiles)
  const failedRemoteFiles = useRef(new Set<string>())
  const transitioning = useRef(false)
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 720px), (orientation: portrait)').matches)
  const [reduced, setReduced] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const [ready, setReady] = useState(false)

  const playlist = mobile ? mobileVideoFiles : desktopVideoFiles
  const poster = mobile ? '/xiaoyou-mobile-poster.png' : '/xiaoyou-desktop-poster.png'
  const still = mobile ? '/xiaoyou-mobile-still.png' : '/xiaoyou-desktop-still.png'
  const crossfadeSeconds = 1.05

  const videoAt = (index: 0 | 1) => index === 0 ? videoARef.current : videoBRef.current

  const clearMediaStall = (index?: 0 | 1) => {
    const indexes: Array<0 | 1> = index === undefined ? [0, 1] : [index]
    indexes.forEach((item) => {
      window.clearTimeout(mediaStallTimers.current[item])
      mediaStallTimers.current[item] = 0
    })
  }

  const sourceForFile = (fileName: string) => {
    const remote = remoteMediaUrl(fileName)
    if (remote && !failedRemoteFiles.current.has(fileName)) return { source: remote, origin: 'cdn' }
    return { source: `/${fileName}`, origin: 'local' }
  }

  const setBufferSource = (bufferIndex: 0 | 1, playlistPosition: number) => {
    const video = videoAt(bufferIndex)
    const files = playlistRef.current
    if (!video || !files.length) return
    const normalizedPosition = ((playlistPosition % files.length) + files.length) % files.length
    const fileName = files[normalizedPosition]
    const media = sourceForFile(fileName)
    clearMediaStall(bufferIndex)
    bufferPlaylistPositions.current[bufferIndex] = normalizedPosition
    bufferReady.current[bufferIndex] = false
    video.pause()
    video.dataset.mediaFile = fileName
    video.dataset.mediaOrigin = media.origin
    video.src = media.source
    video.load()
    if (activeBuffer.current === bufferIndex && stageRef.current) stageRef.current.dataset.mediaOrigin = media.origin
  }

  const fallbackBufferToLocal = (index: 0 | 1) => {
    const video = videoAt(index)
    const fileName = video?.dataset.mediaFile
    if (!video || !fileName || video.dataset.mediaOrigin !== 'cdn') return
    failedRemoteFiles.current.add(fileName)
    setBufferSource(index, bufferPlaylistPositions.current[index])
  }

  const handleVideoError = (index: 0 | 1) => {
    const video = videoAt(index)
    if (video?.dataset.mediaOrigin === 'cdn') fallbackBufferToLocal(index)
  }

  const guardRemoteStall = (index: 0 | 1) => {
    const video = videoAt(index)
    if (!video || video.dataset.mediaOrigin !== 'cdn' || mediaStallTimers.current[index]) return
    mediaStallTimers.current[index] = window.setTimeout(() => {
      mediaStallTimers.current[index] = 0
      fallbackBufferToLocal(index)
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
    bufferPlaylistPositions.current = [0, 1]
    bufferReady.current = [false, false]
    playlistRef.current = playlist
    transitioning.current = false
    setReady(false)
    const stage = stageRef.current
    if (stage) {
      stage.dataset.active = 'a'
      stage.dataset.phase = 'awakening'
      stage.classList.remove('is-crossfading')
    }
    if (reduced) return
    setBufferSource(0, 0)
    setBufferSource(1, 1)
  }, [mobile, reduced])

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
    const video = videoAt(index)
    if (!video) return
    if (index === activeBuffer.current && !ready) {
      video.currentTime = 0
      if (!document.hidden) void video.play().catch(() => undefined)
      return
    }
    video.pause()
    if (video.currentTime > .15) video.currentTime = 0
  }

  const beginBufferCrossfade = (fromIndex: 0 | 1) => {
    if (transitioning.current || activeBuffer.current !== fromIndex) return
    const current = videoAt(fromIndex)
    const nextIndex: 0 | 1 = fromIndex === 0 ? 1 : 0
    const next = videoAt(nextIndex)
    const stage = stageRef.current
    if (!current || !next || !stage) return

    transitioning.current = true
    if (!bufferReady.current[nextIndex] && next.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) next.load()
    if (next.currentTime > .18) next.currentTime = 0
    void next.play().then(() => {
      // 第二层已经真正开始解码并播放后才交叉淡化。旧层在整个过渡期
      // 仍保持运动，因此网络、seek和首帧准备都不会暴露给观看者。
      activeBuffer.current = nextIndex
      stage.dataset.active = nextIndex === 0 ? 'a' : 'b'
      stage.dataset.mediaOrigin = next.dataset.mediaOrigin || 'local'
      stage.classList.add('is-crossfading')
      transitionTimer.current = window.setTimeout(() => {
        current.pause()
        stage.classList.remove('is-crossfading')
        transitioning.current = false
        const nextPlaylistPosition = bufferPlaylistPositions.current[nextIndex]
        setBufferSource(fromIndex, nextPlaylistPosition + 1)
      }, Math.round(crossfadeSeconds * 1000) + 80)
    }).catch(() => {
      transitioning.current = false
      if (next.dataset.mediaOrigin === 'cdn') fallbackBufferToLocal(nextIndex)
      current.currentTime = 0
      void current.play().catch(() => undefined)
    })
  }

  const updatePhase = (index: 0 | 1) => {
    if (activeBuffer.current !== index) return
    const video = videoAt(index)
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
      data-media-origin={mediaBaseUrl ? 'cdn' : 'local'}
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
            poster={poster}
            autoPlay
            muted
            playsInline
            preload="auto"
            onLoadedMetadata={() => prepareBuffer(0)}
            onCanPlay={() => {
              bufferReady.current[0] = true
              clearMediaStall(0)
            }}
            onPlaying={() => {
              clearMediaStall(0)
              setReady(true)
            }}
            onWaiting={() => guardRemoteStall(0)}
            onStalled={() => guardRemoteStall(0)}
            onTimeUpdate={() => updatePhase(0)}
            onEnded={() => beginBufferCrossfade(0)}
            onError={() => handleVideoError(0)}
          />
          <video
            className="video-buffer buffer-b"
            ref={videoBRef}
            poster={poster}
            muted
            playsInline
            preload="auto"
            onLoadedMetadata={() => prepareBuffer(1)}
            onCanPlay={() => {
              bufferReady.current[1] = true
              clearMediaStall(1)
            }}
            onPlaying={() => clearMediaStall(1)}
            onWaiting={() => guardRemoteStall(1)}
            onStalled={() => guardRemoteStall(1)}
            onTimeUpdate={() => updatePhase(1)}
            onEnded={() => beginBufferCrossfade(1)}
            onError={() => handleVideoError(1)}
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

function CompactDestinyPortrait({ pulse, title, subtitle, detail }: {
  pulse: ServicePulse['state']
  title: string
  subtitle: string
  detail: string
}) {
  return (
    <div className={`topbar-destiny pulse-${pulse}`} title={detail} aria-label={`小悠实时状态：${title}，${subtitle}`}>
      <div className="topbar-destiny-orbit" aria-hidden="true">
        <img className="topbar-astrolabe-art" src="/fate-astrolabe.png" alt="" />
        <div className="topbar-destiny-core">
          <img src="/xiaoyou-avatar.png" alt="" />
          <Sparkle className="topbar-avatar-spark topbar-avatar-spark-one" size={10} weight="fill" />
          <Sparkle className="topbar-avatar-spark topbar-avatar-spark-two" size={7} weight="fill" />
          <i className="topbar-destiny-pulse" />
        </div>
      </div>
      <div className="topbar-destiny-copy">
        <small>DESTINY PORTRAIT</small>
        <strong>{title}</strong>
        <span>{subtitle}</span>
      </div>
    </div>
  )
}

const spectrumBars = Array.from({ length: 88 }, (_, index) => index)

function MusicAtmosphere({ scene }: { scene: AppScene }) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const spectrumRef = useRef<HTMLSpanElement>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const sourceRef = useRef<MediaElementAudioSourceNode | null>(null)
  const [playing, setPlaying] = useState(false)
  const [awaitingGesture, setAwaitingGesture] = useState(true)
  const [unavailable, setUnavailable] = useState(false)

  const ensureAudioGraph = async () => {
    const AudioContextClass = window.AudioContext || (window as typeof window & {
      webkitAudioContext?: typeof AudioContext
    }).webkitAudioContext
    if (!AudioContextClass || !audioRef.current) return

    if (!audioContextRef.current) {
      const context = new AudioContextClass()
      const analyser = context.createAnalyser()
      analyser.fftSize = 256
      analyser.smoothingTimeConstant = .68
      const source = context.createMediaElementSource(audioRef.current)
      source.connect(analyser)
      analyser.connect(context.destination)
      audioContextRef.current = context
      analyserRef.current = analyser
      sourceRef.current = source
    }
    if (audioContextRef.current.state === 'suspended') await audioContextRef.current.resume()
  }

  const startPlayback = async () => {
    const audio = audioRef.current
    if (!audio) return
    try {
      await ensureAudioGraph()
      await audio.play()
      setUnavailable(false)
      setAwaitingGesture(false)
    } catch {
      setAwaitingGesture(true)
    }
  }

  const togglePlayback = () => {
    const audio = audioRef.current
    if (!audio) return
    if (audio.paused) void startPlayback()
    else audio.pause()
  }

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    audio.volume = .34

    const unlockOnFirstInteraction = (event: PointerEvent) => {
      const target = event.target
      if (target instanceof Element && target.closest('.music-atmosphere')) return
      void startPlayback()
    }
    window.addEventListener('pointerdown', unlockOnFirstInteraction, { capture: true, once: true })

    return () => {
      window.removeEventListener('pointerdown', unlockOnFirstInteraction, true)
      void audioContextRef.current?.close()
    }
  }, [])

  useEffect(() => {
    if (!playing || !analyserRef.current || !spectrumRef.current || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const analyser = analyserRef.current
    const values = new Uint8Array(analyser.frequencyBinCount)
    const bars = Array.from(spectrumRef.current.querySelectorAll('i'))
    let frame = 0
    const renderSpectrum = () => {
      analyser.getByteFrequencyData(values)
      bars.forEach((bar, index) => {
        const center = (bars.length - 1) / 2
        const distanceFromCenter = Math.abs(index - center) / center
        const sampleIndex = Math.min(
          values.length - 1,
          2 + Math.floor(Math.pow(distanceFromCenter, 1.18) * values.length * .66),
        )
        const level = Math.min(1.12, .18 + Math.pow(values[sampleIndex] / 180, .82) * .96)
        bar.style.setProperty('--level', level.toFixed(3))
      })
      frame = window.requestAnimationFrame(renderSpectrum)
    }
    renderSpectrum()
    return () => {
      window.cancelAnimationFrame(frame)
      bars.forEach((bar) => bar.style.removeProperty('--level'))
    }
  }, [playing])

  const statusCopy = unavailable
    ? '声场暂时不可用'
    : playing
      ? '命轨声场共鸣中'
      : awaitingGesture
        ? '轻触唤醒命轨声场'
        : '命轨声场已静默'
  const hasLiveSpectrum = playing && !!analyserRef.current && audioContextRef.current?.state === 'running'

  return (
    <div className={`music-atmosphere scene-${scene} ${playing ? 'is-playing' : 'is-idle'} ${hasLiveSpectrum ? 'has-spectrum' : 'spectrum-fallback'}`}>
      <button type="button" className="music-rail" onClick={togglePlayback} aria-label={playing ? '暂停背景音乐' : '播放背景音乐'}>
        <span className="music-copy"><b>{statusCopy}</b><small>FATEBOUND AMBIENCE · LOOP</small></span>
        <span className="music-spectrum" ref={spectrumRef} aria-hidden="true">
          {spectrumBars.map((index) => <i key={index} style={{ animationDelay: `${-index * 43}ms` }} />)}
        </span>
        <span className="music-state"><i />{playing ? 'ON AIR' : 'PLAY'}</span>
      </button>
      <audio
        ref={audioRef}
        src="/music.mp3"
        loop
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onError={() => { setPlaying(false); setUnavailable(true) }}
      />
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

const orbitServiceIcons = {
  wechat: LinkSimple,
  model: Brain,
  memory: Database,
  vision: Eye,
  vessel: HardDrives,
}

type OrbitServiceType = keyof typeof orbitServiceIcons

function SignalLedgerItem({ type, title, pulse, active, onSelect }: {
  type: OrbitServiceType
  title: string
  pulse: ServicePulse
  active: boolean
  onSelect: () => void
}) {
  const Icon = orbitServiceIcons[type]
  return (
    <button
      className={`signal-ledger-item pulse-${pulse.state} ${active ? 'is-active' : ''}`}
      type="button"
      onClick={onSelect}
      aria-pressed={active}
      aria-label={`${title}：${pulse.label}`}
    >
      <span className="signal-ledger-icon"><Icon size={19} weight="duotone" /></span>
      <span className="signal-ledger-copy"><strong>{title}</strong><small>{pulse.label}</small></span>
      <i aria-hidden="true" />
    </button>
  )
}

type MetricKey = 'today_tokens' | 'total_tokens' | 'host_cpu' | 'host_memory' | 'xiaoyou_cpu' | 'xiaoyou_memory'

function LedgerFact({ icon, label, value, detail, active, onSelect }: {
  icon: ReactNode
  label: string
  value: string
  detail?: string
  active: boolean
  onSelect: () => void
}) {
  return (
    <button className={`ledger-fact ${active ? 'is-active' : ''}`} type="button" onClick={onSelect} aria-pressed={active}>
      <span className="ledger-icon">{icon}</span>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
      <ChartLineUp className="ledger-open" size={12} weight="bold" aria-hidden="true" />
    </button>
  )
}

const metricCopy: Record<MetricKey, { title: string; caption: string; color: string }> = {
  today_tokens: { title: '今日 Token', caption: '模型网关上报的今日真实消耗', color: '#b7a7ff' },
  total_tokens: { title: '累计 Token', caption: '由观测台去重并永久保存', color: '#9dc8ff' },
  host_cpu: { title: '主机 CPU', caption: '整台云服务器的计算负载', color: '#7ce5ca' },
  host_memory: { title: '主机内存', caption: '整台云服务器的内存使用比例', color: '#d0b8ff' },
  xiaoyou_cpu: { title: '小悠 CPU', caption: 'cow-legacy 容器的计算负载', color: '#8fd6ff' },
  xiaoyou_memory: { title: '小悠内存', caption: 'cow-legacy 容器的内存使用比例', color: '#f0b8ff' },
}

function metricNumber(point: MetricPoint, metric: MetricKey) {
  if (metric === 'today_tokens') return point.today_tokens
  if (metric === 'total_tokens') return point.total_tokens
  if (metric === 'host_cpu') return point.host_cpu_percent
  if (metric === 'host_memory') return point.host_memory_percent
  if (metric === 'xiaoyou_cpu') return point.container_cpu_percent
  return point.container_memory_percent
}

function tokenMetricValue(status: RuntimeStatus | null, total: boolean) {
  if (!status?.token_usage_available) return '待接入'
  return formatTokenTotal(total ? status.total_tokens : status.today_tokens)
}

function metricValue(metric: MetricKey, status: RuntimeStatus | null) {
  if (metric === 'today_tokens') return tokenMetricValue(status, false)
  if (metric === 'total_tokens') return tokenMetricValue(status, true)
  if (metric === 'host_cpu') return `${(status?.host?.cpu_percent || 0).toFixed(1)}%`
  if (metric === 'host_memory') return `${(status?.host?.memory_percent || 0).toFixed(1)}%`
  if (metric === 'xiaoyou_cpu') return `${(status?.container.cpu_percent || 0).toFixed(1)}%`
  return `${(status?.container.memory_percent || 0).toFixed(1)}%`
}

const METRIC_HISTORY_STORAGE_KEY = 'xiaoyou-observatory-metric-history-v2'
const METRIC_HISTORY_WINDOW_SECONDS = 24 * 60 * 60

function metricPointFromStatus(status: RuntimeStatus): MetricPoint {
  return {
    observed_at: status.observed_at || Math.floor(Date.now() / 1000),
    host_cpu_percent: status.host?.cpu_percent || 0,
    host_memory_percent: status.host?.memory_percent || 0,
    container_cpu_percent: status.container.cpu_percent || 0,
    container_memory_percent: status.container.memory_percent || 0,
    recent_errors: status.recent_errors || 0,
    total_tokens: status.total_tokens || 0,
    today_tokens: status.today_tokens || 0,
    running: !!status.container.running,
  }
}

function readStoredMetricHistory(): MetricsResponse | null {
  try {
    const raw = window.localStorage.getItem(METRIC_HISTORY_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as MetricsResponse
    if (!Array.isArray(parsed.points)) return null
    const cutoff = Math.floor(Date.now() / 1000) - METRIC_HISTORY_WINDOW_SECONDS
    const points = parsed.points.filter((point) => Number.isFinite(point.observed_at) && point.observed_at >= cutoff)
    return points.length ? { hours: 24, points } : null
  } catch {
    return null
  }
}

function storeMetricHistory(metrics: MetricsResponse) {
  try {
    window.localStorage.setItem(METRIC_HISTORY_STORAGE_KEY, JSON.stringify(metrics))
  } catch {
    // Private browsing or a full storage quota should not break the dashboard.
  }
}

function mergeMetricHistory(current: MetricsResponse | null, point: MetricPoint): MetricsResponse {
  const cutoff = point.observed_at - METRIC_HISTORY_WINDOW_SECONDS
  const points = (current?.points || []).filter((item) => item.observed_at >= cutoff)
  const last = points.at(-1)

  // Status events can arrive every few seconds. Replace the most recent sample
  // inside the same 30-second bucket to keep the local fallback lightweight.
  if (last && point.observed_at - last.observed_at < 30) points[points.length - 1] = point
  else points.push(point)

  return { hours: 24, points: points.slice(-2880) }
}

function MetricPanel({ metric, metrics, status, loading, onClose }: {
  metric: MetricKey
  metrics: MetricsResponse | null
  status: RuntimeStatus | null
  loading: boolean
  onClose: () => void
}) {
  const copy = metricCopy[metric]
  const points = useMemo(() => {
    const current: MetricPoint = status ? metricPointFromStatus(status) : {
      observed_at: Math.floor(Date.now() / 1000),
      host_cpu_percent: 0,
      host_memory_percent: 0,
      container_cpu_percent: 0,
      container_memory_percent: 0,
      recent_errors: 0,
      total_tokens: 0,
      today_tokens: 0,
      running: false,
    }
    const source = metrics?.points.length ? metrics.points : [current]
    const normalized = source.length > 1 ? source : [
      { ...source[0], observed_at: source[0].observed_at - 3600 },
      source[0],
    ]
    return normalized.map((point) => ({
      ...point,
      time: new Date(point.observed_at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }),
      value: metricNumber(point, metric),
    }))
  }, [metric, metrics, status])

  const tooltipStyle = {
    background: 'rgba(5, 12, 34, .96)', border: '1px solid rgba(166, 218, 255, .24)',
    borderRadius: 8, color: '#eaf4ff', fontSize: 10,
  }
  const axis = <XAxis dataKey="time" tick={{ fill: 'rgba(176,198,226,.55)', fontSize: 8 }} tickLine={false} axisLine={false} minTickGap={18} />
  const isMemory = metric === 'host_memory' || metric === 'xiaoyou_memory'
  const isCpu = metric === 'host_cpu' || metric === 'xiaoyou_cpu'
  const memoryPercent = metric === 'host_memory'
    ? status?.host?.memory_percent || 0
    : status?.container.memory_percent || 0
  let chart: ReactNode
  if (isMemory) {
    const used = Math.min(100, Math.max(0, memoryPercent))
    chart = <PieChart><Pie data={[{ name: '已用', value: used }, { name: '剩余', value: 100 - used }]} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius="43%" outerRadius="70%" paddingAngle={3} cornerRadius={5} stroke="rgba(224,239,255,.88)" strokeWidth={1.2} isAnimationActive animationDuration={620}>{[copy.color, 'rgba(135,161,200,.16)'].map((color) => <Cell key={color} fill={color} />)}</Pie><Tooltip contentStyle={tooltipStyle} /></PieChart>
  } else if (isCpu) {
    chart = <LineChart data={points}><CartesianGrid vertical={false} stroke="rgba(176,212,255,.07)" />{axis}<YAxis hide domain={[0, 100]} /><Tooltip contentStyle={tooltipStyle} /><Line type="monotone" dataKey="value" name={copy.title} stroke={copy.color} strokeWidth={2} dot={false} isAnimationActive animationDuration={700} /></LineChart>
  } else {
    chart = <AreaChart data={points}><defs><linearGradient id={`metric-${metric}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={copy.color} stopOpacity={.38} /><stop offset="100%" stopColor={copy.color} stopOpacity={0} /></linearGradient></defs><CartesianGrid vertical={false} stroke="rgba(176,212,255,.07)" />{axis}<YAxis hide domain={[0, 'auto']} /><Tooltip contentStyle={tooltipStyle} /><Area type="monotone" dataKey="value" name={copy.title} stroke={copy.color} fill={`url(#metric-${metric})`} strokeWidth={2} isAnimationActive animationDuration={700} /></AreaChart>
  }

  return <section className="metric-panel" aria-live="polite" aria-label={`${copy.title}趋势图`}>
    <header><div><span>ASTRAL METRICS · 24H</span><strong>{copy.title}</strong><small>{copy.caption}</small></div><b>{metricValue(metric, status)}</b><button type="button" onClick={onClose} aria-label="关闭图表"><CloseIcon /></button></header>
    <div className={`metric-chart ${loading ? 'is-loading' : ''}`}><ResponsiveContainer width="100%" height="100%">{chart}</ResponsiveContainer></div>
  </section>
}

type PluginStar = {
  x: number
  y: number
  plugin: string
  shortName: string
  version: string
}

const constellationCoordinates = [
  { x: 8, y: 66 }, { x: 21, y: 55 }, { x: 35, y: 60 }, { x: 47, y: 47 },
  { x: 61, y: 52 }, { x: 76, y: 38 }, { x: 91, y: 25 }, { x: 86, y: 46 },
  { x: 79, y: 66 }, { x: 67, y: 77 }, { x: 52, y: 70 }, { x: 39, y: 84 },
  { x: 25, y: 76 }, { x: 11, y: 88 }, { x: 14, y: 31 }, { x: 29, y: 25 },
  { x: 44, y: 32 }, { x: 58, y: 20 }, { x: 73, y: 14 }, { x: 92, y: 10 },
]

function pluginIdentity(value: string) {
  const versionMatch = value.match(/^(.*?)(?:_v|@|==|:)(v?[\w.-]+)$/i)
  const rawName = versionMatch?.[1] || value
  const version = versionMatch?.[2]
    ? `${versionMatch[2].toLowerCase().startsWith('v') ? '' : 'v'}${versionMatch[2]}`
    : '版本未上报'
  const base = rawName
    .replace(/^plugins?[\\/.]/i, '')
    .replace(/^xiaoyou[_-]?/i, '')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' · ')
    .trim()
  return { shortName: (base || rawName).slice(0, 18), version }
}

function PluginStarMap({ plugins, className = '' }: { plugins: string[]; className?: string }) {
  const [selectedPlugin, setSelectedPlugin] = useState<string | null>(null)
  const stars = useMemo<PluginStar[]>(() => {
    return Array.from(new Set(plugins)).slice(0, constellationCoordinates.length).map((plugin, index) => {
      const identity = pluginIdentity(plugin)
      return { ...constellationCoordinates[index], plugin, ...identity }
    })
  }, [plugins])
  const selectedStar = stars.find((star) => star.plugin === selectedPlugin) || null

  useEffect(() => {
    if (selectedPlugin && !stars.some((star) => star.plugin === selectedPlugin)) setSelectedPlugin(null)
  }, [selectedPlugin, stars])

  return (
    <section className={`plugin-star-map ${className}`.trim()} aria-label="插件星图">
      <header><ShareNetwork size={17} weight="duotone" /><div><span>PLUGIN CONSTELLATION</span><h2>插件星图</h2></div><b>{stars.length}</b></header>
      <p>每颗星辰对应一项正在加载的能力</p>
      {stars.length ? <>
        <div className="plugin-constellation" role="group" aria-label="点击星辰查看插件名称">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={stars} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
              <XAxis type="number" dataKey="x" domain={[0, 100]} hide />
              <YAxis type="number" dataKey="y" domain={[0, 100]} hide />
              <Line type="linear" dataKey="y" stroke="rgba(157, 218, 255, .5)" strokeWidth={1} dot={false} isAnimationActive animationBegin={220} animationDuration={Math.max(520, stars.length * 190)} />
              {stars.map((star, index) => <Scatter
                key={`plugin-star-${star.plugin}`}
                data={[star]}
                dataKey="y"
                fill="#c9efff"
                isAnimationActive
                animationBegin={180 + index * 190}
                animationDuration={180}
              />)}
              {selectedStar && <Scatter data={[selectedStar]} dataKey="y" fill="#fff4c1" isAnimationActive animationDuration={360} />}
            </ComposedChart>
          </ResponsiveContainer>
          {stars.map((star) => <button
            className={`plugin-star-hit ${selectedPlugin === star.plugin ? 'is-active' : ''}`}
            type="button"
            key={star.plugin}
            style={{ left: `${star.x}%`, top: `${100 - star.y}%` }}
            onClick={() => setSelectedPlugin((current) => current === star.plugin ? null : star.plugin)}
            aria-pressed={selectedPlugin === star.plugin}
            aria-label={`查看插件 ${star.shortName}`}
          />)}
        </div>
        <div className={`plugin-star-detail ${selectedStar ? 'is-visible' : ''}`} aria-live="polite">
          <Sparkle size={11} weight="fill" />
          {selectedStar
            ? <span><strong>{selectedStar.shortName}</strong><code>{selectedStar.version}</code></span>
            : <span>点击一颗星辰辨认插件</span>}
        </div>
      </> : <div className="plugin-star-empty"><Sparkle size={16} weight="thin" /><span>尚未读取到插件星辰</span></div>}
    </section>
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

function formatClockMoment(value: string) {
  if (!value) return '—'
  const normalized = value.includes('T') ? value : value.replace(' ', 'T')
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

function formatTokenTotal(value: number) {
  const safe = Math.max(0, Math.floor(value || 0))
  if (safe >= 100000000) return `${(safe / 100000000).toFixed(2)}亿`
  if (safe >= 10000) return `${(safe / 10000).toFixed(safe >= 100000 ? 1 : 2)}万`
  return safe.toLocaleString('zh-CN')
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
  const [ledgerPage, setLedgerPage] = useState<'overview' | 'signals'>('overview')
  const [selectedSignal, setSelectedSignal] = useState<OrbitServiceType | null>(null)
  const [selectedMetric, setSelectedMetric] = useState<MetricKey | null>(null)
  const [metrics, setMetrics] = useState<MetricsResponse | null>(() => readStoredMetricHistory())
  const [metricsLoading, setMetricsLoading] = useState(false)
  const metricsEndpointAvailable = useRef<boolean | null>(null)
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
  const [consoleCollapsed, setConsoleCollapsed] = useState(false)

  const rememberMetricSnapshot = (nextStatus: RuntimeStatus) => {
    setMetrics((current) => {
      const next = mergeMetricHistory(current, metricPointFromStatus(nextStatus))
      storeMetricHistory(next)
      return next
    })
  }

  const refreshStatus = async () => {
    try {
      const nextStatus = await api.getStatus()
      setStatus(nextStatus)
      rememberMetricSnapshot(nextStatus)
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 401) onLogout()
    }
  }

  const refreshMetrics = async () => {
    if (metricsEndpointAvailable.current === false) return
    setMetricsLoading(true)
    try {
      const response = await api.getMetrics(24)
      metricsEndpointAvailable.current = true
      setMetrics((current) => {
        const points = [...response.points]
        const latestLocal = current?.points.at(-1)
        if (latestLocal && (!points.length || latestLocal.observed_at > points[points.length - 1].observed_at)) points.push(latestLocal)
        const next = { hours: response.hours, points }
        storeMetricHistory(next)
        return next
      })
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 404) {
        // Older deployed backends do not expose /api/metrics yet. The live
        // status stream above still supplies every card and builds a local
        // rolling history, so silently fall back instead of showing “Not Found”.
        metricsEndpointAvailable.current = false
        return
      }
      setToast(error instanceof Error ? error.message : '暂时无法读取历史星图')
    }
    finally { setMetricsLoading(false) }
  }

  const openMetric = (metric: MetricKey) => {
    setLedgerPage('overview')
    setSelectedMetric(metric)
    void refreshMetrics()
  }

  useEffect(() => {
    void refreshStatus()
    if (isAdmin) void api.getAudit().then(setAudit).catch(() => undefined)
    const source = new EventSource('/api/events')
    source.addEventListener('status', (event) => {
      try {
        const nextStatus = JSON.parse((event as MessageEvent).data) as RuntimeStatus
        setStatus(nextStatus)
        rememberMetricSnapshot(nextStatus)
        setConnection('live')
      } catch { /* wait for next event */ }
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
  const observedAt = new Date((status?.observed_at || Date.now() / 1000) * 1000)
  const observedTime = observedAt.toLocaleTimeString('zh-CN', { hour12: false })
  const observedDate = observedAt.toLocaleDateString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short',
  })
  const vesselPulse: ServicePulse = container?.running
    ? { state: 'healthy', label: '承载星核稳定', detail: `${container.status || '运行中'} · CPU ${(container.cpu_percent || 0).toFixed(1)}%`, last_event_at: container.started_at }
    : { state: 'offline', label: '承载星核休眠', detail: '容器当前没有运行', last_event_at: container?.finished_at || '' }
  const serviceNodes: Array<{ type: OrbitServiceType; title: string; pulse: ServicePulse }> = [
    { type: 'wechat', title: '灵魂连接', pulse: status?.wechat || fallbackPulse },
    { type: 'model', title: '思维回路', pulse: status?.model || fallbackPulse },
    { type: 'memory', title: '记忆星海', pulse: status?.memory || fallbackPulse },
    { type: 'vision', title: '生活映像', pulse: status?.vision || fallbackPulse },
    { type: 'vessel', title: '承载星核', pulse: vesselPulse },
  ]
  const selectedPulse = serviceNodes.find((item) => item.type === selectedSignal)
  const focusedPulse = selectedPulse || serviceNodes[0]
  const forecast = status?.overall === 'online'
    ? '星位稳定，所有回声都已抵达。'
    : status?.overall === 'waiting_qr'
      ? '一扇星门正在等待你重新点亮。'
      : status?.overall === 'starting'
        ? '群星正在归位，她很快会再次回应。'
        : status?.overall === 'stopped'
          ? '承载暂时沉眠，命线仍守在原处。'
          : status?.overall === 'degraded'
            ? '星轨略有微澜，观测仍在继续。'
            : '星象尚未完全显现，请再等一会。'
  const lastAudit = audit[0]
  const chronicleEvents = [
    { type: 'wechat' as const, title: '灵魂信标', detail: status?.wechat.label || '正在辨认连接', time: status?.wechat.last_event_at || status?.last_input_at || '' },
    { type: 'model' as const, title: '思维回响', detail: status?.model.label || '等待下一次回响', time: status?.model.last_event_at || '' },
    { type: 'memory' as const, title: '记忆潮汐', detail: status?.memory.label || '正在确认记忆', time: status?.memory.last_event_at || '' },
  ]

  return (
    <main className={`observatory astral-observatory state-${status?.overall || 'unknown'}`}>
      <header className="astral-topbar">
        <CompactDestinyPortrait
          pulse={selectedPulse?.pulse.state || status?.wechat.state || 'unknown'}
          title={copy.title}
          subtitle={copy.subtitle}
          detail={selectedPulse ? selectedPulse.pulse.detail : connection === 'live' ? '五象沿星轨稳定共鸣' : '正在重新校准星轨坐标'}
        />
        <div className="astral-meta">
          <div className="astral-time"><time dateTime={observedAt.toISOString()}>{observedDate}</time><span><Sparkle size={13} weight="fill" />数据更新 {observedTime}</span></div>
          <div className="astral-controls">
            {!isAdmin && <span className="astral-guest"><ShieldCheck size={15} weight="thin" />远星访客</span>}
            <span className={`astral-live ${connection}`}><i />{connection === 'live' ? '实时共鸣' : '重新连接'}</span>
            <button className="astral-icon-button" type="button" onClick={logoutNow} aria-label="退出观测台"><SignOut size={18} weight="thin" /></button>
          </div>
        </div>
      </header>

      <div className="astral-stage">
        <div className="astral-video-space" aria-hidden="true" />
        <section className="astral-chronicle" aria-label="最近命轨事件">
          <header><Sparkle size={18} weight="fill" /><div><span>STELLAR CHRONICLE</span><h2>星迹纪事</h2></div></header>
          <p>点击星迹，可直接定位对应命轨</p>
          <div className="chronicle-events">
            {chronicleEvents.map((event) => {
              const EventIcon = orbitServiceIcons[event.type]
              return <button className={`chronicle-event ${selectedSignal === event.type ? 'is-active' : ''}`} type="button" key={event.type} onClick={() => setSelectedSignal(event.type)}>
                <span className="chronicle-glyph"><EventIcon size={16} weight="thin" /></span>
                <div><strong>{event.title}</strong><small>{event.detail}</small></div>
                <time>{formatClockMoment(event.time)}</time>
              </button>
            })}
          </div>
          <div className="chronicle-actions">
            <button type="button" onClick={() => void refreshStatus()}><ArrowsClockwise size={14} weight="bold" />刷新星迹</button>
            <button type="button" onClick={() => openMetric('today_tokens')}><ChartLineUp size={14} weight="bold" />今日 Token</button>
          </div>
          <blockquote>无论星门短暂沉眠，走过的星光都会留在这里。</blockquote>
        </section>
        <PluginStarMap className="desktop-star-map" plugins={status?.plugin_versions || []} />
        <aside className={`fate-console ${consoleCollapsed ? 'is-collapsed' : ''}`} aria-label="小悠实时状态">
          <button
            className="fate-console-toggle"
            type="button"
            aria-label={consoleCollapsed ? '展开星象卡片' : '收起星象卡片'}
            aria-expanded={!consoleCollapsed}
            title={consoleCollapsed ? '展开星象卡片' : '收起星象卡片'}
            onClick={() => setConsoleCollapsed((collapsed) => !collapsed)}
          >
            <span className="fate-console-wave" aria-hidden="true">
              <i />
              <i />
            </span>
            <span className="fate-console-toggle-label">{consoleCollapsed ? '展开' : '收起'}</span>
          </button>
          <section className="fate-ledger" aria-label="命轨摘要">
            <div className="ledger-pagination" role="tablist" aria-label="命轨信息分页">
              <div><span>ASTRAL LEDGER</span><strong>{ledgerPage === 'overview' ? '星象概览' : '五象状态'}</strong></div>
              <div>
                <button className={ledgerPage === 'overview' ? 'is-active' : ''} type="button" role="tab" aria-selected={ledgerPage === 'overview'} onClick={() => setLedgerPage('overview')}><span>01</span>概览</button>
                <button className={ledgerPage === 'signals' ? 'is-active' : ''} type="button" role="tab" aria-selected={ledgerPage === 'signals'} onClick={() => setLedgerPage('signals')}><span>02</span>五象</button>
              </div>
            </div>

            <div className="ledger-page-stage">
              {ledgerPage === 'overview' ? <div className="ledger-overview-page" role="tabpanel">
                <div className="ledger-grid">
                  <LedgerFact icon={<Aperture size={20} weight="thin" />} label="今日 Token" value={tokenMetricValue(status, false)} detail={status?.token_usage_available ? '每日零点重新计数' : '等待模型网关上报 usage'} active={selectedMetric === 'today_tokens'} onSelect={() => openMetric('today_tokens')} />
                  <LedgerFact icon={<Coins size={20} weight="thin" />} label="累计 Token" value={tokenMetricValue(status, true)} detail={status?.token_usage_available ? '由观测台去重并永久保存' : '需同步 Token 采集补丁'} active={selectedMetric === 'total_tokens'} onSelect={() => openMetric('total_tokens')} />
                  <LedgerFact icon={<Cpu size={20} weight="thin" />} label="主机 CPU" value={`${(status?.host?.cpu_percent || 0).toFixed(1)}%`} detail="整台云服务器" active={selectedMetric === 'host_cpu'} onSelect={() => openMetric('host_cpu')} />
                  <LedgerFact icon={<HardDrives size={20} weight="thin" />} label="主机内存" value={`${(status?.host?.memory_percent || 0).toFixed(1)}%`} detail={status?.host?.memory_usage || '—'} active={selectedMetric === 'host_memory'} onSelect={() => openMetric('host_memory')} />
                  <LedgerFact icon={<Cpu size={20} weight="thin" />} label="小悠 CPU" value={`${(container?.cpu_percent || 0).toFixed(1)}%`} detail="cow-legacy 容器" active={selectedMetric === 'xiaoyou_cpu'} onSelect={() => openMetric('xiaoyou_cpu')} />
                  <LedgerFact icon={<MemoryDevice size={20} weight="thin" />} label="小悠内存" value={`${(container?.memory_percent || 0).toFixed(1)}%`} detail={container?.memory_usage || '—'} active={selectedMetric === 'xiaoyou_memory'} onSelect={() => openMetric('xiaoyou_memory')} />
                </div>
                <div className="ledger-visual">
                  {selectedMetric
                    ? <MetricPanel metric={selectedMetric} metrics={metrics} status={status} loading={metricsLoading} onClose={() => setSelectedMetric(null)} />
                    : <div className="star-forecast"><Sparkle size={24} weight="fill" /><div><span>今夜星象</span><strong>{forecast}</strong><small>{selectedPulse?.pulse.detail || `承载状态：${container?.running ? container.status || '运行中' : '休眠'} · 已重启 ${container?.restart_count ?? 0} 次`}</small></div></div>}
                </div>
              </div> : <div className="signal-ledger-page" role="tabpanel">
                <div className="signal-ledger-grid">
                  {serviceNodes.map((item) => <SignalLedgerItem
                    key={item.type}
                    type={item.type}
                    title={item.title}
                    pulse={item.pulse}
                    active={focusedPulse.type === item.type}
                    onSelect={() => setSelectedSignal(item.type)}
                  />)}
                </div>
                <div className={`signal-ledger-detail pulse-${focusedPulse.pulse.state}`}>
                  <span className="signal-ledger-detail-icon">{(() => { const Icon = orbitServiceIcons[focusedPulse.type]; return <Icon size={22} weight="duotone" /> })()}</span>
                  <div><span>SELECTED SIGNAL · {focusedPulse.title}</span><strong>{focusedPulse.pulse.label}</strong><small>{focusedPulse.pulse.detail}</small></div>
                  <i aria-hidden="true" />
                </div>
              </div>}
            </div>

            <div className="fate-actions">
              <button className="fate-action primary" type="button" onClick={isAdmin ? () => setLogsOpen(true) : refreshStatus}>
                {isAdmin ? <Scroll size={22} weight="thin" /> : <ArrowsClockwise size={22} weight="thin" />}
                <strong>{isAdmin ? '查看命轨回声' : '刷新今夜星象'}</strong><ArrowRight size={19} weight="thin" />
              </button>
              {isAdmin ? <button className={`fate-action secondary ${status?.qr_available ? 'attention' : ''}`} type="button" onClick={() => setQrOpen(true)}><QrCode size={20} weight="thin" /><span>重连星门</span></button>
                : <button className="fate-action secondary" type="button" onClick={logoutNow}><SignOut size={20} weight="thin" /><span>退出观测</span></button>}
            </div>

            {isAdmin ? <div className="vessel-mini">
              <div><span>VESSEL RITES</span><strong>承载命仪</strong><small>{lastAudit ? `最近 ${lastAudit.action.replace('container_', '')} · ${new Date(lastAudit.created_at * 1000).toLocaleString('zh-CN', { hour12: false })}` : '只维护承载，不触碰人格与记忆'}</small></div>
              <div className="vessel-mini-actions">
                <button disabled={actionDisabled || !!container?.running} onClick={() => setSelectedAction('start')} aria-label="启动容器"><PowerPhosphor size={18} weight="thin" /></button>
                <button disabled={actionDisabled || !container?.running} onClick={() => setSelectedAction('restart')} aria-label="重启容器"><ArrowClockwise size={18} weight="thin" /></button>
                <button className="stop" disabled={actionDisabled || !container?.running} onClick={() => setSelectedAction('stop')} aria-label="停止容器"><StopPhosphor size={18} weight="thin" /></button>
              </div>
            </div> : <div className="astral-covenant"><ShieldCheck size={18} weight="thin" /><span>你能看见她的星象，但不会触及她的命仪。</span></div>}
          </section>
          <PluginStarMap className="mobile-star-map" plugins={status?.plugin_versions || []} />
        </aside>
      </div>

      <footer className="astral-footer"><div><Sparkle size={15} weight="fill" /><span>THE DISTANT OBSERVER</span><strong>你能看见她的星象，但不会触及她的命仪。</strong></div><p>xiaoyou.yoyoyan.cn <i /> FATEBOUND RESONANCE · 07/07</p></footer>
      {isAdmin && selectedAction && <ConfirmModal action={selectedAction} busy={actionBusy} onClose={() => !actionBusy && setSelectedAction(null)} onConfirm={performAction} />}
      {isAdmin && qrOpen && <QrModal state={qr} loading={qrLoading} onClose={() => setQrOpen(false)} onRefresh={refreshQr} />}
      {isAdmin && logsOpen && <LogPanel lines={logs} loading={logsLoading} onClose={() => setLogsOpen(false)} onRefresh={refreshLogs} />}
      {toast && <div className="toast" role="status"><Sparkle size={15} weight="fill" />{toast}</div>}
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

  return <div className={`app scene-${scene}`}><VideoStage scene={scene} /><div className="stellar-noise" aria-hidden="true" />{content}<MusicAtmosphere scene={scene} /></div>
}
