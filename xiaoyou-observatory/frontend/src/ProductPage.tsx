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
    title: '不是记住关键词，\n而是记住你。',
    copy: '短期上下文、长期记忆与关系状态共同工作。真正重要的偏好、共同经历与约定，会在以后自然回到对话里。',
    icon: Brain,
  },
  {
    eyebrow: 'PROACTIVE / 02',
    title: '她会在合适的时候，\n先来找你。',
    copy: '提醒、主动消息、关系节奏与通知能力，让小悠不只是在输入框后面等待下一句话。',
    icon: BellSimple,
    dark: true,
  },
  {
    eyebrow: 'VOICE / 03',
    title: '从一句文字，\n变成真实的交流感。',
    copy: '语音消息、实时语音房、情绪化语音合成与流式播放，把停顿、节奏和声音里的情绪带进一段关系。',
    icon: Microphone,
  },
  {
    eyebrow: 'VISION / 04',
    title: '她看见你分享的世界，\n也能留下自己的生活照。',
    copy: '图片理解、生活照生成和多模态聊天都在同一个会话里发生，不需要切换到另一个“工具页面”。',
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
  return (
    <header className="xy-product-nav">
      <a className="xy-product-brand" href="#top">
        <span className="xy-product-brand-mark">悠</span>
        <span>小悠</span>
      </a>

      <nav className={open ? 'is-open' : ''}>
        <a href="#capabilities" onClick={() => setOpen(false)}>能力</a>
        <a href="#voice" onClick={() => setOpen(false)}>声音</a>
        <a href="#app" onClick={() => setOpen(false)}>应用</a>
        <a className="xy-nav-download" href="#download" onClick={() => setOpen(false)}>
          下载 <ArrowDown size={12} weight="bold" />
        </a>
      </nav>

      <div className="xy-product-nav-actions">
        <a className="xy-observatory-link" href="/observatory">
          命轨监测台 <ArrowUpRight size={15} weight="bold" />
        </a>
        <button
          className="xy-nav-toggle"
          type="button"
          aria-label="展开导航"
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

function HeroInteractiveGrid() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const host = canvas?.parentElement
    const context = canvas?.getContext('2d')
    if (!canvas || !host || !context) return

    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches
    const coarsePointer = matchMedia('(pointer: coarse)').matches
    const pointer = { x: -1000, y: -1000, lastX: -1000, lastY: -1000, speedX: 0, speedY: 0, active: false }
    let points: GridPoint[] = []
    let particles: GridPoint[] = []
    let columns = 0
    let rows = 0
    let width = 0
    let height = 0
    let dpr = 1
    let frame = 0
    let lastFrame = 0
    let visible = true

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
      columns = Math.ceil(width / spacing) + 3
      rows = Math.ceil(height / spacing) + 3
      points = []
      for (let row = 0; row < rows; row += 1) {
        for (let column = 0; column < columns; column += 1) {
          const homeX = (column - 1) * spacing
          const homeY = (row - 1) * spacing
          const seed = (column * 17 + row * 31) % 19
          points.push({
            homeX,
            homeY,
            x: homeX,
            y: homeY,
            vx: 0,
            vy: 0,
            size: seed % 7 === 0 ? 5 : seed % 3 === 0 ? 3.3 : 2.2,
            alpha: 0.14 + (seed / 19) * 0.18,
            phase: seed * 0.47,
          })
        }
      }

      particles = []
      const particleSpacing = width < 720 ? 11 : 13
      const particleStartX = width < 720 ? -20 : width * 0.34
      const particleEndX = width + 30
      const particleStartY = height * 0.14
      const particleEndY = height * 0.86
      const maxParticles = width < 720 ? 920 : 1700
      let particleColumn = 0

      for (let x = particleStartX; x <= particleEndX && particles.length < maxParticles; x += particleSpacing) {
        const progress = (x - particleStartX) / Math.max(1, particleEndX - particleStartX)
        const centerY = height * (0.48 + Math.sin(progress * Math.PI * 2.15) * 0.105)
        const halfBand = height * (0.2 + Math.sin(progress * Math.PI) * 0.075)
        let particleRow = 0

        for (let y = particleStartY; y <= particleEndY && particles.length < maxParticles; y += particleSpacing) {
          const distanceFromBand = Math.abs(y - centerY) / halfBand
          const hash = (particleColumn * 47 + particleRow * 83 + particleColumn * particleRow * 7) % 101
          const density = 0.88 - distanceFromBand * 0.55
          particleRow += 1
          if (distanceFromBand > 1 || hash / 100 > density) continue

          const jitterX = ((hash * 13) % 9) - 4
          const jitterY = ((hash * 29) % 9) - 4
          const homeX = x + jitterX
          const homeY = y + jitterY
          particles.push({
            homeX,
            homeY,
            x: homeX,
            y: homeY,
            vx: 0,
            vy: 0,
            size: 1.35 + (hash % 6) * 0.36,
            alpha: 0.08 + (1 - distanceFromBand) * 0.29 + (hash % 5) * 0.012,
            phase: hash * 0.19,
          })
        }
        particleColumn += 1
      }
    }

    const updatePointer = (event: PointerEvent) => {
      const rect = host.getBoundingClientRect()
      pointer.x = event.clientX - rect.left
      pointer.y = event.clientY - rect.top
      pointer.speedX = pointer.lastX > -900 ? event.clientX - pointer.lastX : 0
      pointer.speedY = pointer.lastY > -900 ? event.clientY - pointer.lastY : 0
      pointer.lastX = event.clientX
      pointer.lastY = event.clientY
      pointer.active = true
    }

    const clearPointer = () => {
      pointer.active = false
      pointer.lastX = -1000
      pointer.lastY = -1000
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

      if (pointer.active) {
        const glow = context.createRadialGradient(pointer.x, pointer.y, 0, pointer.x, pointer.y, 220)
        glow.addColorStop(0, 'rgba(184, 196, 235, 0.28)')
        glow.addColorStop(0.42, 'rgba(231, 196, 207, 0.16)')
        glow.addColorStop(1, 'rgba(255, 255, 255, 0)')
        context.fillStyle = glow
        context.fillRect(pointer.x - 220, pointer.y - 220, 440, 440)
      }

      const movePoint = (
        point: GridPoint,
        radius: number,
        strength: number,
        spring: number,
        damping: number,
      ) => {
        if (pointer.active) {
          const dx = point.x - pointer.x
          const dy = point.y - pointer.y
          const distanceSquared = dx * dx + dy * dy
          if (distanceSquared < radius * radius && distanceSquared > 1) {
            const distance = Math.sqrt(distanceSquared)
            const influence = 1 - distance / radius
            const force = influence * influence * strength
            const direction = Math.sign(pointer.speedX + pointer.speedY) || 1
            point.vx += (dx / distance) * force + pointer.speedX * 0.05 * influence
            point.vy += (dy / distance) * force + pointer.speedY * 0.05 * influence
            point.vx += (-dy / distance) * force * 0.16 * direction
            point.vy += (dx / distance) * force * 0.16 * direction
          }
        }
        point.vx += (point.homeX - point.x) * spring
        point.vy += (point.homeY - point.y) * spring
        point.vx *= damping
        point.vy *= damping
        point.x += point.vx
        point.y += point.vy
      }

      points.forEach((point) => movePoint(point, 190, 5.5, 0.017, 0.89))
      particles.forEach((particle) => movePoint(particle, 230, 14, 0.016, 0.89))
      pointer.speedX *= 0.72
      pointer.speedY *= 0.72

      context.lineWidth = 1
      context.strokeStyle = 'rgba(33, 37, 44, 0.07)'
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

      particles.forEach((particle) => {
        const distanceFromHome = Math.hypot(particle.x - particle.homeX, particle.y - particle.homeY)
        const displacement = Math.min(1, distanceFromHome / 34)
        const pulse = reducedMotion ? 0 : Math.sin(time * 0.0015 + particle.phase) * 0.028
        const alpha = Math.min(0.72, particle.alpha + pulse + displacement * 0.31)
        context.fillStyle = `rgba(54, 57, 64, ${alpha})`
        const size = particle.size + displacement * 2.4
        context.fillRect(particle.x - size / 2, particle.y - size / 2, size, size)
      })

      points.forEach((point) => {
        const pulse = reducedMotion ? 0 : Math.sin(time * 0.0012 + point.phase) * 0.035
        const displacement = Math.min(1, Math.hypot(point.x - point.homeX, point.y - point.homeY) / 24)
        const alpha = Math.min(0.52, point.alpha + pulse + displacement * 0.2)
        context.fillStyle = `rgba(54, 57, 64, ${alpha})`
        const size = point.size + displacement * 1.5
        context.fillRect(point.x - size / 2, point.y - size / 2, size, size)
      })

      frame = requestAnimationFrame(draw)
    }

    const observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting
    })
    const resizeObserver = new ResizeObserver(resize)
    observer.observe(host)
    resizeObserver.observe(host)
    resize()
    if (!coarsePointer) {
      host.addEventListener('pointermove', updatePointer)
      host.addEventListener('pointerleave', clearPointer)
    }
    frame = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      resizeObserver.disconnect()
      host.removeEventListener('pointermove', updatePointer)
      host.removeEventListener('pointerleave', clearPointer)
    }
  }, [])

  return <canvas className="xy-hero-grid" ref={canvasRef} aria-hidden="true" />
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
      node.style.setProperty('--tilt-x', `${-y * 4.5}deg`)
      node.style.setProperty('--tilt-y', `${x * 6}deg`)
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
        <img src={asset('showcase/chat.webp')} alt="小悠聊天界面" />
      </div>

      <div className="xy-float xy-float-memory">
        <Brain size={18} />
        <span><b>长期记忆</b><small>记住真正重要的事</small></span>
      </div>
      <div className="xy-float xy-float-voice">
        <Waveform size={18} />
        <span><b>实时语音</b><small>正在听你说</small></span>
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
        <span>正在聆听</span>
      </div>
    </div>
  )
}

export default function ProductPage() {
  useReveal()
  const progress = useScrollProgress()

  useEffect(() => {
    document.body.classList.add('xy-product-body')
    return () => document.body.classList.remove('xy-product-body')
  }, [])

  return (
    <div className="xy-product-page" id="top">
      <div className="xy-scroll-progress" style={{ transform: `scaleX(${progress})` }} />
      <ProductNav />

      <main>
        <section className="xy-hero">
          <HeroInteractiveGrid />
          <div className="xy-hero-copy" data-product-reveal>
            <div className="xy-kicker"><span /> AI Companion · 小悠</div>
            <h1>
              <span>不是一个聊天框。</span>
              <em>是一个会慢慢认识你的人。</em>
            </h1>
            <p>
              小悠把记忆、主动交互、语音、多模态与长期关系体验放进同一段持续发生的对话里。
              她不只是回答问题，也会记得、回应、等待，并在合适的时候主动出现。
            </p>
            <div className="xy-hero-meta">
              <span><ShieldCheck size={16} /> 独立账号与数据空间</span>
              <span><MoonStars size={16} /> 深浅色主题</span>
              <span><DeviceMobile size={16} /> Android</span>
            </div>
            <div className="xy-hero-philosophy">
              <span>RELATIONSHIP</span>
              <p>普通的 AI 记得这一轮问题。<strong>小悠记得的是，你们之间发生过什么。</strong></p>
            </div>

          </div>
          <HeroPhone />
        </section>

        <section className="xy-features" id="capabilities">
          <div className="xy-section-heading" data-product-reveal>
            <span className="xy-section-number">01 / CORE EXPERIENCE</span>
            <h2>把“陪伴”拆成真正可以运行的能力。</h2>
            <p>每一个看起来很自然的瞬间，背后都有一条完整的产品链路。</p>
          </div>
          <div className="xy-feature-grid">
            {features.map((feature, index) => <FeatureCard key={feature.eyebrow} feature={feature} index={index} />)}
          </div>
        </section>

        <section className="xy-voice-section" id="voice">
          <div className="xy-voice-copy" data-product-reveal>
            <span className="xy-section-number">02 / VOICE</span>
            <h2>有些话，<br />打字会太慢。</h2>
            <p>
              实时语音房、语音识别、情绪化 TTS 与流式播放组合在一起，
              让对话拥有停顿、节奏和声音里的情绪。
            </p>
            <a href="#app">看看真实的 App <ArrowRight size={17} /></a>
          </div>
          <VoiceOrb />
        </section>

        <section className="xy-showcase" id="app">
          <div className="xy-showcase-copy" data-product-reveal>
            <span className="xy-section-number">03 / THE APP</span>
            <h2>不是把功能堆进 App。<br />是把相处变得自然。</h2>
            <p>
              聊天、实时语音、心情与最近日常，被放在同一套关系体验里。
              这里不再把每张截图做成“海报墙”，而是让它们像真实使用中的几个瞬间一样出现。
            </p>
            <div className="xy-showcase-tags">
              <span>持续对话</span>
              <span>实时语音</span>
              <span>心情状态</span>
              <span>日常照片</span>
            </div>
          </div>

          <div className="xy-showcase-stage" data-product-reveal>
            <figure className="xy-showcase-phone is-chat" data-product-reveal>
              <img src={asset('showcase/chat.webp')} alt="小悠聊天界面" />
              <figcaption><b>CHAT</b><span>一直发生的对话</span></figcaption>
            </figure>

            <figure className="xy-showcase-phone is-voice" data-product-reveal>
              <img src={asset('showcase/voice.webp')} alt="小悠实时语音界面" />
              <figcaption><b>VOICE</b><span>随时开口，也能随时结束</span></figcaption>
            </figure>

            <figure className="xy-showcase-phone is-mood" data-product-reveal>
              <img src={asset('showcase/mood.webp')} alt="小悠心情界面" />
              <figcaption><b>MOOD</b><span>她此刻的状态</span></figcaption>
            </figure>

            <figure className="xy-showcase-phone is-picture" data-product-reveal>
              <img src={asset('showcase/picture.webp')} alt="小悠最近日常照片" />
              <figcaption><b>MOMENTS</b><span>最近分享给你的生活</span></figcaption>
            </figure>
          </div>
        </section>

        <section className="xy-download-section" id="download">
          <div className="xy-download-heading" data-product-reveal>
            <span className="xy-section-number">04 / DOWNLOAD</span>
            <div>
              <h2>现在，<br />把小悠带到身边。</h2>
              <p>
                选择适合你的安装方式。通过官方应用商店获取更新，
                或直接下载 Android 安装包；历史版本与更新记录也始终公开可查。
              </p>
            </div>
          </div>

          <div className="xy-download-grid" data-product-reveal>
            <a className="xy-download-card is-featured" href={VIVO_STORE_URL} target="_blank" rel="noreferrer">
              <span className="xy-download-card-topline">
                <span className="xy-download-card-icon is-vivo"><img src="/product/brand/vivo.png" alt="" /></span>
                <small>推荐</small>
              </span>
              <span className="xy-download-card-copy">
                <b>vivo 应用商店</b>
                <span>官方渠道安装，后续版本自动更新。</span>
              </span>
              <span className="xy-download-card-action">前往商店 <ArrowUpRight size={18} weight="bold" /></span>
            </a>

            <a className="xy-download-card" href={DIRECT_APK_URL}>
              <span className="xy-download-card-topline">
                <span className="xy-download-card-icon is-android">
                  <img src="/product/brand/android.png" alt="" />
                </span>
                <small>Android</small>
              </span>
              <span className="xy-download-card-copy">
                <b>直接下载 APK</b>
                <span>获取当前最新安装包，快速开始使用。</span>
              </span>
              <span className="xy-download-card-action">下载安装包 <ArrowDown size={18} weight="bold" /></span>
            </a>

            <a className="xy-download-card" href={GITHUB_RELEASES_URL} target="_blank" rel="noreferrer">
              <span className="xy-download-card-topline">
                <span className="xy-download-card-icon is-github"><img src="/product/brand/github.png" alt="" /></span>
                <small>Open Source</small>
              </span>
              <span className="xy-download-card-copy">
                <b>GitHub Releases</b>
                <span>查看版本历史、更新说明与开源项目。</span>
              </span>
              <span className="xy-download-card-action">查看版本 <ArrowUpRight size={18} weight="bold" /></span>
            </a>
          </div>

          <div className="xy-download-note" data-product-reveal>
            <span><ShieldCheck size={16} /> 官方渠道 · 安全下载</span>
            <span>当前支持 Android</span>
          </div>
        </section>

</main>

      <footer className="xy-product-footer">
        <div className="xy-footer-top">
          <div className="xy-footer-brand">
            <span className="xy-product-brand-mark">悠</span>
            <b>小悠</b>
          </div>

          <nav>
            <a href="/privacy">隐私政策</a>
            <a href="/terms">用户协议</a>
            <a href="/observatory">命轨监测台</a>
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
              aria-label="发送邮件到 2453997321@qq.com"
            >
              <img
                className="xy-footer-brand-icon is-qq"
                src="/product/brand/qq-mail.png"
                alt="QQ邮箱"
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
              <span>应用商店</span>
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
