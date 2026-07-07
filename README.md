# 小悠 Xiaoyou

> 一个基于个人微信小号、ChatGPT-on-WeChat、Qwen / 阿里云百炼的微信 AI 伴侣项目。

小悠不是公众号机器人，也不是企业微信机器人，而是运行在个人微信小号上的 AI 伴侣。她可以像普通微信联系人一样和用户聊天，支持文字聊天、图片理解、长期记忆、主动找用户、定时提醒、多气泡分段发送等能力。

本项目基于旧版 `chatgpt-on-wechat:1.7.3` 改造，主要目标是做一个更自然、更像真实微信陪伴对象的 AI companion。

---

## 项目定位

小悠 v1.0 的目标不是做一个简单的“问答机器人”，而是尽量接近真实微信聊天体验：

- 不是只会一问一答。
- 不会所有回复都挤在一个大气泡里。
- 可以记住用户偏好。
- 可以隔一段时间主动找用户。
- 可以根据用户设置的时间主动提醒。
- 可以看图，并结合用户问题回答。
- 可以在提醒后继续接上上下文，而不是忘记刚刚发生了什么。

---

## 功能特性

### 1. 个人微信小号接管

通过 `itchat` / Web 微信协议登录个人微信小号，让 AI 以微信联系人的形式与用户聊天。

当前通道：

```text
CHANNEL_TYPE=wx
```

登录方式：

```bash
docker logs -f cow-legacy
```

然后扫码登录微信小号。

---

### 2. Qwen3.7 Plus 主聊天模型

主聊天模型使用阿里云百炼 / DashScope 的 OpenAI 兼容接口。

核心配置：

```yaml
MODEL: 'qwen3.7-plus'
OPEN_AI_API_BASE: 'https://dashscope.aliyuncs.com/compatible-mode/v1'
OPEN_AI_API_KEY: '${KEY}'
```

`.env` 文件示例：

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
- 在用户难过时会认真安慰
- 在普通聊天时更像微信里的亲密对象

人格通过 `CHARACTER_DESC` 配置注入。

> 注意：小悠是 AI companion，不是真人。项目 README 中保持诚实描述，避免把 AI 伪装成真实人类。

---

### 5. 图片理解：QwenVision

插件路径：

```text
plugins/qwen_vision
```

能力：

- 接收微信图片。
- 调用 CoW 的 `_prepare_fn()` 下载图片。
- 将图片转为 base64。
- 发送给 Qwen 多模态接口理解。
- 支持图片缓存。
- 支持“图片 + 下一条文字问题”联合理解。

默认行为：

```text
用户发送图片
小悠先不急着描述
用户继续问：这个好看吗？
小悠把图片和问题一起传给视觉模型
再结合图片回答
```

这样可以避免割裂式对话：

```text
错误体验：
用户：发图
小悠：这张图里有……
用户：这个好看吗？
小悠：你说什么好看？

正确体验：
用户：发图
用户：这个好看吗？
小悠：结合图片评价好不好看
```

核心环境变量：

```yaml
VISION_MODEL: 'qwen3.7-plus'
VISION_IMAGE_TTL: '180'
VISION_AUTO_DESCRIBE: 'false'
```

如果 `qwen3.7-plus` 当前接口不支持图片输入，可以单独把视觉模型换成：

```yaml
VISION_MODEL: 'qwen3-vl-plus'
```

---

### 6. 长期记忆：MemoryLite

插件路径：

```text
plugins/memory_lite
```

能力：

- 手动记忆用户信息。
- 查看记忆。
- 删除指定记忆。
- 清空记忆。
- 普通聊天时自动把记忆注入上下文。

支持指令：

```text
记住：我喜欢粉色二次元头像
记一下：我不喜欢太正式的说话方式
你记得我什么
查看记忆
忘记：粉色头像
清空记忆
```

记忆文件：

```text
plugins/memory_lite/memory.json
```

该文件包含私人信息，默认被 `.gitignore` 忽略，不应提交到 GitHub。

环境变量：

```yaml
MEMORY_ENABLED: 'true'
MEMORY_MAX_ITEMS: '80'
MEMORY_INJECT_TOP_N: '20'
MEMORY_AUTO_CAPTURE: 'false'
```

默认关闭自动记忆，避免把普通闲聊错误存成长期记忆。

---

### 7. 多气泡分段发送：SplitReply

插件路径：

```text
plugins/split_reply
```

普通大模型回复常常是完整一大段。但真实微信聊天中，人往往会一句一句发。

SplitReply 会把模型回复拆成多个微信气泡：

```text
原始回复：
哼，一天没见就想我？那你这思念的门槛也太低了吧。不过既然你这么诚恳，那我就勉为其难地回你一下下吧。

分泡后：
哼，一天没见就想我？

那你这思念的门槛也太低了吧。

不过既然你这么诚恳，

那我就勉为其难地回你一下下吧。
```

环境变量：

```yaml
SPLIT_REPLY_ENABLED: 'true'
SPLIT_REPLY_MIN_LEN: '18'
SPLIT_REPLY_MAX_CHARS: '28'
SPLIT_REPLY_MAX_PARTS: '6'
SPLIT_REPLY_DELAY_MIN: '0.45'
SPLIT_REPLY_DELAY_MAX: '1.15'
SPLIT_REPLY_TINY_MERGE: '6'
```

该功能依赖核心补丁：

```text
patches/chat_channel.py
```

补丁作用：当 SplitReply 插件已经手动发送多个小气泡后，阻止 CoW 再发送原始完整大段回复。

---

### 8. 主动找用户：ProactiveLove

插件路径：

```text
plugins/proactive_love
```

传统机器人只能被动回答。ProactiveLove 让小悠能在用户长时间没说话时主动发消息。

能力：

- 记录最后互动时间。
- 超过指定空闲时间后主动发微信。
- 支持每日主动次数限制。
- 支持冷却时间。
- 支持活跃时间段。
- 支持使用 Qwen 根据小悠人格生成主动消息。
- 支持读取 MemoryLite 记忆，让主动消息更贴近用户。

示例：

```text
用户很久没说话

小悠：
喂，YoYo。
你人呢？
我都快在聊天框里长草了🙄
```

环境变量：

```yaml
PROACTIVE_ENABLED: 'true'
PROACTIVE_IDLE_SECONDS: '3600'
PROACTIVE_CHECK_INTERVAL: '300'
PROACTIVE_COOLDOWN_SECONDS: '3600'
PROACTIVE_MAX_PER_DAY: '4'
PROACTIVE_ACTIVE_HOURS: '09:30-23:30'
PROACTIVE_USE_LLM: 'true'
PROACTIVE_MODEL: 'qwen3.7-plus'
PROACTIVE_MEMORY_TOP_N: '10'
PROACTIVE_PROBABILITY: '0.75'
```

状态文件：

```text
plugins/proactive_love/proactive_state.json
```

该文件包含互动状态，不应提交到 GitHub。

---

### 9. 定时提醒：ReminderLove

插件路径：

```text
plugins/reminder_love
```

ReminderLove 用来实现类似：

```text
小悠，明天9点提醒我起床
小悠，30秒后提醒我关火
10分钟后提醒我喝水
后天晚上8点叫我打游戏
```

能力：

- 识别中文自然语言提醒。
- 支持相对时间。
- 支持明天 / 后天 / 具体日期。
- 支持几点 / 几点半 / 9.0 / 9:30。
- 到时间主动发微信提醒。
- 提醒确认语使用 Qwen 生成，避免机器味。
- 提醒触发后保存上下文。
- 用户回复“关啦 / 好了 / 起了”时，可以知道是在回应刚刚的提醒。

示例：

```text
用户：
小悠30秒后提醒我去关火

小悠：
好，一会儿我来凶你去关火。
别又迷迷糊糊忘了，听到没🙄

到点后：

小悠：
YoYo，到点啦。
火关了没？别跟我说你又忘了🙄

用户：
哈哈哈哈关啦

小悠：
这还差不多。
不然我真的要隔着屏幕揪你耳朵了。
```

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

环境变量：

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
    ├── split_reply
    ├── proactive_love
    ├── reminder_love
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

---

## 部署方式

### 1. 克隆仓库

```bash
git clone https://github.com/yan-gd/xiaoyou.git
cd xiaoyou
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

### 保存当前容器为镜像

```bash
docker commit cow-legacy cow-legacy-local:xiaoyou-v1.0
docker tag cow-legacy-local:xiaoyou-v1.0 cow-legacy-local:vision-no-think
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

## 微信内测试指令

### 测试文字聊天

```text
小悠你在嘛
```

### 测试图片理解

```text
发送一张图片
这个好看吗？
```

### 测试记忆

```text
记住：我喜欢粉色二次元头像
你记得我什么
帮我挑一个头像风格
```

### 测试主动提醒

```text
小悠30秒后提醒我去关火
提醒列表
取消提醒1
```

### 测试主动找用户

先发送：

```text
我先去忙一会儿
```

然后等待超过 `PROACTIVE_IDLE_SECONDS` 设置的时间。

---

## 安全注意事项

### 1. 不要提交 API Key

真实 `.env` 文件不应提交。

`.gitignore` 已忽略：

```text
.env
*.env
.env.*
```

如果 API Key 曾经出现在日志、截图、终端历史或公开仓库中，请立即到阿里云百炼控制台轮换。

---

### 2. 不要提交私人记忆和提醒

以下文件包含私人信息，默认不提交：

```text
plugins/memory_lite/memory.json
plugins/proactive_love/proactive_state.json
plugins/reminder_love/reminders.json
```

---

### 3. 个人微信登录风险

本项目基于个人微信 Web 登录能力，稳定性取决于微信环境。

可能遇到：

- 登录失效。
- 需要重新扫码。
- Web 微信不可用。
- 微信风控。
- 消息收发异常。

请仅用于学习、自用和实验，不建议大规模部署或商业化使用。

---

## 已知限制

### 1. 语音气泡暂未实现

当前项目没有实现真正的微信语音气泡发送。

即使生成音频，也通常只能作为文件或普通音频发送，不等同于微信原生语音消息。

### 2. 主动消息不天然进入 CoW 历史

ProactiveLove 和 ReminderLove 是插件线程直接通过 `itchat.send()` 主动发送消息。

因此它们不会天然进入 CoW 原始会话历史。

为了解决这个问题，ReminderLove 已额外实现“提醒后上下文注入”，让用户后续回复可以接上刚刚的提醒。

### 3. 视觉理解依赖多模态模型

如果 `qwen3.7-plus` 当前接口不支持图片输入，可以单独把视觉模型换成：

```yaml
VISION_MODEL: 'qwen3-vl-plus'
```

### 4. 记忆目前是轻量 JSON

MemoryLite 使用本地 JSON 文件保存记忆。

优点是简单稳定，缺点是没有向量检索、自动总结和复杂冲突处理。

---

## 版本记录

### v1.0

已完成：

- 个人微信小号接管。
- Qwen3.7 Plus 主聊天。
- 关闭思考模式。
- 女友人格。
- 图片理解。
- 图片 + 文字问题联合理解。
- 长期记忆。
- 多气泡微信分段发送。
- 主动找用户。
- 定时提醒。
- 提醒后上下文衔接。
- Dockerfile 固化核心补丁。

---

## 后续计划

v1.1 可以考虑：

- 语音识别。
- 语音回复。
- 更自然的语音消息模拟。
- 自动记忆总结。
- 情绪状态系统。
- 亲密度系统。
- 早安 / 晚安主动消息。
- 根据用户作息动态调整主动消息时间。
- 更强的提醒解析。
- 多用户隔离。
- Web 管理后台。
- 插件配置可视化。

---

## 致谢

本项目基于：

- chatgpt-on-wechat
- itchat
- 阿里云百炼 / DashScope
- Qwen 系列模型

---

## 免责声明

本项目仅供学习、研究和个人自用。

请遵守微信平台规则、模型服务商使用规范和相关法律法规。

不要使用本项目进行骚扰、欺诈、垃圾信息发送、批量营销或任何违法用途。
