# 小悠 Xiaoyou v1.1

<p align="center">
  <img src="assets/xiaoyou-avatar.jpg" alt="小悠头像" width="320" />
</p>

> 一个基于个人微信小号、chatgpt-on-wechat、Qwen / 阿里云百炼的微信 AI 伴侣项目。

小悠不是公众号机器人，也不是企业微信机器人，而是运行在个人微信小号上的 AI 伴侣。她可以像普通微信联系人一样和用户聊天，支持文字聊天、图片理解、长期记忆、短期记忆、主动找用户、定时提醒、多气泡分段发送，以及联网搜索、天气、地图、时间等工具调用能力。

本项目基于旧版 `chatgpt-on-wechat:1.7.3` 改造，核心目标不是做一个“问答机器人”，而是尽量接近真实微信聊天体验：她能记事、能等你补话、会避开打扰时间、能自然分段回复，也能在需要时查信息。

---

## 项目定位

小悠 v1.1 关注的是“微信日常陪伴感”，而不是单纯的接口问答。

她应该做到：

- 不是只会一问一答
- 不会所有回复都挤在一个大气泡里
- 可以记住用户长期偏好和重要信息
- 可以保留最近几天的短期上下文
- 可以看图，并结合用户后续补充一起理解
- 可以隔一段时间主动找用户
- 可以根据用户设定的时间主动提醒
- 可以在提醒之后自然接上“关啦 / 好了 / 起了”等回复
- 可以在需要时调用 MCP 工具查询搜索、天气、地图和时间

---

## 当前版本

```text
当前版本：v1.1
基础镜像：zhayujie/chatgpt-on-wechat:1.7.3
主模型：qwen3.7-plus
部署方式：Docker Compose
微信通道：个人微信 wx / itchat
```

---

## v1.1 主要变化

相较于 v1.0，v1.1 重点做了这些增强：

- 移除废弃 `timetask` 插件
- 长期记忆改为模型判断是否值得记忆
- 新增 `ShortMemory` 短期记忆插件
- 分段回复加入按字符长度延迟
- 图片理解改为等待图片后续文字，并合并多条补充消息
- 主动消息加入免打扰时间段和反重复机制
- 新增 `XiaoyouMCP` 工具插件
- 增加插件目录权限问题处理说明
- 更新数据文件和测试清单

---

## 功能特性

### 1. 个人微信小号接管

通过 `itchat` / Web 微信协议登录个人微信小号，让 AI 以微信联系人的形式与用户聊天。

当前通道：

```yaml
CHANNEL_TYPE: 'wx'
```

启动后查看日志并扫码：

```bash
docker logs -f cow-legacy
```

---

### 2. Qwen3.7 Plus 主聊天模型

主聊天模型使用阿里云百炼 / DashScope 的 OpenAI 兼容接口。

核心配置：

```yaml
MODEL: 'qwen3.7-plus'
OPEN_AI_API_BASE: 'https://dashscope.aliyuncs.com/compatible-mode/v1'
OPEN_AI_API_KEY: '${KEY}'
```

`.env` 示例：

```env
KEY=your_dashscope_api_key_here
```

---

### 3. 关闭 Qwen 思考模式

Qwen 部分模型默认可能进入 thinking / reasoning 模式，导致回复延迟变长。

本项目对 CoW 原始 `chat_gpt_bot.py` 做了补丁，给 Qwen 请求注入：

```python
args["enable_thinking"] = False
```

对应环境变量：

```yaml
ENABLE_THINKING: 'false'
```

核心补丁文件：

```text
patches/chat_gpt_bot.py
```

---

### 4. 小悠人格

小悠被设计为一个微信 AI 伴侣角色：

- 温柔
- 俏皮
- 会撒娇
- 有一点毒舌
- 不是无脑讨好
- 用户难过时会认真安慰
- 普通聊天时更像微信里的亲密对象

人格通过 `CHARACTER_DESC` 配置注入。

---

## 插件说明

### QwenVision：图片理解插件

路径：

```text
plugins/qwen_vision
```

能力：

- 接收微信图片
- 下载图片到临时目录
- 将图片转为 base64
- 调用 Qwen 多模态接口理解图片
- 支持图片缓存
- 支持“图片 + 后续文字问题”联合理解
- v1.1 支持等待后续文字，并合并多条补充消息

v1.0 的图片逻辑更偏向“发图后等待下一条问题”；v1.1 进一步优化为：

- 收到图片后先等待一小会儿
- 如果用户没有补充文字，就根据图片自然回复
- 如果用户继续发文字，就继续短暂等待
- 多条补充消息会合并理解后再回复
- 回复重点不再是机械描述，而是像女朋友一样给反应、判断、调侃、关心或追问

相关配置：

```yaml
VISION_MODEL: 'qwen3.7-plus'
VISION_IMAGE_TTL: '180'
VISION_IMAGE_WAIT_SECONDS: '5.0'
VISION_TEXT_SETTLE_SECONDS: '3.0'
VISION_CHECK_INTERVAL: '1.0'
VISION_MAX_FOLLOWUP_MESSAGES: '6'
VISION_MAX_FOLLOWUP_CHARS: '500'
```

提示词方向：

- 不要以“这张图里”“画面中”“我看到”开头
- 不要为了证明看懂而铺陈画面
- 如果要提细节，只挑 1 到 2 个关键点
- 默认 1 到 3 句，像微信聊天
- 自拍、头像、穿搭、截图、文字图分别按不同场景自然回应

示例：

```text
用户：发送头像图
用户：这个好看吗？
小悠：结合图片评价头像，而不是只机械描述画面
```

---

### MemoryLite：长期记忆插件

路径：

```text
plugins/memory_lite
```

v1.0 中，长期记忆主要依赖手动指令：

```text
记住：我喜欢粉色二次元头像
你记得我什么
查看记忆
忘记：粉色头像
清空记忆
```

v1.1 中，`MemoryLite` 不再只依赖简单关键词判断，而是改为由语言模型判断当前聊天内容是否值得写入长期记忆，例如：

- 用户明确说“你记住”
- 用户长期偏好、习惯、重要信息
- 对两人关系有持续影响的事实
- 需要以后主动提起或避免踩雷的内容

本版本没有关键词兜底逻辑，是否记忆完全交给模型判断。

相关配置：

```yaml
MEMORY_ENABLED: 'true'
MEMORY_AUTO_CAPTURE: 'true'
MEMORY_CAPTURE_USE_LLM: 'true'
MEMORY_CAPTURE_MODEL: 'qwen3.7-plus'
MEMORY_CAPTURE_MAX_PER_MESSAGE: '3'
MEMORY_CAPTURE_EXISTING_TOP_N: '30'
MEMORY_MAX_ITEMS: '80'
MEMORY_INJECT_TOP_N: '20'
```

记忆文件：

```text
plugins/memory_lite/memory.json
```

该文件包含私人信息，不应提交到 GitHub。

---

### ShortMemory：短期记忆插件

路径：

```text
plugins/short_memory
```

v1.1 新增 `ShortMemory` 插件，用于保留最近聊天中的短期上下文。

短期记忆定位为“七天内的模糊记忆”：

- 最近 24 小时保留较清晰的原始对话片段
- 超过 24 小时后逐步压缩成摘要
- 摘要最长保留 7 天
- 时间越久，注入给模型的内容越概括

这样小悠不会像失忆一样只看当前一句话，也不会把所有短期闲聊永久写进长期记忆。

相关配置：

```yaml
SHORT_MEMORY_ENABLED: 'true'
SHORT_MEMORY_MAX_MESSAGES: '60'
SHORT_MEMORY_RAW_TTL_SECONDS: '86400'
SHORT_MEMORY_INJECT_MESSAGES: '24'
SHORT_MEMORY_INJECT_MAX_CHARS: '2200'
SHORT_MEMORY_SUMMARY_ENABLED: 'true'
SHORT_MEMORY_SUMMARY_MODEL: 'qwen3.7-plus'
SHORT_MEMORY_SUMMARY_TTL_SECONDS: '604800'
SHORT_MEMORY_SUMMARY_MIN_MESSAGES: '8'
SHORT_MEMORY_MAX_SUMMARIES: '8'
SHORT_MEMORY_PENDING_ARCHIVE_MAX: '80'
```

数据文件：

```text
plugins/short_memory/short_memory.json
```

该文件包含短期聊天上下文，不应提交到 GitHub。

---

### SplitReply：多气泡分段发送插件

路径：

```text
plugins/split_reply
```

大模型回复常常是一整段，但真实微信聊天更像一句一句发。

`SplitReply` 会把模型回复拆成多个微信气泡，并在发送之间加入延迟。

v1.0 中延迟主要是固定随机延迟；v1.1 中加入按字符长度延迟：

```yaml
SPLIT_REPLY_DELAY_PER_CHAR: '0.2'
```

含义：每一段发送前，根据上一段文字长度计算等待时间。比如上一段 20 个字符，则下一段大约等待 4 秒再发送，更接近真人打字节奏。

相关配置：

```yaml
SPLIT_REPLY_ENABLED: 'true'
SPLIT_REPLY_MIN_LEN: '18'
SPLIT_REPLY_MAX_CHARS: '28'
SPLIT_REPLY_MAX_PARTS: '6'
SPLIT_REPLY_DELAY_PER_CHAR: '0.2'
SPLIT_REPLY_TINY_MERGE: '6'
```

该功能依赖核心补丁：

```text
patches/chat_channel.py
```

补丁作用：当 `SplitReply` 已经手动发送多个小气泡后，阻止 CoW 再发送原始完整大段回复。

---

### ProactiveLove：主动找用户插件

路径：

```text
plugins/proactive_love
```

传统机器人只能被动回答，`ProactiveLove` 让小悠能在用户长时间没说话时主动发消息。

能力：

- 记录最后互动时间
- 超过指定空闲时间后主动发微信
- 支持每日主动次数限制
- 支持冷却时间
- v1.1 支持免打扰时间段
- v1.1 支持记录最近主动消息，减少固定开头反复出现
- 支持使用 Qwen 根据小悠人格生成主动消息
- 支持读取长期记忆，让主动消息更贴近用户

相关配置：

```yaml
TZ: 'Asia/Shanghai'
PROACTIVE_ENABLED: 'true'
PROACTIVE_IDLE_SECONDS: '3600'
PROACTIVE_CHECK_INTERVAL: '300'
PROACTIVE_COOLDOWN_SECONDS: '7200'
PROACTIVE_MAX_PER_DAY: '6'
PROACTIVE_QUIET_HOURS: '02:30-08:00'
PROACTIVE_RECENT_TEXTS_MAX: '8'
PROACTIVE_USE_LLM: 'true'
PROACTIVE_MODEL: 'qwen3.7-plus'
PROACTIVE_MEMORY_TOP_N: '10'
PROACTIVE_PROBABILITY: '0.75'
```

说明：

```text
PROACTIVE_QUIET_HOURS
```

表示“不主动打扰”的时间段，不是允许主动发消息的时间段。支持 `02:30-08:00` 和 `2.30-8.00` 两种写法。

状态文件：

```text
plugins/proactive_love/proactive_state.json
```

该文件包含互动状态，不应提交到 GitHub。

---

### ReminderLove：定时提醒插件

路径：

```text
plugins/reminder_love
```

用于实现类似：

```text
小悠，明天9点提醒我起床
小悠，30秒后提醒我关火
10分钟后提醒我喝水
后天晚上8点叫我打游戏
```

能力：

- 识别中文自然语言提醒
- 支持相对时间
- 支持明天 / 后天 / 具体日期
- 支持几点 / 几点半 / 9.0 / 9:30
- 到时间主动发微信提醒
- 提醒确认语使用 Qwen 生成，避免机器味
- 提醒触发后保存上下文
- 用户回复“关啦 / 好了 / 起了”时，可以知道是在回应刚刚的提醒

支持指令：

```text
提醒我20秒后喝水
小悠30秒后提醒我去关火
明天9点提醒我起床
后天晚上8点叫我打游戏
提醒列表
取消提醒1
取消提醒
```

相关配置：

```yaml
REMINDER_ENABLED: 'true'
REMINDER_CHECK_INTERVAL: '15'
REMINDER_USE_LLM: 'true'
REMINDER_MODEL: 'qwen3.7-plus'
REMINDER_MEMORY_TOP_N: '10'
REMINDER_ACK_USE_LLM: 'true'
REMINDER_FOLLOWUP_CONTEXT_SECONDS: '900'
```

提醒文件：

```text
plugins/reminder_love/reminders.json
```

该文件包含私人提醒，不应提交到 GitHub。

---

### XiaoyouMCP：联网工具插件

路径：

```text
plugins/xiaoyou_mcp
```

v1.1 新增 `XiaoyouMCP` 插件，让小悠可以在自然聊天中调用魔搭社区 MCP 广场的 Hosted MCP 服务。

当前接入方向：

- 必应中文搜索：联网搜索、实时信息查询
- 高德地图：天气查询、地点搜索、路线规划、地图导航
- 时间服务：当前时间、时区时间

小悠会根据用户消息判断是否需要调用工具，例如：

```text
帮我查一下今天有什么新闻
明天杭州天气怎么样
从这里到某个地方怎么走
现在纽约几点
```

相关配置：

```yaml
XIAOYOU_MCP_ENABLED: 'true'
XIAOYOU_MCP_MODE: 'streamable_http'

XIAOYOU_MCP_SEARCH_ENDPOINT: '必应中文搜索 Hosted MCP URL'
XIAOYOU_MCP_AMAP_ENDPOINT: '高德地图 Hosted MCP URL'
XIAOYOU_MCP_TIME_ENDPOINT: '时间服务 Hosted MCP URL'

XIAOYOU_MCP_TOKEN: ''
XIAOYOU_MCP_AUTH_HEADER: 'Authorization'
XIAOYOU_MCP_AUTH_SCHEME: 'Bearer'
XIAOYOU_MCP_TIMEOUT: '45'

XIAOYOU_MCP_AUTO_SCHEMA: 'true'
XIAOYOU_MCP_ARG_MODEL: 'qwen3.7-plus'
XIAOYOU_MCP_POLISH_REPLY: 'true'
XIAOYOU_MCP_POLISH_MODEL: 'qwen3.7-plus'

XIAOYOU_MCP_SEARCH_TOOL: 'bing_search'
XIAOYOU_MCP_AMAP_WEATHER_TOOL: 'maps_weather'
XIAOYOU_MCP_AMAP_ROUTE_TOOL: 'maps_direction_driving'
XIAOYOU_MCP_AMAP_SEARCH_TOOL: 'maps_text_search'
XIAOYOU_MCP_TIME_CURRENT_TOOL: 'get_current_time'

XIAOYOU_DEFAULT_LOCATION: ''
XIAOYOU_MAP_DEFAULT_ORIGIN: ''
XIAOYOU_TIMEZONE: 'Asia/Shanghai'
```

注意：

- Hosted MCP URL 属于专属连接地址，不建议公开泄露
- 如果 URL 本身已经包含鉴权信息，`XIAOYOU_MCP_TOKEN` 可以留空
- 如果服务商额外要求 token，再填写 token 和鉴权头配置

---

## 插件加载顺序

v1.1 当前启用的主要插件包括：

```text
SplitReply
QwenVision
ReminderLove
XiaoyouMCP
MemoryLite
ShortMemory
ProactiveLove
```

已移除：

```text
timetask
```

提醒相关能力统一交给 `ReminderLove` 处理，避免多个定时 / 提醒插件重复触发、逻辑打架。

---

## 项目结构

```text
.
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .gitignore
├── patches
│   ├── chat_gpt_bot.py
│   └── chat_channel.py
└── plugins
    ├── qwen_vision
    ├── memory_lite
    ├── short_memory
    ├── split_reply
    ├── proactive_love
    ├── reminder_love
    ├── xiaoyou_mcp
    └── ...
```

核心目录说明：

```text
patches/
```

存放对原版 chatgpt-on-wechat 容器的核心补丁。

```text
plugins/qwen_vision
```

图片理解插件。

```text
plugins/memory_lite
```

长期记忆插件。

```text
plugins/short_memory
```

短期记忆插件。

```text
plugins/split_reply
```

多气泡分段发送插件。

```text
plugins/proactive_love
```

主动找用户插件。

```text
plugins/reminder_love
```

定时提醒插件。

```text
plugins/xiaoyou_mcp
```

MCP 工具调用插件。

---

## 部署方式

项目推荐部署在云服务器路径：

```bash
/opt/cow-legacy
```

### 1. 克隆仓库

```bash
git clone https://github.com/yan-gd/xiaoyou.git
cd xiaoyou
```

如果你希望沿用服务器旧路径：

```bash
cd /opt
git clone https://github.com/yan-gd/xiaoyou.git cow-legacy
cd /opt/cow-legacy
```

### 2. 配置 API Key

```bash
cp .env.example .env
nano .env
```

填写：

```env
KEY=your_dashscope_api_key_here
```

### 3. 构建镜像

```bash
docker build -t cow-legacy-local:vision-no-think .
```

### 4. 启动容器

```bash
docker compose up -d --force-recreate
```

### 5. 查看日志并扫码登录

```bash
docker logs -f cow-legacy
```

看到二维码链接后，使用微信小号扫码登录。

---

## 常用命令

### 查看运行状态

```bash
docker ps
```

### 查看日志

```bash
docker logs -f cow-legacy
```

### 重启

```bash
docker restart cow-legacy
```

### 重建

```bash
docker compose up -d --force-recreate
```

### 重新构建镜像

当修改了 `patches/` 或 `Dockerfile` 时，需要重新 build：

```bash
docker build -t cow-legacy-local:vision-no-think .
docker compose up -d --force-recreate
```

### 保存当前容器为镜像

```bash
docker commit cow-legacy cow-legacy-local:xiaoyou-v1.1
docker tag cow-legacy-local:xiaoyou-v1.1 cow-legacy-local:vision-no-think
```

### 检查 no-thinking 补丁

```bash
docker exec -it cow-legacy grep -n "ENABLE_THINKING\|enable_thinking\|ChatCompletion.create" -B 5 -A 8 /app/bot/chatgpt/chat_gpt_bot.py
```

### 检查分泡拦截补丁

```bash
docker exec cow-legacy grep -n "reply sending cancelled by plugin\|original reply cancelled by SplitReply" -B 5 -A 8 /app/channel/chat_channel.py
```

---

## 本地开发与上传服务器

如果你在本地修改代码，推荐流程：

```text
本地修改代码
→ git commit
→ git push 到 GitHub
→ 服务器 git pull
→ 按修改内容决定是否重新 build
```

判断表：

```text
改 plugins/                → docker compose up -d --force-recreate
改 docker-compose.yml      → docker compose up -d --force-recreate
改 patches/                → docker build ... 然后 docker compose up -d --force-recreate
改 Dockerfile              → docker build ... 然后 docker compose up -d --force-recreate
改 .env                    → docker compose up -d --force-recreate
```

---

## 插件目录权限问题

如果插件目录出现权限问题，例如：

```text
PermissionError: [Errno 13] Permission denied: './plugins/plugins.json'
```

通常是因为本地上传文件后，宿主机 `plugins/` 目录或 `plugins/plugins.json` 对容器内进程不可写。

可以执行：

```bash
cd /opt/cow-legacy
docker compose down
chmod -R a+rwX plugins
touch plugins/plugins.json
chmod 666 plugins/plugins.json
docker compose up -d --force-recreate
docker logs -f cow-legacy
```

如果仍然报权限问题，可以临时放宽权限：

```bash
cd /opt/cow-legacy
docker compose down
find plugins -type d -exec chmod 777 {} \;
find plugins -type f -exec chmod 666 {} \;
docker compose up -d --force-recreate
docker logs -f cow-legacy
```

---

## 数据文件说明

以下数据会写在插件目录中，建议保留 `./plugins:/app/plugins` 挂载，避免容器重建后丢失：

```text
plugins/memory_lite/memory.json
plugins/short_memory/short_memory.json
plugins/proactive_love/proactive_state.json
plugins/reminder_love/reminders.json
```

这些文件包含私人记忆、短期上下文、主动消息状态和提醒任务，不应提交到 GitHub。

`.gitignore` 应至少包含：

```gitignore
.env
*.env
.env.*
plugins/memory_lite/memory.json
plugins/short_memory/short_memory.json
plugins/proactive_love/proactive_state.json
plugins/reminder_love/reminders.json
tmp/
logs/
*.log
__pycache__/
*.pyc
```

---

## 微信内测试清单

升级后建议按下面顺序测试。

### 1. 普通文字聊天

```text
小悠你在嘛
```

确认小悠能正常回复。

### 2. 长期记忆

```text
记住：我喜欢粉色二次元头像
你记得我什么
帮我挑一个头像风格
```

确认她能在后续聊天中自然使用长期记忆。

### 3. 短期记忆

连续聊几轮，再换话题，看她是否能自然承接最近上下文。

### 4. 图片消息

```text
发送一张图片
```

确认她不会机械描述画面，而是自然回应。

### 5. 图片加补充文字

```text
发送一张图片
这个好看吗？
还有这个适合当头像吗？
```

确认她会等待并合并理解多条补充消息。

### 6. 分段回复

发送容易触发长回复的话题，观察分段间隔是否自然。

### 7. 主动消息

发送：

```text
我先去忙一会儿
```

等待超过 `PROACTIVE_IDLE_SECONDS` 设置的时间，并确认免打扰时间段内不会主动联系。

### 8. MCP 查询

```text
帮我查一下今天有什么新闻
明天杭州天气怎么样
现在纽约几点
```

确认搜索、天气、时间等工具是否正常。

### 9. 提醒功能

```text
小悠30秒后提醒我去关火
提醒列表
取消提醒1
```

确认到点触发，并且触发后回复“关啦 / 好了”等能自然接上上下文。

---

## 安全注意事项

### 1. 不要提交 API Key

真实 `.env` 文件不应提交。

如果 API Key 曾经出现在日志、截图、终端历史或公开仓库中，请立即到阿里云百炼控制台轮换。

### 2. 不要提交私人记忆和提醒

以下文件包含私人信息，不应提交：

```text
plugins/memory_lite/memory.json
plugins/short_memory/short_memory.json
plugins/proactive_love/proactive_state.json
plugins/reminder_love/reminders.json
```

### 3. Hosted MCP URL 不要公开泄露

Hosted MCP URL 可能属于专属连接地址，不建议公开到 README、issue、日志或截图中。

如果 MCP 服务商额外要求 token，也不要提交相关 token。

### 4. 个人微信登录风险

本项目基于个人微信 Web 登录能力，稳定性取决于微信环境。

可能遇到：

- 登录失效
- 需要重新扫码
- Web 微信不可用
- 微信风控
- 消息收发异常

请仅用于学习、自用和实验，不建议大规模部署或商业化使用。

---

## 已知限制

### 1. 语音气泡暂未实现

当前项目没有实现真正的微信语音气泡发送。即使生成音频，也通常只能作为文件或普通音频发送，不等同于微信原生语音消息。

### 2. 主动消息不天然进入 CoW 历史

`ProactiveLove` 和 `ReminderLove` 是插件线程直接通过 `itchat.send()` 主动发送消息，因此它们不会天然进入 CoW 原始会话历史。

为了解决这个问题，`ReminderLove` 已额外实现“提醒后上下文注入”，让用户后续回复可以接上刚刚的提醒。

### 3. 视觉理解依赖多模态模型

如果 `qwen3.7-plus` 当前接口不支持图片输入，可以单独把视觉模型换成：

```yaml
VISION_MODEL: 'qwen3-vl-plus'
```

### 4. 记忆目前是轻量 JSON

长期记忆、短期记忆和提醒状态都使用本地 JSON 文件保存。优点是简单稳定，缺点是没有数据库级事务、向量检索和复杂冲突处理。

### 5. MCP 工具能力取决于 Hosted MCP 服务

MCP 工具是否可用取决于对应 Hosted MCP endpoint、鉴权配置、网络环境和服务稳定性。

---

## 版本记录

### v1.1

已完成：

- 移除废弃 `timetask`
- 长期记忆改为模型判断
- 新增七天短期模糊记忆
- 图片理解更像女朋友看图
- 图片后续文字等待和合并理解
- 分段回复加入按字符延迟
- 主动消息支持免打扰和反重复
- 新增 MCP 联网、天气、地图、时间工具
- 增加插件权限问题处理说明

### v1.0

已完成：

- 个人微信小号接管
- Qwen3.7 Plus 主聊天
- 关闭思考模式
- 女友人格
- 图片理解
- 图片 + 文字问题联合理解
- 长期记忆
- 多气泡微信分段发送
- 主动找用户
- 定时提醒
- 提醒后上下文衔接
- Dockerfile 固化核心补丁

---

## 后续计划

v1.2 可以考虑：

- 语音识别
- 语音回复
- 更自然的语音消息模拟
- 自动记忆总结
- 情绪状态系统
- 亲密度系统
- 早安 / 晚安主动消息
- 根据用户作息动态调整主动消息时间
- 更强的提醒解析
- 多用户隔离
- Web 管理后台
- 插件配置可视化
- 数据库化存储记忆与提醒
- 向量检索长期记忆

---

## 致谢

本项目基于：

- chatgpt-on-wechat
- itchat
- 阿里云百炼 / DashScope
- Qwen 系列模型
- 魔搭社区 MCP 广场 Hosted MCP 服务

---

## 免责声明

本项目仅供学习、研究和个人自用。请遵守微信平台规则、模型服务商使用规范和相关法律法规。

不要使用本项目进行骚扰、欺诈、垃圾信息发送、批量营销或任何违法用途。
