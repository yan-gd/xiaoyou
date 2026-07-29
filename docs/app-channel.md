# 小悠 App 通道

AppChannel 是现有微信通道之外的移动端传输适配器，不是第二套小悠。
人格、模型、短期记忆、长期记忆、提醒和生活照仍以服务器为唯一事实源，
App 与微信共用固定会话 `yoyo`。

## 通道关系

```text
Flutter App ── HTTPS ── AppChannel ─┐
                                    ├─ ChatChannel.produce
微信 ─────────────── WeChat Channel ┘
                                           ├─ 现有插件与模型
                                           ├─ data 下的记忆和状态
                                           └─ 按来源通道发送
```

App 的文字和普通语音转写都会进入现有连续输入、记忆治理和模型回复链路。
协议层只校验身份、大小、消息 ID 和送达状态，不使用关键词、正则或硬编码
替代模型的语义判断。

语音是 App 专属媒介：

- App 录音使用 `qwen3-asr-flash` 转写，转写结果作为用户原话。
- App 语音输入固定获得语音回复，不增加媒介判断调用。
- App 文字输入会把 YoYo 原话、已经生成的小悠回复和近期对话交给
  `qwen3.7-plus`，由模型判断本轮更适合文字还是语音；不使用关键词、
  正则或本地意图捷径。
- 小悠回复使用火山引擎 Seed-TTS 2.0 与
  `ICL_uranus_zh_female_rouguhunshi_tob`（柔骨魂师女声）合成，并通过
  `loudness_rate=100` 在服务端统一生成 2 倍音量。
- 微信的 `SPEECH_RECOGNITION` 和 `VOICE_REPLY_VOICE` 保持关闭。
- TTS 失败时退回真实文本回复，不丢失模型结果。

## 开启服务

服务器 `.env`：

```dotenv
XIAOYOU_APP_ENABLED=true
XIAOYOU_APP_TOKEN=替换为长随机值
XIAOYOU_APP_DEFAULT_PROACTIVE=false
XIAOYOU_APP_VOICE_ENABLED=true
XIAOYOU_APP_TEXT_VOICE_DECISION_ENABLED=true
XIAOYOU_APP_TTS_API_KEY=火山语音控制台的API_Key
XIAOYOU_APP_TTS_LOUDNESS_RATE=100
XIAOYOU_VOICE_ROOM_ENABLED=true
XIAOYOU_VOICE_ROOM_APP_ID=火山端到端实时语音App_ID
XIAOYOU_VOICE_ROOM_ACCESS_KEY=火山端到端实时语音Access_Token
XIAOYOU_VOICE_ROOM_MODEL=1.2.1.1
XIAOYOU_VOICE_ROOM_SPEAKER=zh_female_xiaohe_jupiter_bigtts
XIAOYOU_VOICE_ROOM_LOUDNESS_RATE=100
XIAOYOU_VOICE_ROOM_IDLE_TIMEOUT=300
XIAOYOU_VIVO_PUSH_APP_ID=vivo开放平台应用ID
XIAOYOU_VIVO_PUSH_APP_KEY=vivo开放平台AppKey
XIAOYOU_VIVO_PUSH_APP_SECRET=vivo开放平台AppSecret
```

生成随机令牌：

```bash
openssl rand -hex 32
```

语音识别继续复用现有百炼 `KEY`；语音合成使用独立的火山语音凭证，
不会复用方舟图片生成的 `SEEDREAM_KEY`。Compose 中的默认值为：

```text
XIAOYOU_APP_ASR_MODEL=qwen3-asr-flash
XIAOYOU_APP_TTS_PROVIDER=volcengine
XIAOYOU_APP_TTS_MODEL=seed-tts-2.0
XIAOYOU_APP_TTS_VOICE=ICL_uranus_zh_female_rouguhunshi_tob
XIAOYOU_APP_TTS_LOUDNESS_RATE=100
XIAOYOU_APP_TEXT_VOICE_DECISION_ENABLED=true
XIAOYOU_APP_VOICE_ROUTE_MODEL=qwen3.7-plus
XIAOYOU_APP_VOICE_ROUTE_ENABLE_THINKING=false
```

新版火山语音控制台只需配置 `XIAOYOU_APP_TTS_API_KEY`。若账号仍使用
旧版鉴权，则留空该变量并同时配置 `XIAOYOU_APP_TTS_APP_ID` 与
`XIAOYOU_APP_TTS_ACCESS_KEY`。`XIAOYOU_APP_TTS_LOUDNESS_RATE` 会被限制在
`[-50, 100]`，默认 `100`；其中 `100` 代表 2 倍音量、`0` 代表默认音量、
`-50` 代表 0.5 倍音量。凭证只放服务器 `.env`，不要写入 APK 或提交 Git。
App 播放端不再额外增加 Android 响度，避免双重放大和跨平台音量不一致。

### O2.0 端到端语音房

语音房不再把每句话伪装成普通 App 语音消息。服务器会为每次进入语音房创建
一个独立的火山 WebSocket 会话，固定使用 O2.0 规范模型
`dialog.extra.model=1.2.1.1`：

- App 进入房间后立即打开连续 `16 kHz / 单声道 / PCM16` 麦克风流；无需点击
  圆球开始下一轮。静音时客户端持续发送静音帧以维持 O2.0 会话，服务端按
  20 ms 音频帧转发。
- O2.0 在同一连接中完成识别、对话和 `24 kHz / PCM16` 语音生成。
- 服务端收到 O2.0 `ASRInfo` 首字事件后，客户端立即停止当前播放，并用实际
  已播放毫秒数发送 `ConversationTruncate`。因此用户可以在小悠说话时直接
  插嘴，下一轮模型上下文只保留真正听到的回复部分。
- 建立会话前，从 ShortMemory 读取当前本来就会注入模型的原生
  `user/assistant` 消息，并作为结构化 `dialog_context` 传给 O2.0。
- 同一语音房复用一个 WebSocket；下一次进入时再用服务端 `dialog_id` 和最新
  短期记忆续接，因此不会依靠客户端拼接提示词。
- 每个完整或被打断的问答终态写入 `data/app_channel/voice_rooms.db`，随后由后台 FIFO
  异步写入 ShortMemory、ConversationArchive 和 LongTermMemory；记忆写入不
  占用主聊天线程，失败记录会持久化并在启动后重放。被打断时，用户原话仍会
  独立进入记忆；助手侧只投影已经完整播放完毕的句子，并标记为 `partial`。
- 语音房逐句记录只从 `/v1/voice-rooms` 读取，绝不写入
  `data/app_channel/app.db` 的主聊天消息表，所以主聊天页面不显示这些气泡；
  常规聊天的后续模型上下文仍能记得这段语音对话。
- App 被系统杀掉而没有发送结束请求时，房间空闲 300 秒后会自动关闭并保留
  已完成的逐轮记录；服务重启会把失去 WebSocket 的旧活动房标为中断。

O2.0 与普通消息的 Seed-TTS 是两套产品凭证。这里使用火山端到端实时语音
控制台的 App ID 和 Access Token，不使用 `XIAOYOU_APP_TTS_API_KEY`。默认
音色 `zh_female_xiaohe_jupiter_bigtts` 是 O2.0 支持音色；现有
`ICL_uranus_zh_female_rouguhunshi_tob` 不支持 O2.0，仍只用于普通 App
语音回复。字段与版本号以
[火山端到端实时语音大模型 API 文档](https://docs.volcengine.com/docs/6561/1594356?lang=zh)
为准。

### vivo 系统级推送

原生后台轮询仍作为降级链路，但 OriginOS 可以冻结或终止 App 进程。要在
App 已退出时仍由手机系统秒级唤醒通知，需要在 vivo 开放平台创建推送应用：

1. 应用包名固定为 `com.yoyo.xiaoyou`，使用稳定的正式签名登记应用。
2. 服务器 `.env` 配置上面的 App ID、App Key 和 App Secret；Secret 不得写入
   APK、Gradle 文件或 Git。
3. 构建 APK 的电脑只注入 App ID 与 App Key：

```powershell
$env:XIAOYOU_VIVO_PUSH_APP_ID="你的AppID"
$env:XIAOYOU_VIVO_PUSH_APP_KEY="你的AppKey"
cd xiaoyou-app
flutter build apk --release
```

4. 安装后在「系统设置 → vivo 系统级推送」中阅读隐私说明并明确同意。

注册成功后，服务器在消息事务提交后把 `action_id` 放入有界后台队列，再调用
vivo 系统长连接。推送只是唤醒信号，完整消息仍保存在
`data/app_channel/app.db`；推送接口失败不会阻塞回复，也不会丢消息。

Android 同时保持一个低优先级前台长轮询连接。服务器会为每个 `action_id`
返回 `pending / accepted / failed`：vivo 接受消息时客户端只推进收件箱游标，
不再弹第二条本地通知；审核中、凭证失效、队列拥塞或接口失败时，客户端立即用
同一条持久化事件生成本地通知。这样 vivo 审核通过前后都不需要更换通知逻辑，
也不会因为“已取得 RegID 但正式消息被拒绝”而漏提醒。

vivo 推送 SDK 只有在用户明确同意后才初始化。它会按照 vivo SDK 的要求处理
设备推送标识、设备类型和系统版本，唯一用途是系统消息提醒。

重新创建容器：

```bash
cd /opt/cow-legacy
docker compose \
  -f docker-compose.yml \
  -f docker-compose.app.yml \
  up -d --build --force-recreate chatgpt-on-wechat
```

成功日志包含：

```text
[AppChannel] inited ... provider=volcengine tts_ready=True text_voice_decision=True voice_route=qwen3.7-plus
```

## HTTPS 反向代理

`docker-compose.app.yml` 只把服务映射到宿主机 `127.0.0.1:8787`。
公网必须使用 HTTPS。Nginx 路径示例：

```nginx
location /xiaoyou-app/ {
    proxy_pass http://127.0.0.1:8787/;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 150s;
    proxy_send_timeout 120s;
    client_max_body_size 8m;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

`client_max_body_size` 必须在该 `location` 中覆盖站点较小的全局值，否则
录音会在到达 AppChannel 前被 Nginx 以 413 拒绝。

检查：

```bash
nginx -t
systemctl reload nginx
curl https://你的域名/xiaoyou-app/v1/health
```

## HTTP 协议

健康检查以外的接口均要求：

```http
Authorization: Bearer <XIAOYOU_APP_TOKEN>
```

### 注册设备

```http
POST /v1/devices
Content-Type: application/json

{
  "device_id": "yoyo-phone",
  "platform": "android",
  "push_token": ""
}
```

### 提交文字

```http
POST /v1/messages
Content-Type: application/json

{
  "message_id": "每次输入生成的唯一ID",
  "device_id": "yoyo-phone",
  "client_sequence": 18,
  "created_at": 1784780000,
  "text": "在干嘛呀"
}
```

相同 `message_id` 重试不会重复触发模型或记忆写入。

### 提交语音

```http
POST /v1/voice-messages
Content-Type: audio/mp4
X-Message-Id: voice-唯一ID
X-Device-Id: yoyo-phone
X-Audio-Duration-Ms: 2300
X-Client-Sequence: 19
X-Client-Created-At: 1784780010

<原始 M4A/AAC 字节>
```

服务端最多接收 6 MiB；客户端当前限制最长 60 秒。成功响应包含转写文本、
持久化 `media_id`、MIME 和时长。重试同一 ID 返回原结果，不生成第二轮回复。

### 提交图片或表情包

```http
POST /v1/image-messages
Content-Type: image/png
X-Message-Id: image-唯一ID
X-Message-Kind: image
X-Device-Id: yoyo-phone
X-Client-Sequence: 20
X-Client-Created-At: 1784780020

<原始图片字节>
```

`X-Message-Kind` 可为 `image` 或 `sticker`；支持 JPEG、PNG、WebP 和 GIF，
默认单张最大 8 MiB。图片保存到 `data/app_channel/media/`，并作为
`ContextType.IMAGE` 进入现有 QwenVision 理解链路，不会新建第二套图片判断。

### 拉取事件和历史

```http
GET /v1/events?device_id=yoyo-phone&after=0&limit=100
GET /v1/history?device_id=yoyo-phone&limit=200
GET /v1/media/<media_id>?device_id=yoyo-phone
```

事件 `kind` 可以是 `text`、`image`、`sticker` 或 `voice`。语音事件同时带有
`text`（转写/对应回复）、`media_id`、`mime_type` 和 `duration_ms`。

### 提交送达终态

```http
POST /v1/deliveries/<action_id>
Content-Type: application/json

{
  "device_id": "yoyo-phone",
  "terminal_status": "complete"
}
```

只有客户端实际渲染并确认的回复才会作为“小悠确实说过的话”写入助手记忆。
语音事件的对应文本参与记忆，二进制音频本身不进入模型上下文。

### 关系轨道、共同日记和时光信笺

App 的“我们的轨道”把已加载的真实聊天、照片、语音、收藏和成就映射为可缩放
星图。额外的展示记录使用独立 SQLite，不写入长期记忆，也不参与聊天路由：

```text
GET  /v1/relationship/entries
POST /v1/relationship/journals/draft
POST /v1/relationship/journals/<entry_id>/confirm
POST /v1/relationship/capsules
POST /v1/relationship/capsules/<entry_id>/open
POST /v1/relationship/voice-memories
```

### 独立 O2.0 语音房

```text
POST /v1/voice-rooms
Content-Type: application/json

{"device_id":"yoyo-phone","title":"耳边的一会儿"}

POST /v1/voice-rooms/<room_id>/audio
Content-Type: audio/pcm
X-Device-Id: yoyo-phone

<持续发送 16 kHz / 单声道 / PCM16；推荐每包 20 ms>

GET /v1/voice-rooms/<room_id>/events?device_id=yoyo-phone&after=0&timeout=25

POST /v1/voice-rooms/<room_id>/truncate
Content-Type: application/json

{"device_id":"yoyo-phone","reply_id":"火山返回的item_id","audio_end_ms":860}

POST /v1/voice-rooms/<room_id>/finish
Content-Type: application/json

{"device_id":"yoyo-phone"}

GET /v1/voice-rooms?device_id=yoyo-phone&limit=30
GET /v1/voice-rooms/<room_id>?device_id=yoyo-phone
```

客户端在同一个房间内并行上传连续音频流和长轮询事件。`events` 会依次返回
识别开始、用户转写、小悠思考、回复转写、PCM 音频、被打断和轮次结束事件；
音频由 Android 原生 `AudioTrack` 边收边播。每个房间的逐轮转写、送达终态和
异步记忆状态都只存在独立语音房数据库中。

“我们今天”先以当天真实对话生成草稿。代表原话必须逐字存在于当天聊天中，
代表照片必须来自当天已有 `media_id`；模型结果无法通过事实校验时退回本地
统计摘要。只有 YoYo 确认后状态才变为 `confirmed`。

时光信笺在 `unlock_at` 之前不会向客户端返回正文。连续语音房间只保存开始、
结束、时长和来回数作为纪念卡，不保存第二份整段通话录音。

Android 回退通知使用系统 Conversation API：小悠会作为长效会话联系人出现，
支持通知栏直接回复、优先会话、动态桌面快捷方式、未读角标和独立通知声道。
vivo 厂商通知仍负责进程完全退出后的可靠到达；系统能力是否展示由手机 ROM
和用户通知设置共同决定。

## 数据和影响

运行数据位于：

```text
data/app_channel/app.db
data/app_channel/relationship.db
data/app_channel/media/
```

升级时 SQLite 会自动增加语音字段，不需要删除旧数据库。影响如下：

- 语音识别会增加一次 ASR 调用费用和等待时间。
- 每个可发送回复的 App 文字回合会增加一次轻量媒介模型调用；它只选择
  `text` 或 `voice`，不改写小悠回复。默认关闭思考，失败时直接使用文字。
- 语音回复会增加一次火山 Seed-TTS 2.0 音频生成 HTTP 调用；接口返回 MP3，
  默认使用 `loudness_rate=100` 生成 2 倍音量，TTS 完成后 App 才收到可播放
  事件。
- 若火山凭证缺失或 TTS 失败，App 会收到同一条真实文字回复；语音输入、
  文字聊天和记忆链路不受影响。
- 音频会占用 `data/` 磁盘空间，当前没有自动过期清理。
- 图片和表情包同样占用 `data/` 磁盘空间，并会增加一次视觉模型调用。
- App 语音输入以及模型选择语音的文字回合不经过文字 SplitReply，以保证
  整段回复使用同一语音气泡；模型选择文字时仍保持原有文字行为。
- Android 优先使用 vivo 系统长连接推送，同时保留声明为 `remoteMessaging`
  的原生前台长轮询服务。`/v1/events?wait=25` 在没有新消息时阻塞等待，通常
  不产生固定频率空请求；有消息时由服务端立即唤醒。
- vivo 返回接受状态时前台服务只推进游标，不产生重复通知；审核中、凭证缺失、
  用户未同意、设备不支持或发送失败时，前台服务自动显示本地会话通知。
  Flutter 进入后台后停止自身轮询，避免与原生服务重复。
- 代价是通知栏会保留一个低优先级的“小悠后台提醒”常驻项，并有少量持久在线
  连接耗电；在没有厂商正式推送资质时，这是及时性和可控性更高的方案。OriginOS
  仍可能在用户手动强行停止 App 后终止它，建议允许自启动并把电池策略设为
  “不限制”。
- Android 通知权限由原生 Activity 查询和请求；App 内开关只暂停或恢复后台提醒，不会撤销系统权限。
  已授权时再次开启会直接启动后台服务，不再跳转设置。设置页会显示系统实际授权状态并提供测试通知。
- 客户端区分“Android 系统权限”“App 内提醒偏好”和“后台服务状态”。系统权限关闭时不再覆盖用户偏好，
  重新授权并返回 App 后会按原偏好恢复服务。
- 图片预览可通过 Android MediaStore 或 iOS Photos 保存到系统相册；权限仅在用户点击保存时请求。
- 关闭 `XIAOYOU_APP_VOICE_ENABLED` 只停用 App 语音，不影响文字聊天和微信。
- 关闭 `XIAOYOU_APP_TEXT_VOICE_DECISION_ENABLED` 后，App 文字输入只回文字，
  语音输入仍然回语音。
