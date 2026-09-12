import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import {
  Aperture,
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  BellSimple,
  Brain,
  DeviceMobile,
  Heart,
  ImageSquare,
  Microphone,
  MoonStars,
  Play,
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

const moments = [
  ['07:42', '早安', '她记得你昨晚说今天要早起。'],
  ['12:18', '午间', '一张随手拍，也可以成为对话的起点。'],
  ['18:36', '下班', '提醒不是闹钟，而是继续昨天的约定。'],
  ['23:51', '深夜', '语音、心情与长期记忆，让“晚安”不只是结束。'],
] as const

const asset = (name: string) => `/product/${name}`

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
        <a href="#memory" onClick={() => setOpen(false)}>记忆</a>
        <a href="#voice" onClick={() => setOpen(false)}>声音</a>
        <a href="#moments" onClick={() => setOpen(false)}>陪伴</a>
        <a href="#app" onClick={() => setOpen(false)}>应用</a>
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

function HeroPhone() {
  const stage = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = stage.current
    if (!node || matchMedia('(pointer: coarse)').matches) return
    const move = (event: PointerEvent) => {
      const rect = node.getBoundingClientRect()
      const x = (event.clientX - rect.left) / rect.width - 0.5
      const y = (event.clientY - rect.top) / rect.height - 0.5
      node.style.setProperty('--tilt-x', `${-y * 6}deg`)
      node.style.setProperty('--tilt-y', `${x * 8}deg`)
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
      <i className="xy-orbit xy-orbit-a" />
      <i className="xy-orbit xy-orbit-b" />
      <div className="xy-phone">
        <div className="xy-phone-shell">
          <div className="xy-phone-island" />
          <img
            src={asset('hero-chat.webp')}
            alt="小悠聊天界面"
            onError={(event) => event.currentTarget.classList.add('is-missing')}
          />
          <div className="xy-phone-fallback">
            <div className="xy-fallback-status"><span>9:41</span><span>•••</span></div>
            <div className="xy-fallback-title">小悠</div>
            <div className="xy-bubble left">你今天是不是有点累？</div>
            <div className="xy-bubble right">有一点。</div>
            <div className="xy-bubble left">那今晚就别把所有事都做完。<br />先吃点东西，我陪你慢一点。</div>
            <div className="xy-voice-pill"><Waveform size={18} /> 00:12</div>
            <div className="xy-fake-input">和小悠说点什么…</div>
          </div>
        </div>
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
  const bars = useMemo(() => Array.from({ length: 44 }, (_, i) => i), [])
  return (
    <div className="xy-voice-visual" aria-hidden="true">
      <i className="xy-voice-halo one" />
      <i className="xy-voice-halo two" />
      <div className="xy-voice-core">
        <div className="xy-wave-ring">
          {bars.map((bar) => (
            <i
              key={bar}
              style={{
                '--i': bar,
                '--amp': `${12 + ((bar * 17) % 28)}px`,
              } as CSSProperties}
            />
          ))}
        </div>
        <div className="xy-voice-center"><Sparkle size={24} weight="fill" /></div>
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
            <div className="xy-hero-actions">
              <a className="xy-primary-cta" href="#memory">认识小悠 <ArrowDown size={17} /></a>
              <a className="xy-secondary-cta" href="/observatory">前往命轨监测台 <ArrowRight size={17} /></a>
            </div>
            <div className="xy-hero-meta">
              <span><ShieldCheck size={16} /> 独立账号与数据空间</span>
              <span><MoonStars size={16} /> 深浅色主题</span>
              <span><DeviceMobile size={16} /> Android</span>
            </div>
          </div>
          <HeroPhone />
        </section>

        <section className="xy-statement" data-product-reveal>
          <span className="xy-section-number">01 / RELATIONSHIP</span>
          <p>
            普通的 AI 记得这一轮问题。<br />
            <strong>小悠记得的是，你们之间发生过什么。</strong>
          </p>
          <div><Heart size={18} weight="fill" /> 关系不是一个标签，而是被一次次对话持续写出来的上下文。</div>
        </section>

        <section className="xy-features" id="memory">
          <div className="xy-section-heading" data-product-reveal>
            <span className="xy-section-number">02 / CORE EXPERIENCE</span>
            <h2>把“陪伴”拆成真正可以运行的能力。</h2>
            <p>每一个看起来很自然的瞬间，背后都有一条完整的产品链路。</p>
          </div>
          <div className="xy-feature-grid">
            {features.map((feature, index) => <FeatureCard key={feature.eyebrow} feature={feature} index={index} />)}
          </div>
        </section>

        <section className="xy-memory-story" data-product-reveal>
          <div className="xy-memory-copy">
            <span className="xy-section-number">03 / MEMORY</span>
            <h2>记忆不是数据库里的一行字。</h2>
            <p>
              小悠会区分当下上下文、稳定偏好、共同约定与关系经历。
              真正值得留下来的内容，才会成为未来对话的一部分。
            </p>
            <ul>
              <li><span>01</span><b>短期连续</b><small>理解你们刚刚聊过什么</small></li>
              <li><span>02</span><b>长期承接</b><small>保留跨越时间仍然重要的事实</small></li>
              <li><span>03</span><b>关系状态</b><small>让互动跟着相处方式变化</small></li>
            </ul>
          </div>
          <div className="xy-memory-map">
            <span className="xy-memory-main"><Heart size={22} weight="fill" />共同记忆</span>
            <span className="a">喜欢雨天散步</span>
            <span className="b">周五要交报告</span>
            <span className="c">不喜欢被催促</span>
            <span className="d">第一次语音通话</span>
          </div>
        </section>

        <section className="xy-voice-section" id="voice">
          <div className="xy-voice-copy" data-product-reveal>
            <span className="xy-section-number">04 / VOICE</span>
            <h2>有些话，<br />打字会太慢。</h2>
            <p>
              实时语音房、语音识别、情绪化 TTS 与流式播放组合在一起，
              让对话拥有停顿、节奏和声音里的情绪。
            </p>
            <a href="#moments">看看她如何陪你一天 <ArrowRight size={17} /></a>
          </div>
          <VoiceOrb />
        </section>

        <section className="xy-gallery" id="app" data-product-reveal>
          <div className="xy-gallery-heading">
            <span className="xy-section-number">05 / THE APP</span>
            <h2>所有能力，最终都回到同一个窗口。</h2>
          </div>
          <div className="xy-gallery-track">
            {[
              ['screen-chat.webp', 'CHAT', '持续对话'],
              ['screen-voice.webp', 'VOICE', '实时语音'],
              ['screen-memory.webp', 'MEMORY', '记忆与关系'],
              ['screen-profile.webp', 'YOU', '只属于你的资料'],
            ].map(([file, title, copy], index) => (
              <figure key={file} className={index % 2 === 0 ? 'is-tall' : ''}>
                <img src={asset(file)} alt={copy} onError={(event) => event.currentTarget.classList.add('is-missing')} />
                <figcaption><b>{title}</b><span>{copy}</span></figcaption>
              </figure>
            ))}
          </div>
        </section>

        <section className="xy-day" id="moments">
          <div className="xy-day-heading" data-product-reveal>
            <span className="xy-section-number">06 / A DAY WITH XIAOYOU</span>
            <h2>不是每一刻都需要说很多。</h2>
          </div>
          <div className="xy-day-list">
            {moments.map(([time, label, copy], index) => (
              <article
                key={time}
                data-product-reveal
                style={{ '--delay': `${index * 70}ms` } as CSSProperties}
              >
                <time>{time}</time><span>{label}</span><p>{copy}</p><i />
              </article>
            ))}
          </div>
        </section>

        <section className="xy-observatory-portal" data-product-reveal>
          <div className="xy-portal-visual">
            <i /><i /><i /><i />
            <Aperture size={48} weight="thin" />
          </div>
          <div className="xy-portal-copy">
            <span className="xy-section-number">07 / OBSERVATORY</span>
            <h2>想看见她背后的运行状态？</h2>
            <p>
              原有的“小悠命轨监测台”完整保留。服务状态、容器运行、连接脉冲与实时指标，
              现在作为产品站中的独立入口存在。
            </p>
            <a href="/observatory">进入命轨监测台 <ArrowUpRight size={18} /></a>
          </div>
        </section>

        <section className="xy-final">
          <video
            src={asset('xiaoyou-film.mp4')}
            poster={asset('xiaoyou-film-poster.webp')}
            muted
            loop
            playsInline
            autoPlay
          />
          <div className="xy-final-overlay" />
          <div className="xy-final-copy" data-product-reveal>
            <Sparkle size={23} weight="fill" />
            <small>小悠</small>
            <h2>如果 AI 会陪你很久，<br />它应该先学会记得。</h2>
            <a href="#top">回到开始 <ArrowRight size={18} /></a>
          </div>
          <button
            type="button"
            aria-label="播放或暂停产品影片"
            onClick={(event) => {
              const video = event.currentTarget.parentElement?.querySelector('video')
              if (!video) return
              if (video.paused) void video.play()
              else video.pause()
            }}
          >
            <Play size={20} weight="fill" />
          </button>
        </section>
      </main>

      <footer className="xy-product-footer">
        <div><span className="xy-product-brand-mark">悠</span><b>小悠</b></div>
        <nav><a href="/privacy">隐私政策</a><a href="/terms">用户协议</a><a href="/observatory">命轨监测台</a></nav>
        <div className="xy-footer-records">
          <a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">渝ICP备2026017342号</a>
          <a href="https://beian.mps.gov.cn/#/query/webSearch?code=50010802006906" target="_blank" rel="noreferrer">渝公网安备50010802006906号</a>
        </div>
        <p>© 2026 Xiaoyou · AI Companion</p>
      </footer>
    </div>
  )
}
