import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { ArrowLeft, CheckCircle, ShieldCheck, Sparkle } from '@phosphor-icons/react'

export type LegalDocumentKind = 'privacy' | 'terms'

const websiteFiling = '渝ICP备2026017342号-1'
const appFiling = '渝ICP备2026017342号-2A'
const operator = '鄢国栋'
const privacyEmail = '2453997321@qq.com'

export function legalDocumentFromPath(pathname: string): LegalDocumentKind | null {
  const normalized = pathname.replace(/\/+$/, '') || '/'
  if (normalized === '/privacy') return 'privacy'
  if (normalized === '/terms') return 'terms'
  return null
}

export function ComplianceLinks({ compact = false }: { compact?: boolean }) {
  return <span className={`compliance-links${compact ? ' compact' : ''}`}>
    <a href="/privacy">隐私政策</a>
    <i aria-hidden="true" />
    <a href="/terms">用户协议</a>
    <i aria-hidden="true" />
    <a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">{websiteFiling}</a>
  </span>
}

function PolicySection({ number, title, children }: { number: string; title: string; children: ReactNode }) {
  return <section className="legal-section">
    <header><span>{number}</span><h2>{title}</h2></header>
    <div className="legal-section-body">{children}</div>
  </section>
}

function DataTable({ children, labels }: { children: ReactNode; labels: string[] }) {
  return <div className="legal-table" role="table">
    <div className="legal-table-head" role="row">
      {labels.map((label) => <strong key={label} role="columnheader">{label}</strong>)}
    </div>
    {children}
  </div>
}

function DataRow({ children }: { children: ReactNode }) {
  return <div className="legal-table-row" role="row">{children}</div>
}

function PrivacyPolicy() {
  return <>
    <div className="legal-lead">
      <ShieldCheck size={24} weight="duotone" />
      <p>小悠重视你的个人信息与共同回忆。本政策说明小悠 App、命轨观测台及其配套服务在提供聊天、语音、图片、记忆、通知和跨设备同步时，如何处理与保护信息。</p>
    </div>

    <PolicySection number="01" title="适用范围与运营者">
      <p>本政策适用于“小悠”App 及通过 <a href="https://xiaoyou.yoyoyan.cn">xiaoyou.yoyoyan.cn</a> 提供的配套服务。服务运营者为<strong>{operator}</strong>。</p>
      <ul>
        <li>App 备案号：{appFiling}</li>
        <li>网站备案号：{websiteFiling}</li>
        <li>隐私联系邮箱：<a href={`mailto:${privacyEmail}`}>{privacyEmail}</a></li>
      </ul>
      <p>在首次使用前，我们会展示本政策与用户协议。只有你主动选择“同意并继续”后，App 才会连接服务器并启用需要联网的功能；不同意时可以退出 App。</p>
    </PolicySection>

    <PolicySection number="02" title="我们处理的信息">
      <DataTable labels={['信息类别', '具体内容', '使用目的']}>
        <DataRow><strong>账号与登录</strong><span>邮箱地址、邮箱验证状态、不可逆密码哈希；使用 QQ 登录时的 QQ OpenID、昵称和头像</span><span>创建并识别账号、完成登录和找回密码、隔离不同用户的数据。我们不保存你的邮箱密码、QQ 密码或 QQ 授权凭证</span></DataRow>
        <DataRow><strong>连接与设备</strong><span>你填写的服务地址、设备名称和设备标识、App 版本、系统类型及必要运行日志</span><span>建立安全连接、设备同步、故障诊断与防止重复投递</span></DataRow>
        <DataRow><strong>聊天与媒体</strong><span>主动发送的文字、图片、表情、语音及由小悠生成的回复和媒体</span><span>完成对话、图片理解、语音识别与合成、内容同步和历史记录恢复</span></DataRow>
        <DataRow><strong>记忆与关系状态</strong><span>从对话中形成的短期记忆、长期记忆、提醒、关系状态和心情状态</span><span>保持对话连续性、实现提醒和个性化陪伴；不用于向第三方投放广告</span></DataRow>
        <DataRow><strong>本地偏好</strong><span>主题、字号、通知、App 锁、草稿、收藏及本地缓存设置</span><span>保存你的界面偏好与便捷功能，大部分仅保存在当前设备</span></DataRow>
        <DataRow><strong>推送信息</strong><span>推送 RegID、应用及设备基础信息、通知开关与送达状态</span><span>在 App 不处于前台时送达小悠的新消息与提醒</span></DataRow>
      </DataTable>
      <p>我们不会为了本服务主动读取你的通讯录、短信、通话记录、精确位置或广告画像。若未来新增相关能力，会在启用前另行说明并征得授权。</p>
    </PolicySection>

    <PolicySection number="03" title="系统权限说明">
      <DataTable labels={['权限或能力', '使用场景', '拒绝后的影响']}>
        <DataRow><strong>麦克风</strong><span>发送语音、语音识别和实时语音房</span><span>仍可使用文字和图片聊天</span></DataRow>
        <DataRow><strong>通知</strong><span>展示新消息、主动提醒和会话通知</span><span>只能在打开 App 后看到新消息</span></DataRow>
        <DataRow><strong>照片/媒体保存</strong><span>选择要发送的图片，或将聊天图片保存到相册；旧版 Android 可能请求存储权限</span><span>不影响文字聊天，但无法使用相应媒体操作</span></DataRow>
        <DataRow><strong>生物识别</strong><span>使用系统指纹或面容解锁 App</span><span>可关闭 App 锁；小悠不读取、存储或上传生物特征模板</span></DataRow>
        <DataRow><strong>后台与自启动相关能力</strong><span>维持消息连接、接收推送及恢复未读消息</span><span>系统可能延迟后台通知；可随时在系统设置中关闭</span></DataRow>
      </DataTable>
      <p>权限均按具体功能触发申请。你可以在 Android“设置—应用—小悠—权限/通知”中随时调整授权。</p>
    </PolicySection>

    <PolicySection number="04" title="第三方服务与 SDK">
      <p>为了实现模型、语音、图片和系统级推送能力，我们仅在对应功能被触发时向服务商传递完成请求所必需的信息：</p>
      <DataTable labels={['服务提供方', '处理内容', '目的与说明']}>
        <DataRow><strong>腾讯 QQ 互联</strong><span>仅在你选择“QQ 一键登录”时处理 QQ OpenID、昵称和头像；授权页面由腾讯提供</span><span>用于完成 QQ 授权登录并关联小悠内部用户编号。小悠不会获得或保存你的 QQ 密码；你也可以改用邮箱登录</span></DataRow>
        <DataRow><strong>验证邮件服务</strong><span>邮箱地址、一次性验证码和必要的投递状态</span><span>仅用于注册邮箱验证和忘记密码；验证码 10 分钟有效，验证完成或过期后失效</span></DataRow>
        <DataRow><strong>阿里云模型服务</strong><span>必要的对话文本、用户主动提交的图片或语音、上下文，以及请求日志</span><span>用于对话生成、语音识别、视觉理解、向量检索和工具判断</span></DataRow>
        <DataRow><strong>北京火山引擎科技有限公司</strong><span>需要合成的回复文本、实时语音音频、人物参考图及图片生成描述</span><span>用于语音合成、端到端语音房和生活照生成</span></DataRow>
        <DataRow><strong>维沃移动通信有限公司（vivo 推送 SDK）</strong><span>应用基础信息、应用内设备标识、设备硬件信息和系统基础信息，包括 AppID/AppKey/包名/版本、Push SDK 版本、RegID、设备类型及操作系统类型和版本</span><span>用于消息推送及推送 API 成功率统计。SDK 在你同意本政策后初始化；详见 <a href="https://developers.vivo.com/doc/d/23807c559e844cbeb06049ee69e71833" target="_blank" rel="noreferrer">vivo 推送 SDK 隐私与安全说明</a> 和 <a href="https://developers.vivo.com/doc/d/dc4bd47dfb974a0a92bc70840527b6b9" target="_blank" rel="noreferrer">vivo 推送隐私政策</a></span></DataRow>
      </DataTable>
      <p>上述服务商可能依其服务协议处理必要的网络地址、设备和安全日志。我们不会向第三方出售你的个人信息，也不会使用聊天内容投放个性化广告。</p>
    </PolicySection>

    <PolicySection number="05" title="存储、同步与保留">
      <p>服务数据主要存储在中国境内的自有部署服务器及你的设备中。服务器保存聊天历史、媒体、记忆、提醒、投递状态和语音房历史，以便跨设备恢复和保持对话连续；本地按日期缓存聊天图片和语音。</p>
      <ul>
        <li>每个注册账号拥有独立的内部用户编号、会话、设备命名空间、聊天历史及长短期记忆；一个账号的数据不会作为另一个账号的对话上下文。</li>
        <li>密码仅保存为带随机盐的 bcrypt 哈希；邮箱验证码以不可逆摘要保存，且设有有效期和错误次数限制。</li>
        <li>退出登录不会自动删除服务器历史；卸载 App 会清除系统管理的本地数据，但重新连接后可能从服务器恢复。</li>
        <li>草稿、界面偏好、App 锁开关等以本地保存为主；系统备份行为受 Android 与设备厂商设置控制。</li>
        <li>仅在实现功能与安全审计所需期限内保留信息。你提出删除请求后，我们会核验身份并删除或匿名化法律无需继续保留的数据。</li>
      </ul>
    </PolicySection>

    <PolicySection number="06" title="你的权利">
      <p>你可以查询、复制、更正或删除自己的聊天、媒体和设置，关闭通知或撤回系统权限，也可以通过上述隐私邮箱请求删除服务器数据、注销连接标识或获取说明。撤回同意不影响撤回前基于同意进行处理的合法性，但部分功能可能因此不可用。</p>
    </PolicySection>

    <PolicySection number="07" title="信息安全">
      <p>我们采用 HTTPS、访问令牌、最小权限、服务端访问控制和数据目录隔离等措施保护信息。请勿向他人泄露连接令牌。互联网服务无法保证绝对安全；发生可能影响你权益的安全事件时，我们会依法采取补救并告知。</p>
    </PolicySection>

    <PolicySection number="08" title="人工智能内容提示">
      <p>小悠的文字、语音、图片、心情摘要和记忆提取可能由人工智能生成，存在不准确或误解上下文的可能，不应作为医疗、法律、财务或紧急决策依据。重要共同经历应由你确认后再作为长期内容保留。</p>
      <p>App 聊天页面底部持续展示“内容由 AI 生成 · 请注意甄别”作为显式标识。服务端同时为助手生成的文字、图片和语音记录 AI 生成属性、服务提供方名称及编码、唯一内容编号和标识版本，并在媒体下载响应中携带对应来源标识；这些字段用于内容来源识别、安全审计和问题追溯，不用于广告画像。</p>
    </PolicySection>

    <PolicySection number="09" title="未成年人保护">
      <p>本服务主要面向具备相应民事行为能力的用户。未成年人应在监护人指导和同意下使用，不应提交不必要的敏感个人信息。监护人发现相关信息需要处理时，可通过隐私邮箱联系我们。</p>
    </PolicySection>

    <PolicySection number="10" title="政策更新与联系">
      <p>功能、权限或第三方服务发生实质变化时，我们会更新本政策并在 App 内提示；重大变化会再次征求同意。对本政策或个人信息处理有疑问，请联系 <a href={`mailto:${privacyEmail}`}>{privacyEmail}</a>。</p>
    </PolicySection>
  </>
}

function UserAgreement() {
  return <>
    <div className="legal-lead">
      <CheckCircle size={24} weight="duotone" />
      <p>本协议是你与本服务运营者关于使用“小悠”App、命轨观测台及配套服务的约定。请在使用前完整阅读。</p>
    </div>

    <PolicySection number="01" title="服务内容">
      <p>小悠提供单联系人聊天、语音、图片、提醒、记忆、关系状态、跨设备同步和命轨观测等功能。部分能力由第三方云模型、语音、图片和推送服务支持。</p>
    </PolicySection>

    <PolicySection number="02" title="使用与授权">
      <p>你可以使用已验证的邮箱和密码，或经 QQ 授权登录。你应妥善保管账号、密码与登录设备，并对通过自己账号发出的操作负责。你保留对主动提交内容的合法权益，同时授权服务在完成聊天、识别、生成、同步、记忆和通知所必需的范围内处理这些内容。该授权不包含公开传播或广告用途。</p>
    </PolicySection>

    <PolicySection number="03" title="人工智能生成内容">
      <p>小悠输出由人工智能生成，可能出现事实错误、时间误差、情绪误判或不恰当内容。图片和语音为合成内容，不代表真实人物或真实事件。你应结合实际判断，不将其作为专业意见或紧急服务。</p>
      <p>标识方式与样式：聊天页面底部持续显示“内容由 AI 生成 · 请注意甄别”；每条助手消息在数据层附带 <code>ai_generated</code>、<code>ai_provider_name</code>、<code>ai_provider_code</code>、<code>ai_content_id</code> 和标识版本，助手媒体的下载响应同步返回来源标识头。你不得恶意删除、篡改或隐匿依法设置的人工智能生成内容标识。</p>
    </PolicySection>

    <PolicySection number="04" title="合理使用">
      <p>不得利用服务侵害他人权益、绕过安全控制、实施违法活动、恶意消耗资源或上传无权处理的内容。发现明显滥用、安全风险或法律要求时，运营者可以限制相关功能并保留必要审计记录。</p>
    </PolicySection>

    <PolicySection number="05" title="服务可用性">
      <p>模型、网络、系统推送和设备后台策略可能造成延迟、中断或结果差异。我们会尽力维护服务，但不承诺永久无故障。维护、迁移或不可抗力导致服务暂时不可用时，会在合理范围内恢复并尽量保护已有数据。</p>
    </PolicySection>

    <PolicySection number="06" title="费用与第三方服务">
      <p>当前具体使用方式以运营者实际提供为准。模型、语音、图片、短信、网络或设备厂商服务可能产生第三方费用；如未来向你收费，会在付费前明确说明项目、价格和规则。</p>
    </PolicySection>

    <PolicySection number="07" title="终止与数据">
      <p>你可以停止使用并请求删除服务器数据。卸载 App 只会删除本机数据，不等同于删除服务器记录。终止服务后，依法应留存的信息将在法定期限内隔离保存，其余信息按隐私政策处理。</p>
    </PolicySection>

    <PolicySection number="08" title="协议变更与争议">
      <p>功能或法律要求变化时，我们可能更新本协议，并对重大变化进行提示。本协议适用中华人民共和国法律；争议应先友好协商，协商不成的，依法向有管辖权的人民法院提起诉讼。</p>
    </PolicySection>

    <PolicySection number="09" title="备案与联系">
      <ul>
        <li>运营者：{operator}</li>
        <li>App 备案号：{appFiling}</li>
        <li>网站备案号：{websiteFiling}</li>
        <li>联系邮箱：<a href={`mailto:${privacyEmail}`}>{privacyEmail}</a></li>
      </ul>
    </PolicySection>
  </>
}

export default function LegalPage({ kind }: { kind: LegalDocumentKind }) {
  const isPrivacy = kind === 'privacy'
  const title = isPrivacy ? '小悠隐私政策' : '小悠用户协议'

  useEffect(() => {
    const previousTitle = document.title
    document.title = `${title} · 小悠`
    return () => { document.title = previousTitle }
  }, [title])

  return <main className="legal-page">
    <div className="legal-glow legal-glow-one" aria-hidden="true" />
    <div className="legal-glow legal-glow-two" aria-hidden="true" />
    <header className="legal-topbar">
      <a className="legal-back" href="/" aria-label="返回小悠命轨观测台"><ArrowLeft size={18} /></a>
      <a className="legal-brand" href="/">
        <span><Sparkle size={14} weight="fill" /></span>
        <div><strong>小悠</strong><small>FATEBOUND OBSERVATORY</small></div>
      </a>
      <span className="legal-filing">{appFiling}</span>
    </header>

    <article className="legal-document">
      <div className="legal-title">
        <span>{isPrivacy ? 'PRIVACY & MEMORY' : 'TERMS & COVENANT'}</span>
        <h1>{title}</h1>
        <p>更新日期：2026 年 8 月 7 日 · 生效日期：2026 年 8 月 7 日</p>
      </div>
      {isPrivacy ? <PrivacyPolicy /> : <UserAgreement />}
    </article>

    <footer className="legal-footer">
      <strong>小悠 · 命轨观测台</strong>
      <ComplianceLinks />
      <p>App 备案号：{appFiling} · 联系邮箱：{privacyEmail}</p>
    </footer>
  </main>
}
