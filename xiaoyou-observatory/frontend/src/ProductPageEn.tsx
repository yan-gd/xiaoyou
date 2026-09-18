import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import {
  Aperture,
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  BellSimple,
  Brain,
  DeviceMobile,
  ImageSquare,
  Microphone,
  MoonStars,
  ShieldCheck,
  Sparkle,
  Waveform,
} from '@phosphor-icons/react'
import './product.css'

type Feature = {
  eyebrow: string
  title: string
  copy: string
  icon: typeof Brain
  dark?: boolean
}

const features: Feature[] = [
  {
    eyebrow: 'MEMORY / 01',
    title: 'Remember you.',
    copy: 'Important preferences and shared moments naturally return in future conversations.',
    icon: Brain,
  },
  {
    eyebrow: 'PROACTIVE / 02',
    title: 'Reaches out first.',
    copy: 'Sometimes, Xiaoyou chooses to reach out first.',
    icon: BellSimple,
    dark: true,
  },
  {
    eyebrow: 'VOICE / 03',
    title: 'Hear you.',
    copy: 'Voice messages and realtime conversation make talking feel more natural.',
    icon: Microphone,
  },
  {
    eyebrow: 'VISION / 04',
    title: 'See your world.',
    copy: 'She understands what you share and can share moments of her own.',
    icon: ImageSquare,
  },
]

const asset = (name: string) => `/product/${name}`

const VIVO_STORE_URL = 'https://h5.appstore.vivo.com.cn/#/result?keyword=%E5%B0%8F%E6%82%A0&keyfrom=2'
const DIRECT_APK_URL = '/downloads/xiaoyou-latest.apk'
const GITHUB_RELEASES_URL = 'https://github.com/yan-gd/xiaoyou/releases'

function useReveal() {
  useEffect(() => {
    const nodes = [...document.querySelectorAll<HTMLElement>('[data-product-reveal]')]
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return
          entry.target.classList.add('is-visible')
          observer.unobserve(entry.target)
        })
      },
      { threshold: 0.14, rootMargin: '0px 0px -7% 0px' },
    )
    nodes.forEach((node) => observer.observe(node))
    return () => observer.disconnect()
  }, [])
}

function useScrollProgress() {
  const [progress, setProgress] = useState(0)

  useEffect(() => {
    const update = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight
      setProgress(max > 0 ? Math.min(1, window.scrollY / max) : 0)
    }

    update()
    window.addEventListener('scroll', update, { passive: true })
    window.addEventListener('resize', update)
    return () => {
      window.removeEventListener('scroll', update)
      window.removeEventListener('resize', update)
    }
  }, [])

  return progress
}


function ProductNav() {
  const [open, setOpen] = useState(false)

  const scrollToVoice = (
    event: React.MouseEvent<HTMLAnchorElement>,
  ) => {
    event.preventDefault()
    setOpen(false)

    const target =
      document.getElementById('capabilities')

    if (!target) return

    const targetTop =
      target.getBoundingClientRect().top +
      window.scrollY

    window.scrollTo({
      top: targetTop,
      behavior: 'smooth',
    })

    window.history.replaceState(
      null,
      '',
      '#capabilities',
    )
  }
  return (
    <header className="xy-product-nav">
      <a className="xy-product-brand" href="#top">
        <span>Xiaoyou</span>
      </a>

      <nav className={open ? 'is-open' : ''}>
        <a href="#top" onClick={() => setOpen(false)}>Home</a>
        <a href="#capabilities" onClick={scrollToVoice}>Voice</a>
        <a href="#app" onClick={() => setOpen(false)}>App</a>
        <a className="xy-nav-download" href="#download" onClick={() => setOpen(false)}>
          下载
        </a>
      </nav>

      <div className="xy-product-nav-actions">
        <a
          className="xy-language-switch"
          href="/"
          aria-label="切换到中文"
        >
          中文
        </a>
        <a className="xy-observatory-link" href="/observatory">
          Observatory <ArrowUpRight size={15} weight="bold" />
        </a>
        <button
          className="xy-nav-toggle"
          type="button"
          aria-label="Open navigation"
          onClick={() => setOpen((value) => !value)}
        >
          <span /><span />
        </button>
      </div>
    </header>
  )
}

type GridPoint = {
  homeX: number
  homeY: number
  x: number
  y: number
  vx: number
  vy: number
  size: number
  alpha: number
  phase: number
}

type InteractiveGridProps = {
  dark?: boolean
}

function InteractiveGrid({ dark = false }: InteractiveGridProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const host = canvas?.parentElement
    const context = canvas?.getContext('2d')
    if (!canvas || !host || !context) return

    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches
    const coarsePointer = matchMedia('(pointer: coarse)').matches

    const pointer = {
      x: -1000,
      y: -1000,
      lastX: -1000,
      lastY: -1000,
      speedX: 0,
      speedY: 0,
      active: false,
    }

    let points: GridPoint[] = []
    let columns = 0
    let rows = 0
    let width = 0
    let height = 0
    let dpr = 1

    // 当前 section 在整个 document 中的位置。
    // 所有 section 使用同一全局网格坐标系。
    let documentOriginX = 0
    let documentOriginY = 0
    let frame = 0
    let lastFrame = 0
    let visible = true

    // Continuous ambient grid wave.
    // The grid is always moving very slowly, while mouse interaction
    // takes priority near the pointer.
    const waveAmplitude = 9.0
    const waveDirection = 1
    const wavePhase = 0

    const resize = () => {
      const rect = host.getBoundingClientRect()

      width = Math.max(1, rect.width)
      height = Math.max(1, rect.height)
      dpr = Math.min(window.devicePixelRatio || 1, 1.6)

      canvas.width = Math.round(width * dpr)
      canvas.height = Math.round(height * dpr)
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`

      context.setTransform(dpr, 0, 0, dpr, 0, 0)

      const spacing = width < 720 ? 64 : 78

      documentOriginX = window.scrollX + rect.left
      documentOriginY = window.scrollY + rect.top

      const offsetX =
        -(((documentOriginX % spacing) + spacing) % spacing)

      const offsetY =
        -(((documentOriginY % spacing) + spacing) % spacing)

      columns =
        Math.ceil((width - offsetX) / spacing) + 2

      rows =
        Math.ceil((height - offsetY) / spacing) + 2

      points = []

      for (let row = 0; row < rows; row += 1) {
        for (let column = 0; column < columns; column += 1) {
          const homeX =
            offsetX + column * spacing

          const homeY =
            offsetY + row * spacing
          const seed = (column * 17 + row * 31) % 19

          points.push({
            homeX,
            homeY,
            x: homeX,
            y: homeY,
            vx: 0,
            vy: 0,
            size: 1.05 + (seed % 4) * 0.26,
            alpha: 0.1 + (seed / 19) * 0.08,
            phase: seed * 0.47,
          })
        }
      }
    }

    const updatePointer = (event: PointerEvent) => {
      const rect = host.getBoundingClientRect()

      pointer.x = event.clientX - rect.left
      pointer.y = event.clientY - rect.top

      pointer.speedX =
        pointer.lastX > -900 ? event.clientX - pointer.lastX : 0

      pointer.speedY =
        pointer.lastY > -900 ? event.clientY - pointer.lastY : 0

      pointer.lastX = event.clientX
      pointer.lastY = event.clientY
      pointer.active = true
    }

    const clearPointer = () => {
      pointer.active = false
      pointer.lastX = -1000
      pointer.lastY = -1000
    }

    const movePoint = (
      point: GridPoint,
      waveEnvelope: number,
      waveProgress: number,
    ) => {
      if (pointer.active) {
        const dx = point.x - pointer.x
        const dy = point.y - pointer.y
        const distanceSquared = dx * dx + dy * dy

        const radius = 158

        if (distanceSquared < radius * radius && distanceSquared > 1) {
          const distance = Math.sqrt(distanceSquared)
          const influence = 1 - distance / radius
          const force = influence * influence * 2.5

          point.vx +=
            (dx / distance) * force +
            pointer.speedX * 0.018 * influence

          point.vy +=
            (dy / distance) * force +
            pointer.speedY * 0.018 * influence

          const direction =
            Math.sign(pointer.speedX + pointer.speedY) || 1

          point.vx +=
            (-dy / distance) *
            force *
            0.065 *
            direction

          point.vy +=
            (dx / distance) *
            force *
            0.065 *
            direction
        }
      }

      let ambientX = 0
      let ambientY = 0

      if (waveEnvelope > 0) {
        // Large wavelength = very soft deformation instead of a water ripple.
        const spatialPhase =
          (point.homeX + documentOriginX) *
            0.0062 *
            waveDirection +
          (point.homeY + documentOriginY) *
            0.0042 +
          wavePhase

        const temporalPhase =
          waveProgress * Math.PI * 2

        const primaryWave =
          Math.sin(spatialPhase - temporalPhase)

        const secondaryWave =
          Math.sin(
            spatialPhase * 0.57 +
            temporalPhase * 0.68 +
            1.4,
          )

        ambientX =
          primaryWave *
          waveAmplitude *
          0.48 *
          waveEnvelope

        ambientY =
          (
            primaryWave * 0.76 +
            secondaryWave * 0.24
          ) *
          waveAmplitude *
          waveEnvelope

        if (pointer.active) {
          const pointerDistance = Math.hypot(
            point.x - pointer.x,
            point.y - pointer.y,
          )

          // Mouse area owns the motion completely.
          // From 90px to 240px the ambient wave gradually returns.
          const mousePriority = Math.min(
            1,
            Math.max(
              0,
              (pointerDistance - 90) / 150,
            ),
          )

          ambientX *= mousePriority
          ambientY *= mousePriority
        }
      }

      point.vx +=
        (
          point.homeX +
          ambientX -
          point.x
        ) * 0.02

      point.vy +=
        (
          point.homeY +
          ambientY -
          point.y
        ) * 0.02

      point.vx *= 0.89
      point.vy *= 0.89

      point.x += point.vx
      point.y += point.vy
    }

    const draw = (time: number) => {
      if (!visible) {
        frame = requestAnimationFrame(draw)
        return
      }

      if (time - lastFrame < 30) {
        frame = requestAnimationFrame(draw)
        return
      }

      lastFrame = time

      context.clearRect(0, 0, width, height)

      // Continuous, slow ambient motion.
      // One spatial cycle takes roughly 14 seconds.
      const waveProgress =
        reducedMotion
          ? 0
          : (time % 16000) / 16000

      // Keep the wave alive at all times, but let its strength breathe
      // very slowly so it never feels mechanical.
      const waveEnvelope =
        reducedMotion
          ? 0
          : 0.82 +
            Math.sin(time * 0.00018 + wavePhase) * 0.18

      if (pointer.active) {
        const radius = 150

        const glow = context.createRadialGradient(
          pointer.x,
          pointer.y,
          0,
          pointer.x,
          pointer.y,
          radius,
        )

        if (dark) {
          glow.addColorStop(0, 'rgba(255,255,255,.075)')
          glow.addColorStop(.46, 'rgba(170,185,230,.035)')
        } else {
          glow.addColorStop(0, 'rgba(175,188,220,.105)')
          glow.addColorStop(.46, 'rgba(230,205,214,.055)')
        }

        glow.addColorStop(1, 'rgba(255,255,255,0)')

        context.fillStyle = glow

        context.fillRect(
          pointer.x - radius,
          pointer.y - radius,
          radius * 2,
          radius * 2,
        )
      }

      points.forEach((point) => {
        movePoint(
          point,
          waveEnvelope,
          waveProgress,
        )
      })

      pointer.speedX *= 0.72
      pointer.speedY *= 0.72

      context.lineWidth = 1

      context.strokeStyle = dark
        ? 'rgba(255,255,255,.055)'
        : 'rgba(36,42,54,.055)'

      context.beginPath()

      for (let row = 0; row < rows; row += 1) {
        for (let column = 0; column < columns; column += 1) {
          const index = row * columns + column
          const point = points[index]

          if (column < columns - 1) {
            const right = points[index + 1]

            context.moveTo(point.x, point.y)
            context.lineTo(right.x, right.y)
          }

          if (row < rows - 1) {
            const below = points[index + columns]

            context.moveTo(point.x, point.y)
            context.lineTo(below.x, below.y)
          }
        }
      }

      context.stroke()

      points.forEach((point) => {
        const displacement = Math.min(
          1,
          Math.hypot(
            point.x - point.homeX,
            point.y - point.homeY,
          ) / 32,
        )

        const pulse = reducedMotion
          ? 0
          : Math.sin(time * 0.00115 + point.phase) * 0.018

        const alpha = Math.min(
          dark ? 0.32 : 0.28,
          point.alpha + pulse + displacement * 0.08,
        )

        const radius =
          point.size +
          displacement * 0.45

        context.beginPath()

        context.fillStyle = dark
          ? `rgba(255,255,255,${alpha})`
          : `rgba(54,57,64,${alpha})`

        context.arc(
          point.x,
          point.y,
          radius,
          0,
          Math.PI * 2,
        )

        context.fill()
      })

      frame = requestAnimationFrame(draw)
    }

    const intersectionObserver =
      new IntersectionObserver(([entry]) => {
        visible = entry.isIntersecting
      })

    const resizeObserver = new ResizeObserver(resize)

    intersectionObserver.observe(host)
    resizeObserver.observe(host)

    resize()

    if (!coarsePointer) {
      host.addEventListener('pointermove', updatePointer)
      host.addEventListener('pointerleave', clearPointer)
    }

    frame = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(frame)

      intersectionObserver.disconnect()
      resizeObserver.disconnect()

      host.removeEventListener('pointermove', updatePointer)
      host.removeEventListener('pointerleave', clearPointer)
    }
  }, [dark])

  return (
    <canvas
      className="xy-section-grid"
      ref={canvasRef}
      aria-hidden="true"
    />
  )
}

function HeroPhone() {
  const stage = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = stage.current
    if (!node || matchMedia('(pointer: coarse)').matches) return

    const move = (event: PointerEvent) => {
      const rect = node.getBoundingClientRect()
      const x = (event.clientX - rect.left) / rect.width - 0.5
      const y = (event.clientY - rect.top) / rect.height - 0.5
      node.style.setProperty('--tilt-x', `${-y * 2.2}deg`)
      node.style.setProperty('--tilt-y', `${x * 3}deg`)
    }

    const reset = () => {
      node.style.setProperty('--tilt-x', '0deg')
      node.style.setProperty('--tilt-y', '0deg')
    }

    node.addEventListener('pointermove', move)
    node.addEventListener('pointerleave', reset)
    return () => {
      node.removeEventListener('pointermove', move)
      node.removeEventListener('pointerleave', reset)
    }
  }, [])

  return (
    <div className="xy-hero-device-stage" ref={stage}>

      <div className="xy-hero-fan" aria-hidden="true">
        <figure className="xy-hero-fan-card is-left">
          <img src={asset('showcase/mood.webp')} alt="" />
        </figure>
        <figure className="xy-hero-fan-card is-mid">
          <img src={asset('showcase/voice.webp')} alt="" />
        </figure>
        <figure className="xy-hero-fan-card is-right">
          <img src={asset('showcase/picture.webp')} alt="" />
        </figure>
      </div>
      <i className="xy-orbit xy-orbit-a" />
      <i className="xy-orbit xy-orbit-b" />

      <div className="xy-hero-real-device">
        <img src={asset('showcase/chat.webp')} alt="Xiaoyou chat interface" />
      </div>

      <div className="xy-float xy-float-memory">
        <Brain size={18} />
        <span><b>Long-term memory</b><small>Remember what matters</small></span>
      </div>
      <div className="xy-float xy-float-voice">
        <Waveform size={18} />
        <span><b>Realtime voice</b><small>Listening to you</small></span>
      </div>
    </div>
  )
}

function FeatureCard({ feature, index }: { feature: Feature; index: number }) {
  const Icon = feature.icon
  return (
    <article
      className={`xy-feature-card ${feature.dark ? 'is-dark' : ''}`}
      data-product-reveal
      style={{ '--delay': `${index * 70}ms` } as CSSProperties}
    >
      <div className="xy-feature-meta">
        <span>{feature.eyebrow}</span>
        <Icon size={22} />
      </div>
      <h3>{feature.title.split('\n').map((line) => <span key={line}>{line}</span>)}</h3>
      <p>{feature.copy}</p>
      <strong className="xy-feature-number">0{index + 1}</strong>
    </article>
  )
}

function VoiceOrb() {
  return (
    <div className="xy-voice-visual xy-siri-visual" aria-hidden="true">
      <div className="xy-siri-orb">
        <span className="xy-siri-aurora aurora-a" />
        <span className="xy-siri-aurora aurora-b" />
        <span className="xy-siri-aurora aurora-c" />
        <span className="xy-siri-aurora aurora-d" />

        <span className="xy-siri-mesh mesh-a" />
        <span className="xy-siri-mesh mesh-b" />
        <span className="xy-siri-mesh mesh-c" />

        <span className="xy-siri-glass" />
        <span className="xy-siri-highlight" />
        <span className="xy-siri-core" />

        <span className="xy-siri-ripple ripple-a" />
        <span className="xy-siri-ripple ripple-b" />
        <span className="xy-siri-ripple ripple-c" />
      </div>

      <div className="xy-siri-status">
        <Waveform size={18} weight="bold" />
        <span>Listening</span>
      </div>
    </div>
  )
}

export default function ProductPage() {
  useReveal()
  const progress = useScrollProgress()

  useEffect(() => {
    const previousLanguage = document.documentElement.lang
    const previousTitle = document.title

    document.documentElement.lang = 'en'
    document.title = '小悠'

    document.body.classList.add('xy-product-body')

    return () => {
      document.body.classList.remove('xy-product-body')
      document.documentElement.lang = previousLanguage
      document.title = previousTitle
    }
  }, [])

  return (
    <div className="xy-product-page" id="top">
      <div className="xy-scroll-progress" style={{ transform: `scaleX(${progress})` }} />
      <ProductNav />

      <main>
        <section className="xy-hero">
          <InteractiveGrid />
          <div className="xy-hero-copy" data-product-reveal>
            <div className="xy-kicker"><span /> AI Companion · Xiaoyou</div>
            <h1>
              <span>More than a chat box.</span>
              <em>Someone who slowly gets to know you.</em>
            </h1>
            <p>
              Xiaoyou brings memory, proactivity, voice, multimodality, and long-term companionship into one continuous conversation.
              She does more than answer. She remembers, responds, waits, and sometimes chooses to reach out first.
            </p>
            <div className="xy-hero-meta">
              <span><ShieldCheck size={16} /> Private account & data space</span>
              <span><MoonStars size={16} /> Light & dark themes</span>
              <span><DeviceMobile size={16} /> Android</span>
            </div>
            <div className="xy-hero-philosophy">
              <span>RELATIONSHIP</span>
              <p>Most AI remembers the current question. <strong>Xiaoyou remembers what happened between you.</strong></p>
            </div>

          </div>
          <HeroPhone />
        </section>
        <section className="xy-capabilities-section" id="capabilities">
          <InteractiveGrid />

          <div className="xy-capabilities-heading" data-product-reveal>
            <span className="xy-section-number">01 / CORE EXPERIENCE</span>

            <h2>
              She remembers,<br />
              and responds.
            </h2>

            <p>
              Memory, proactivity, multimodality, and realtime voice
              shape the way Xiaoyou stays with you.
            </p>
          </div>

          <div className="xy-capabilities-body">
            <div className="xy-feature-grid xy-feature-grid-compact">
              {features.map((feature, index) => (
                <FeatureCard
                  key={feature.eyebrow}
                  feature={feature}
                  index={index}
                />
              ))}
            </div>

            <div className="xy-capabilities-voice" id="voice">
              <InteractiveGrid dark />

              <div className="xy-capabilities-voice-copy" data-product-reveal>
                <span className="xy-section-number">
                  VOICE / REALTIME
                </span>

                <h3>
                  Just<br />
                  speak.
                </h3>

                <p>
                  Realtime voice combines recognition, natural speech, and interruption-aware playback.
                </p>

                <a href="#app">
                  Open the App
                  <ArrowRight size={16} />
                </a>
              </div>

              <VoiceOrb />
            </div>
          </div>
        </section>

<section className="xy-showcase" id="app">
          <InteractiveGrid />
          <div className="xy-showcase-copy" data-product-reveal>
            <span className="xy-section-number">02 / THE APP</span>
            <h2>Not more features.<br />A more natural way to be together.</h2>
            <p>
              Chat, realtime voice, mood, and everyday moments live inside one continuous relationship experience.
              Instead of a wall of screenshots, the interface appears as a collection of real moments from everyday use.
            </p>
            <div className="xy-showcase-tags">
              <span>Continuous chat</span>
              <span>Realtime voice</span>
              <span>Mood</span>
              <span>Moments</span>
            </div>
          </div>

          <div className="xy-showcase-stage" data-product-reveal>
            <figure className="xy-showcase-phone is-chat" data-product-reveal>
              <img src={asset('showcase/chat.webp')} alt="Xiaoyou chat interface" />
              <figcaption><b>CHAT</b><span>A conversation that continues</span></figcaption>
            </figure>

            <figure className="xy-showcase-phone is-voice" data-product-reveal>
              <img src={asset('showcase/voice.webp')} alt="Xiaoyou realtime voice interface" />
              <figcaption><b>VOICE</b><span>Speak naturally, stop anytime</span></figcaption>
            </figure>

            <figure className="xy-showcase-phone is-mood" data-product-reveal>
              <img src={asset('showcase/mood.webp')} alt="Xiaoyou mood interface" />
              <figcaption><b>MOOD</b><span>How she feels right now</span></figcaption>
            </figure>

            <figure className="xy-showcase-phone is-picture" data-product-reveal>
              <img src={asset('showcase/picture.webp')} alt="Recent Xiaoyou moments" />
              <figcaption><b>MOMENTS</b><span>Moments she recently shared</span></figcaption>
            </figure>
          </div>
        </section>

        <section className="xy-download-section" id="download">
          <InteractiveGrid />
          <div className="xy-download-heading" data-product-reveal>
            <span className="xy-section-number">03 / DOWNLOAD</span>
            <div>
              <h2>Now,<br />bring Xiaoyou with you.</h2>
              <p>
                Choose the installation method that works for you. Get automatic updates through the official app store,
                or download the Android APK directly. Previous releases and changelogs remain publicly available.
              </p>
            </div>
          </div>

          <div className="xy-download-grid" data-product-reveal>
            <a className="xy-download-card is-featured" href={VIVO_STORE_URL} target="_blank" rel="noreferrer">
              <span className="xy-download-card-topline">
                <span className="xy-download-card-icon is-vivo"><img src="/product/brand/vivo.png" alt="" /></span>
                <small>Recommended</small>
              </span>
              <span className="xy-download-card-copy">
                <b>vivo App Store</b>
                <span>Install from the official store and receive future updates automatically.</span>
              </span>
              <span className="xy-download-card-action">Open store <ArrowUpRight size={18} weight="bold" /></span>
            </a>

            <a className="xy-download-card" href={DIRECT_APK_URL}>
              <span className="xy-download-card-topline">
                <span className="xy-download-card-icon is-android">
                  <img src="/product/brand/android.png" alt="" />
                </span>
                <small>Android</small>
              </span>
              <span className="xy-download-card-copy">
                <b>Direct APK Download</b>
                <span>Download the latest Android package and get started immediately.</span>
              </span>
              <span className="xy-download-card-action">Download APK <ArrowDown size={18} weight="bold" /></span>
            </a>

            <a className="xy-download-card" href={GITHUB_RELEASES_URL} target="_blank" rel="noreferrer">
              <span className="xy-download-card-topline">
                <span className="xy-download-card-icon is-github"><img src="/product/brand/github.png" alt="" /></span>
                <small>Open Source</small>
              </span>
              <span className="xy-download-card-copy">
                <b>GitHub Releases</b>
                <span>Browse release history, changelogs, and the open-source project.</span>
              </span>
              <span className="xy-download-card-action">View releases <ArrowUpRight size={18} weight="bold" /></span>
            </a>
          </div>

          <div className="xy-download-note" data-product-reveal>
            <span><ShieldCheck size={16} /> Official channels · Secure downloads</span>
            <span>Currently available on Android</span>
          </div>
        </section>

</main>

      <footer className="xy-product-footer">
        <InteractiveGrid />
        <div className="xy-footer-top">
          <div className="xy-footer-brand">
            <b>Xiaoyou</b>
          </div>

          <nav>
            <a href="/privacy">Privacy Policy</a>
            <a href="/terms">Terms of Service</a>
            <a href="/observatory">Observatory</a>
          </nav>
        </div>

        <div className="xy-footer-bottom">
          <div className="xy-footer-records">
            <a
              href="https://beian.miit.gov.cn/"
              target="_blank"
              rel="noreferrer"
            >
              渝ICP备2026017342号
            </a>

            <span className="xy-footer-separator" aria-hidden="true">·</span>

            <a
              className="xy-footer-police"
              href="https://beian.mps.gov.cn/#/query/webSearch?code=50010802006906"
              target="_blank"
              rel="noreferrer"
            >
              <img src="/gongan-beian.png" alt="" aria-hidden="true" />
              <span>渝公网安备50010802006906号</span>
            </a>

            <span className="xy-footer-separator" aria-hidden="true">·</span>

            <a
              className="xy-footer-contact"
              href="mailto:2453997321@qq.com"
              aria-label="Email 2453997321@qq.com"
            >
              <img
                className="xy-footer-brand-icon is-qq"
                src="/product/brand/qq-mail.png"
                alt="QQ Mail"
              />
              <span>2453997321@qq.com</span>
            </a>

            <span className="xy-footer-separator" aria-hidden="true">·</span>

            <a
              className="xy-footer-github"
              href="https://github.com/yan-gd/xiaoyou"
              target="_blank"
              rel="noreferrer"
            >
              <img
                className="xy-footer-brand-icon is-github"
                src="/product/brand/github.png"
                alt="GitHub"
              />
              <span>github.com/yan-gd/xiaoyou</span>
            </a>

            <span className="xy-footer-separator" aria-hidden="true">·</span>

            <a
              className="xy-footer-vivo"
              href={VIVO_STORE_URL}
              target="_blank"
              rel="noreferrer"
            >
              <img
                className="xy-footer-brand-icon is-vivo"
                src="/product/brand/vivo.png"
                alt="vivo"
              />
              <span>App Store</span>
            </a>
          </div>

          <p className="xy-footer-copyright">
            © 2026 Xiaoyou · AI Companion
          </p>
        </div>
      </footer>
    </div>
  )
}

