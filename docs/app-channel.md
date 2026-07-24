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
- 只有经过认证的 App 语音输入会设置 App 语音回复标记。
- 小悠回复使用 `cosyvoice-v3-flash` 与 `longyan_v3` 合成。
- 微信的 `SPEECH_RECOGNITION` 和 `VOICE_REPLY_VOICE` 保持关闭。
- TTS 失败时退回真实文本回复，不丢失模型结果。

## 开启服务

服务器 `.env`：

```dotenv
XIAOYOU_APP_ENABLED=true
XIAOYOU_APP_TOKEN=替换为长随机值
XIAOYOU_APP_DEFAULT_PROACTIVE=false
XIAOYOU_APP_VOICE_ENABLED=true
```

生成随机令牌：

```bash
openssl rand -hex 32
```

语音服务复用现有百炼 `KEY`。Compose 中的默认值为：

```text
XIAOYOU_APP_ASR_MODEL=qwen3-asr-flash
XIAOYOU_APP_TTS_MODEL=cosyvoice-v3-flash
XIAOYOU_APP_TTS_VOICE=longyan_v3
```

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
[AppChannel] inited ... voice=True asr=qwen3-asr-flash tts=cosyvoice-v3-flash
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

## 数据和影响

运行数据位于：

```text
data/app_channel/app.db
data/app_channel/media/
```

升级时 SQLite 会自动增加语音字段，不需要删除旧数据库。影响如下：

- 语音识别会增加一次 ASR 调用费用和等待时间。
- 语音回复会增加一次 CosyVoice 调用；TTS 完成后 App 才收到可播放事件。
- 音频会占用 `data/` 磁盘空间，当前没有自动过期清理。
- 图片和表情包同样占用 `data/` 磁盘空间，并会增加一次视觉模型调用。
- App 语音回合不经过文字 SplitReply，以保证整段回复使用同一语音气泡。
- App 被系统彻底结束后仍没有 FCM/APNs 通知，消息会保存在收件箱并在下次打开时显示。
- App 进程仍在后台运行时，客户端约每 15 秒轮询并通过本地系统通知提示新消息；系统暂停或杀死进程后
  不保证即时通知。完整离线推送仍需要额外接入 FCM/APNs。
- Android 通知授权失败或不再弹出权限框时，客户端会提供系统通知设置入口；权限调用异常不会阻塞设置页。
- 关闭 `XIAOYOU_APP_VOICE_ENABLED` 只停用 App 语音，不影响文字聊天和微信。
