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

App 的文字和语音转写都会进入现有连续输入、记忆治理和模型回复链路。
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
`data/app_channel/app.db`；推送接口失败不会阻塞回复，也不会丢消息。系统推送
生效时 Android 会停用 4 秒轮询，避免双重通知与常驻耗电；凭证缺失、设备不
支持或注册失败时自动继续使用原生前台服务。

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
    proxy_read_timeout 90s;
    proxy_send_timeout 90s;
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
- Android 优先使用 vivo 系统长连接推送；注册成功后，即使 App 进程退出也由
  系统投递通知，不再依赖 App 自己每 4 秒醒来。
- 未配置 vivo 凭证、用户未同意、设备不支持或注册失败时，声明为
  `remoteMessaging` 的原生前台服务继续约每 4 秒读取一次 `/v1/events`。
  Flutter 进入后台后停止自身轮询，避免重复通知。
- Android 通知权限由原生 Activity 查询和请求；App 内开关只暂停或恢复后台提醒，不会撤销系统权限。
  已授权时再次开启会直接启动后台服务，不再跳转设置。设置页会显示系统实际授权状态并提供测试通知。
- 客户端区分“Android 系统权限”“App 内提醒偏好”和“后台服务状态”。系统权限关闭时不再覆盖用户偏好，
  重新授权并返回 App 后会按原偏好恢复服务。
- 图片预览可通过 Android MediaStore 或 iOS Photos 保存到系统相册；权限仅在用户点击保存时请求。
- 关闭 `XIAOYOU_APP_VOICE_ENABLED` 只停用 App 语音，不影响文字聊天和微信。
- 关闭 `XIAOYOU_APP_TEXT_VOICE_DECISION_ENABLED` 后，App 文字输入只回文字，
  语音输入仍然回语音。
