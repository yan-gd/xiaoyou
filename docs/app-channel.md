# 小悠 App 通道

App 通道是现有微信通道的第二个传输适配器，不是第二套小悠。服务器仍是人格、模型、短期记忆、长期记忆、提醒和生活照的唯一事实源。

## 运行关系

```text
Flutter App ── HTTPS ── AppChannel ─┐
                                    ├─ ChatChannel.produce
微信 ───────────── WeChat Channel ──┘
                                          │
                                          ├─ 现有插件与模型
                                          ├─ data 下的记忆和状态
                                          └─ 按来源渠道发送
```

App 的普通文字输入继续经过现有的：

- 连续输入合并
- `input_version` 与 stale 取消
- ShortMemory、ConversationArchive 与长期记忆治理
- XiaoyouChat、MCP、生活照和回复拆分

没有增加关键词、正则或硬编码语义路由。协议层只校验长度、身份、消息 ID 和送达状态。

## 开启服务

先在服务器项目的 `.env` 增加：

```dotenv
XIAOYOU_APP_ENABLED=true
XIAOYOU_APP_TOKEN=替换为长随机值
XIAOYOU_APP_DEFAULT_PROACTIVE=false
```

生成 256-bit 随机令牌：

```bash
openssl rand -hex 32
```

重新创建容器：

```bash
cd /opt/cow-legacy
docker compose \
  -f docker-compose.yml \
  -f docker-compose.app.yml \
  up -d --build --force-recreate chatgpt-on-wechat
docker compose \
  -f docker-compose.yml \
  -f docker-compose.app.yml \
  logs --since=5m chatgpt-on-wechat \
  | grep -E "AppChannel|ERROR|Traceback"
```

成功日志应包含：

```text
[AppChannel] inited bind=0.0.0.0:8787 database=/app/data/app_channel/app.db session=yoyo
```

宿主机检查：

```bash
curl http://127.0.0.1:8787/v1/health
ls -lh /opt/cow-legacy/data/app_channel/app.db
```

## HTTPS 反向代理

App 覆盖文件 `docker-compose.app.yml` 只把端口映射到 `127.0.0.1`，不会直接暴露公网。以下 Nginx 路径示例假设域名已有有效 TLS 证书：

```nginx
location /xiaoyou-app/ {
    proxy_pass http://127.0.0.1:8787/;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 40s;
    client_max_body_size 1m;

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

检查并重载：

```bash
nginx -t
systemctl reload nginx
```

公网检查：

```bash
curl https://你的域名/xiaoyou-app/v1/health
```

App 中填写的服务地址为：

```text
https://你的域名/xiaoyou-app
```

## HTTP 协议

除健康检查外，全部接口都需要：

```http
Authorization: Bearer <XIAOYOU_APP_TOKEN>
Content-Type: application/json
```

### 注册设备

```http
POST /v1/devices

{
  "device_id": "yoyo-phone",
  "platform": "android",
  "push_token": ""
}
```

### 提交文字

```http
POST /v1/messages

{
  "message_id": "每次输入生成的唯一ID",
  "device_id": "yoyo-phone",
  "client_sequence": 18,
  "created_at": 1784780000,
  "text": "在干嘛呀"
}
```

相同 `message_id` 重试只返回 `duplicate=true`，不会重复触发模型或写入记忆。

### 拉取事件

```http
GET /v1/events?device_id=yoyo-phone&after=0&limit=100
```

响应中的 `sequence` 是设备事件游标。客户端持有最大值并在下次请求中作为 `after`。

服务端也提供最长约 25 秒的 SSE 长轮询：

```http
GET /v1/events/stream?device_id=yoyo-phone&after=42
Accept: text/event-stream
```

### 提交送达终态

全部显示：

```http
POST /v1/deliveries/<action_id>

{
  "device_id": "yoyo-phone",
  "terminal_status": "complete"
}
```

只显示部分：

```http
POST /v1/deliveries/<action_id>

{
  "device_id": "yoyo-phone",
  "terminal_status": "partial",
  "event_ids": ["实际显示的event_id"]
}
```

终态一旦进入 `complete`、`partial`、`failed` 或 `cancelled` 就不可改变。只有确认显示的文字会进入助手短期记忆、长期记忆治理和 RecentState。

### 聊天历史与媒体

```http
GET /v1/history?device_id=yoyo-phone&limit=200
GET /v1/media/<media_id>?device_id=yoyo-phone
```

本地生成图片会复制到 `data/app_channel/media/` 后通过鉴权接口读取，不会向客户端泄露 `/app/data/...` 服务器路径。

## 数据与影响

新增的运行数据全部位于：

```text
data/app_channel/app.db
data/app_channel/media/
```

启用后的影响：

- 多一个很轻量的 HTTP 线程和共享会话消费者。
- 不叠加 `docker-compose.app.yml` 时，主 Compose 不发布任何新端口，不影响现有微信容器启动。
- App 与微信共用 `yoyo`，因此两端能够延续同一段关系和记忆。
- App 收到的拆分回复不再执行微信式逐字延迟，会更快进入客户端收件箱。
- App 未提交送达终态时，助手回复不会进入助手记忆；用户原话仍会正常提交。
- 主动动作如果明确继承了 `app:<device_id>` 收件人，会回到 App。
- 没有明确来源的主动消息默认仍走微信，不会因为注册过 App 就悄悄改变渠道；将 `XIAOYOU_APP_DEFAULT_PROACTIVE=true` 后才会优先进入最近 App 设备收件箱。
- App 在后台时还没有 FCM/APNs 系统通知，消息会可靠保存在收件箱并在下次打开时显示。

当前令牌适合单用户自用。正式分发或多人使用前，必须升级为账户登录、一次性配对、独立设备凭证、撤销机制、速率限制和严格的数据隔离。

## 回退

无需删除数据库，只要在 `.env` 中设置：

```dotenv
XIAOYOU_APP_ENABLED=false
```

然后重新创建容器。微信聊天、现有 `data` 记忆和所有状态不受影响。
