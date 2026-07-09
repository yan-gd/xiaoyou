# 小悠 Xiaoyou v1.2

<p align="center">
  <img src="assets/xiaoyou-avatar.jpg" alt="小悠头像" width="320" />
</p>

> 一个基于个人微信小号、chatgpt-on-wechat、Qwen / 阿里云百炼 / 阿里云记忆库的微信 AI 伴侣项目。

小悠不是公众号机器人，也不是企业微信机器人，而是运行在个人微信小号上的 AI 伴侣。她可以像普通微信联系人一样和用户聊天，支持普通文字接管、图片理解、图文合并理解、长期记忆、短期记忆、主动找用户、定时提醒、拍一拍回复、多气泡分段发送，以及联网搜索、天气、地点搜索和路线规划。

本项目基于旧版 `chatgpt-on-wechat:1.7.3` 改造，核心目标不是做一个“问答机器人”，而是尽量接近真实微信聊天体验：她能记事、能接住上下文、能等你补话、能知道当前现实时间背景、能自然分段回复，也能在需要时调用工具查信息。

---

## 项目定位

小悠 v1.2 关注的是“长期陪伴感”和“微信真实聊天感”，而不是单纯的接口问答。

她应该做到：

- 不是只会一问一答
- 不会所有回复都挤在一个大气泡里
- 普通文字聊天尽量由小悠自己的链路接管，而不是原始 CoW 助手口吻
- 可以记住用户长期偏好、关系设定、重要约定和近期状态
- 可以保留最近几天的短期上下文
- 可以看图，并结合用户后续补充一起理解
- 用户发图后补几句话，她会合并理解，而不是抢答两次
- 可以隔一段时间主动找用户
- 可以根据用户设定的时间主动提醒
- 可以在提醒之后自然接上“关啦 / 好了 / 起了”等回复
- 可以自然回应微信“拍一拍”
- 可以在需要时调用 MCP 工具查询搜索、天气、地点和路线
- 拥有全局现实时间感知，但不会每次机械报时

---

## 当前版本

```text
当前版本：v1.2
基础镜像：zhayujie/chatgpt-on-wechat:1.7.3
主模型：qwen3.7-plus
部署方式：Docker Compose
微信通道：个人微信 wx / itchat
长期记忆：阿里云百炼记忆库
MCP 工具：阿里云百炼 MCP / WebSearch / 高德地图
```

---

## v1.2 主要变化

相较于 v1.1，v1.2 重点做了这些增强和清理：

- 新增 `XiaoyouChat` 普通文字接管插件，正常聊天不再直接交给原始 CoW ChatGPT 入口
- 删除或禁用原始 CoW 污染插件：`Hello`、`tool`、`Godcmd`、`Role`、`Keyword`、`Banwords`、`BDunit`、`Dungeon`、`LinkAI`、`Finish`、`MemoryLite`
- 长期记忆从本地 `MemoryLite` 切换为阿里云百炼记忆库 `AliyunMemory`
- `AliyunMemory` 升级为带时间感知版本，检索记忆时注入 `created_at / updated_at`，让小悠能判断记忆新旧
- 支持结合阿里云控制台的用户画像字段和记忆片段规则，自定义“什么该记、什么不该记”
- 新增全局现实时间上下文 `xiaoyou_common/time_context.py`
- 普通聊天、看图、拍一拍、主动消息、提醒确认、MCP 结果润色都能获得当前日期、时间、星期、时段背景
- MCP 时间服务已移除，当前时间不再走外部工具，而是作为全局事实注入
- `XiaoyouMCP` 精简为搜索 + 高德天气 / 地点 / 路线，不再处理 `$time` 或时间工具
- `QwenVision` 修复图文合并：发图后补文字会合并理解，并阻止普通聊天抢答
- `QwenVision` 支持多条后续文字合并，视觉回复也支持自然分段
- 新增 `PatPatReply`，真实微信拍一拍会走小悠人格自然回复
- `ProactiveLove` 增加固定目标会话配置，避免主动消息找不到发送对象
- `SplitReply` 调整为更偏微信语义分段和延迟发送
- 更新 `.gitignore`，避免提交 `.env`、本地提醒、短期记忆、主动状态、禁用插件备份和密钥备份文件

---

## 当前启用插件栈

v1.2 当前核心插件栈如下：

```text
XiaoyouMCP_v0.5-no-time
AliyunMemory_v0.2-time-aware
QwenVision_v0.5-followup-silent
ReminderLove_v0.1
PatPatReply_v0.2-clean
ProactiveLove_v0.2-target
SplitReply_v0.1
XiaoyouChat_v0.2-context-clean
ShortMemory_v0.1
```

已删除或不建议再启用的原始 CoW 插件：

```text
Hello
tool
Godcmd
Role
Keyword
Banwords
BDunit
Dungeon
LinkAI
Finish
MemoryLite
timetask
```

说明：

- 删除的是原始 CoW 插件，不是 CoW 框架底座。
- `bridge/`、`bot/`、`channel/`、`common/`、`config.py`、`app.py` 等仍然是项目运行所需的基础框架。
- 小悠通过插件链路接管正常消息，避免原始 `#help`、角色切换、工具命令和客服腔污染体验。

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
ENABLE_THINKING: 'false'
```

`.env` 示例：

```env
KEY=your_bailian_api_key_here
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
- 会吃醋、会调侃、会小小反驳
- 用户难过时会认真安慰
- 普通聊天时更像微信里的亲密对象
- 不说“我是 AI 助手”“有什么可以帮您”这类客服话术

人格通过 `CHARACTER_DESC` 配置注入。

建议回复风格：

```text
- 通常 1 到 3 行
- 每行会被分段发送成微信气泡
- 能一句说完就不要硬拆
- 只有图片复杂、提醒说明或确实需要解释时，才写到 4 行左右
```

---

## 核心插件说明

### XiaoyouChat：普通文字接管插件

路径：

```text
plugins/xiaoyou_chat
```

v1.2 新增 `XiaoyouChat`，用于接管普通文字聊天。

它的作用是：

- 让正常用户消息最后由小悠自己的链路回复
- 阻止原始 CoW ChatGPT 入口抢答
- 保留短期记忆、长期记忆、提醒上下文等隐藏参考信息
- 提取真正的用户当前原话，避免把记忆注入块当成用户原话
- 注入小悠人格和全局现实时间背景
- 回复失败时沉默，不发送预设兜底话术

插件处理成功时，日志中可能看到：

```text
[XiaoyouChat] handling normal text chat current_text='...'
[ChatChannel] reply sending cancelled by plugin
```

这里的 `reply sending cancelled by plugin` 是正常现象，表示插件已经接管发送并阻止原始回复重复发送。

---

### AliyunMemory：阿里云长期记忆插件

路径：

```text
plugins/aliyun_memory
```

v1.2 使用阿里云百炼记忆库作为长期记忆系统，替代原来的本地 `MemoryLite`。

工作方式：

```text
YoYo 发消息
↓
AliyunMemory 用当前原话检索阿里云记忆库
↓
取回相关长期记忆 content + created_at + updated_at
↓
整理成隐藏上下文注入给后续模型
↓
小悠根据记忆内容和记录时间自然使用
```

写入方式：

```text
用户发一句
小悠回一句
↓
插件把这一轮对话送到 AddMemory
↓
阿里云记忆库根据规则提炼、更新或忽略记忆
```

v1.2 时间感知增强：

- 检索结果会保留 `created_at`
- 检索结果会保留 `updated_at`
- 注入时会显示类似“记录于 / 更新于 / 几分钟前 / 几天前”
- 越新的记忆通常越可信
- 旧状态不会被当成永久事实

相关配置：

```yaml
ALIYUN_MEMORY_ENABLED: 'true'
ALIYUN_MEMORY_API_KEY: '${KEY}'
ALIYUN_MEMORY_USER_ID: 'yoyo'
ALIYUN_MEMORY_LIBRARY_ID: 'your_memory_library_id_here'
ALIYUN_MEMORY_MAX_RESULTS: '5'
ALIYUN_MEMORY_THRESHOLD: '0.55'
ALIYUN_MEMORY_TIMEZONE: 'Asia/Shanghai'
```

注意：

- `ALIYUN_MEMORY_LIBRARY_ID` 不建议写进公开 README 示例里的真实值。
- 真实 `.env` 和真实 API Key 不应提交到 GitHub。
- 阿里云 AddMemory 会自行判断是否新增、更新、合并或忽略记忆。
- 对亲密、暧昧内容，建议通过控制台规则抽象成“关系偏好”和“边界”，不要保存露骨细节。

---

### 阿里云记忆规则建议

v1.2 推荐在阿里云控制台配置自定义用户画像字段和记忆片段规则。

适合用户画像的字段：

```text
用户称呼
居住地
关系设定偏好
聊天风格偏好
亲密互动偏好
边界偏好
情绪陪伴偏好
身体健康状态
作息习惯
重要承诺/约定
饮食习惯
内容偏好
人生理想/目标
技术项目偏好
记忆处理偏好
```

记忆片段规则建议：

```text
优先提取：
- YoYo 明确表达的长期偏好、习惯、边界、称呼、城市、作息、饮食、兴趣、人生目标
- YoYo 与小悠之间反复确认或明确约定的关系设定、相处方式、亲密边界、聊天风格
- YoYo 的近期身体状态、情绪状态、压力状态、睡眠状态、用药情况、重要计划和待提醒事项
- YoYo 明确要求小悠以后记住、提醒、遵守或避免的事情
- 会影响未来回复方式的信息

不要提取：
- 普通寒暄、一次性玩笑、临时撒娇、无长期价值的闲聊
- 工具查询结果，除非 YoYo 明确表示这是长期偏好或重要计划
- 小悠自己猜测、调侃或编出来但 YoYo 没确认的内容
- 露骨成人细节、低俗表达、过于私密的具体描写
- API Key、密码、账号、token、服务器地址、敏感凭据
- 明显错误、被 YoYo 否认或纠正的信息

亲密内容处理：
如果出现亲密、暧昧或带有成人暗示的内容，不保存露骨原文，只提取背后的长期关系偏好、互动边界和表达风格。
```

---

### ShortMemory：短期记忆插件

路径：

```text
plugins/short_memory
```

`ShortMemory` 用于保留最近聊天中的短期上下文。

短期记忆定位为“近期聊天感”：

- 最近 24 小时保留较清晰的对话片段
- 超过 24 小时后逐步压缩成摘要
- 摘要最长保留 7 天
- 时间越久，注入给模型的内容越概括
- 不替代阿里云长期记忆

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

### QwenVision：图片理解与图文合并插件

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
- 支持多条后续文字合并理解
- 支持视觉回复按语义分段
- 支持全局现实时间背景

v1.2 重点修复：

- 用户发图片后再补文字，小悠会等补充内容
- 多条补充文字会合并进同一次视觉理解
- 被视觉插件捕获的后续文字会阻止普通聊天抢答
- 不再出现“普通文字先回一次，视觉再回一次”的双回复问题

相关配置：

```yaml
VISION_MODEL: 'qwen3.7-plus'
VISION_IMAGE_TTL: '250'
VISION_IMAGE_WAIT_SECONDS: '10.0'
VISION_TEXT_SETTLE_SECONDS: '6.0'
VISION_CHECK_INTERVAL: '0.5'
VISION_MAX_FOLLOWUP_MESSAGES: '6'
VISION_MAX_FOLLOWUP_CHARS: '500'
VISION_SPLIT_REPLY_ENABLED: 'true'
VISION_SPLIT_REPLY_MAX_CHARS: '80'
VISION_SPLIT_REPLY_MAX_PARTS: '5'
VISION_SPLIT_REPLY_DELAY_PER_CHAR: '0.4'
VISION_SPLIT_REPLY_TINY_MERGE: '4'
```

提示词方向：

- 不要以“这张图里”“画面中”“我看到”开头
- 不要为了证明看懂而铺陈画面
- 如果要提细节，只挑 1 到 2 个关键点
- 默认像微信聊天一样回应
- 自拍、头像、穿搭、截图、文字图分别按不同场景自然回应

示例：

```text
用户：发送一张图片
用户：看我在哪儿
用户：我还带了水杯
小悠：结合图片和两条补充文字一起回应，而不是拆成两次回复
```

---

### PatPatReply：拍一拍回复插件

路径：

```text
plugins/patpat_reply
```

v1.2 新增微信“拍一拍”自然回复。

能力：

- 只识别真实拍一拍事件
- 不再响应原始 CoW `Hello` 插件的默认自我介绍
- 不再提示 `#help`
- 拍一拍会交给大模型按小悠人格自然回复
- 模型失败时沉默，不发送固定兜底话术
- 支持全局现实时间背景

示例：

```text
YoYo 拍了拍小悠
小悠：干嘛呀
小悠：拍坏了你要赔的！
```

---

### SplitReply：多气泡分段发送插件

路径：

```text
plugins/split_reply
```

大模型回复常常是一整段，但真实微信聊天更像一句一句发。

`SplitReply` 会把模型回复拆成多个微信气泡，并在发送之间加入延迟。

v1.2 推荐配置偏向语义分段：

```yaml
SPLIT_REPLY_ENABLED: 'true'
SPLIT_REPLY_MIN_LEN: '1'
SPLIT_REPLY_MAX_CHARS: '80'
SPLIT_REPLY_MAX_PARTS: '5'
SPLIT_REPLY_DELAY_PER_CHAR: '0.4'
SPLIT_REPLY_TINY_MERGE: '4'
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

`ProactiveLove` 让小悠能在用户长时间没说话时主动发消息。

能力：

- 记录最后互动时间
- 超过指定空闲时间后主动发微信
- 支持每日主动次数限制
- 支持冷却时间
- 支持免打扰时间段
- 支持记录最近主动消息，减少固定开头反复出现
- 支持使用 Qwen 根据小悠人格生成主动消息
- 支持固定目标会话，避免主动消息不知道发给谁
- 支持全局现实时间背景

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
PROACTIVE_TARGET_SESSION: 'your_target_session_id_here'
PROACTIVE_REQUIRE_TARGET: 'true'
```

说明：

```text
PROACTIVE_TARGET_SESSION
```

建议配置为 YoYo 的微信会话 ID，避免主动消息误发或找不到目标。

状态文件：

```text
plugins/proactive_love/proactive_state.json
```

该文件包含互动状态，不应提交到 GitHub，但应该保留在服务器本地。

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
- 提醒确认和触发回复支持全局现实时间背景

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

该文件包含私人提醒，不应提交到 GitHub，但应该保留在服务器本地。

---

### XiaoyouMCP：联网工具插件

路径：

```text
plugins/xiaoyou_mcp
```

v1.2 的 `XiaoyouMCP` 已精简为搜索 + 高德地图能力。

当前接入方向：

- WebSearch：联网搜索、实时信息查询
- 高德地图：天气查询、地点搜索、路线规划、地图导航

已移除：

```text
时间 MCP 服务
XIAOYOU_MCP_TIME_ENDPOINT
XIAOYOU_MCP_TIME_CURRENT_TOOL
XIAOYOU_TIMEZONE
```

原因：当前现实时间已经由本地全局时间上下文提供，不再需要外部时间工具。

小悠会根据用户消息判断是否需要调用工具，例如：

```text
帮我查一下今天有什么新闻
明天杭州天气怎么样
附近有没有好吃的
从重庆到成都怎么走
```

相关配置：

```yaml
XIAOYOU_MCP_ENABLED: 'true'
XIAOYOU_MCP_MODE: 'streamable_http'

XIAOYOU_MCP_SEARCH_ENDPOINT: 'https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp'
XIAOYOU_MCP_AMAP_ENDPOINT: 'https://dashscope.aliyuncs.com/api/v1/mcps/amap-maps/mcp'

XIAOYOU_MCP_TOKEN: '${KEY}'
XIAOYOU_MCP_AUTH_HEADER: 'Authorization'
XIAOYOU_MCP_AUTH_SCHEME: 'Bearer'
XIAOYOU_MCP_TIMEOUT: '45'

XIAOYOU_MCP_AUTO_SCHEMA: 'true'
XIAOYOU_MCP_ARG_MODEL: 'qwen3.7-plus'
XIAOYOU_MCP_SEARCH_TOOL: 'bing_search'
XIAOYOU_MCP_AMAP_WEATHER_TOOL: 'maps_weather'
XIAOYOU_MCP_AMAP_ROUTE_TOOL: 'maps_direction_driving'
XIAOYOU_MCP_AMAP_SEARCH_TOOL: 'maps_text_search'

XIAOYOU_MCP_POLISH_REPLY: 'true'
XIAOYOU_MCP_POLISH_MODEL: 'qwen3.7-plus'
XIAOYOU_MCP_ROUTE_MODEL: 'qwen3.7-plus'
XIAOYOU_MCP_ROUTE_THRESHOLD: '0.72'
XIAOYOU_MCP_ROUTE_TIMEOUT: '20'
```

注意：

- MCP 路由由模型判断，不靠 `$time`、`$tool` 这类命令词
- 查询某城市天气，不代表小悠本人在该城市
- 工具查询结果不应该自动写入长期记忆，除非 YoYo 明确表示这是长期偏好或重要计划
- Hosted MCP URL 和 token 不建议公开泄露

---

### XiaoyouCommon：全局时间上下文

路径：

```text
plugins/xiaoyou_common/time_context.py
```

v1.2 新增全局时间事实源。

它会提供：

```text
当前时区
当前日期
当前时间
当前星期
当前类型：工作日 / 周末
当前时段：清晨 / 上午 / 中午 / 下午 / 傍晚 / 晚上 / 深夜 / 凌晨
```

使用规则：

- 这些只是现实时间事实
- 不作为固定回复模板
- 不要每次主动报时
- 不要固定输出“该吃饭 / 该睡觉 / 该上班”
- 只有当 YoYo 的原话涉及时间、今天、明天、刚才、晚上、早上、吃饭、睡觉、回家、计划、提醒、身体状态时，才自然参考

当前已接入：

```text
XiaoyouChat
QwenVision
PatPatReply
ProactiveLove
ReminderLove
XiaoyouMCP 结果润色
```

不建议注入的内部判断任务：

```text
MCP 路由判断
MCP 参数生成
ReminderLove 是否为提醒的 YES/NO 判断
AliyunMemory 检索 query
```

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
    ├── aliyun_memory
    ├── patpat_reply
    ├── proactive_love
    ├── qwen_vision
    ├── reminder_love
    ├── short_memory
    ├── split_reply
    ├── xiaoyou_chat
    ├── xiaoyou_common
    └── xiaoyou_mcp
```

核心目录说明：

```text
patches/
```

存放对原版 chatgpt-on-wechat 容器的核心补丁。

```text
plugins/aliyun_memory
```

阿里云长期记忆插件。

```text
plugins/qwen_vision
```

图片理解和图文合并插件。

```text
plugins/xiaoyou_chat
```

普通文字接管插件。

```text
plugins/xiaoyou_common
```

小悠公共能力，目前包含全局现实时间上下文。

```text
plugins/xiaoyou_mcp
```

搜索、天气、地点、路线等 MCP 工具调用插件。

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
KEY=your_bailian_api_key_here
```

### 3. 配置阿里云记忆库

在 `docker-compose.yml` 或 `.env` 中配置：

```yaml
ALIYUN_MEMORY_ENABLED: 'true'
ALIYUN_MEMORY_API_KEY: '${KEY}'
ALIYUN_MEMORY_USER_ID: 'yoyo'
ALIYUN_MEMORY_LIBRARY_ID: 'your_memory_library_id_here'
```

建议在阿里云控制台为该记忆库配置小悠专用用户画像字段和记忆片段规则。

### 4. 构建镜像

```bash
docker build -t cow-legacy-local:vision-no-think .
```

### 5. 启动容器

```bash
docker compose up -d --force-recreate
```

### 6. 查看日志并扫码登录

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

### 过滤日志中的 Key

```bash
docker logs --tail=200 cow-legacy | sed -E 's/sk-[A-Za-z0-9_-]+/sk-***MASKED***/g'
```

### 重启

只改插件 `.py` 时通常可以直接重启容器：

```bash
docker restart cow-legacy
```

### 修改 docker-compose.yml / .env 后重建容器

```bash
cd /opt/cow-legacy
docker compose down
docker compose up -d
docker logs -f cow-legacy
```

### 重新构建镜像

当修改了 `patches/` 或 `Dockerfile` 时，需要重新 build：

```bash
docker build -t cow-legacy-local:vision-no-think .
docker compose up -d --force-recreate
```

### 保存当前容器为镜像

```bash
docker commit cow-legacy cow-legacy-local:xiaoyou-v1.2
docker tag cow-legacy-local:xiaoyou-v1.2 cow-legacy-local:vision-no-think
```

### 检查当前启用插件

```bash
docker logs --tail=160 cow-legacy | grep "Plugin "
```

### 检查 no-thinking 补丁

```bash
docker exec -it cow-legacy grep -n "ENABLE_THINKING\|enable_thinking\|ChatCompletion.create" -B 5 -A 8 /app/bot/chatgpt/chat_gpt_bot.py
```

### 检查分泡拦截补丁

```bash
docker exec cow-legacy grep -n "reply sending cancelled by plugin\|original reply cancelled by SplitReply" -B 5 -A 8 /app/channel/chat_channel.py
```

### 检查主动消息目标配置

```bash
docker exec cow-legacy sh -lc 'echo "PROACTIVE_TARGET_SESSION=$PROACTIVE_TARGET_SESSION"; echo "PROACTIVE_REQUIRE_TARGET=$PROACTIVE_REQUIRE_TARGET"'
```

### 检查全局时间上下文接入

```bash
grep -R "build_time_context\|_xiaoyou_time_context" -n \
plugins/xiaoyou_mcp \
plugins/qwen_vision \
plugins/patpat_reply \
plugins/proactive_love \
plugins/reminder_love \
plugins/xiaoyou_chat
```

---

## 本地开发与上传服务器

推荐流程：

```text
本地修改代码
→ git commit
→ git push 到 GitHub
→ 服务器 git pull
→ 按修改内容决定是否重新 build 或重启
```

判断表：

```text
改 plugins/                → docker restart cow-legacy 通常即可
改 docker-compose.yml      → docker compose down && docker compose up -d
改 patches/                → docker build ... 然后 docker compose up -d --force-recreate
改 Dockerfile              → docker build ... 然后 docker compose up -d --force-recreate
改 .env                    → docker compose down && docker compose up -d
```

Git 提交前建议检查：

```bash
git status --short
```

不应提交：

```text
.env
.env.bak*
docker-compose.yml.bak*
disabled_plugins/
plugins/proactive_love/proactive_state.json
plugins/reminder_love/reminders.json
plugins/short_memory/short_memory.json
```

扫描真实 Key：

```bash
grep -R \
  --exclude-dir=.git \
  --exclude-dir=data \
  --exclude-dir=tmp \
  --exclude-dir=__pycache__ \
  --exclude-dir=disabled_plugins \
  --exclude=".env" \
  --exclude="*.pyc" \
  --exclude="*.bak" \
  --exclude="*.bak.*" \
  -nE "sk-[A-Za-z0-9_-]+|AKIA[0-9A-Z]{16}" .
```

没有输出再 push。

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
plugins/short_memory/short_memory.json
plugins/proactive_love/proactive_state.json
plugins/reminder_love/reminders.json
```

这些文件包含私人短期上下文、主动消息状态和提醒任务，不应提交到 GitHub，但应该保留在服务器本地。

长期记忆当前由阿里云百炼记忆库保存，不再使用本地 `plugins/memory_lite/memory.json`。

`.gitignore` 应至少包含：

```gitignore
.env
.env.*
!.env.example
*.key
*.pem
*.bak
*.bak.*
*.backup
*.before_session_fix.*
docker-compose.yml.bak*
disabled_plugins/
plugins/proactive_love/proactive_state.json
plugins/reminder_love/reminders.json
plugins/short_memory/short_memory.json
data/
tmp/
logs/
log/
*.log
__pycache__/
*.pyc
.pytest_cache/
.DS_Store
.vscode/
.idea/
*.pid
```

---

## 微信内测试清单

升级后建议按下面顺序测试。

### 1. 普通文字聊天

```text
小悠你在嘛
```

确认小悠能正常回复，并且日志中由 `XiaoyouChat` 接管。

### 2. 长期记忆

```text
小悠你记住，我喜欢你微信聊天短一点，像真人一点
过一会儿再问：你知道我喜欢你怎么聊天吗
```

确认她能在后续聊天中自然使用阿里云长期记忆。

### 3. 长期记忆时间感

```text
我最近有点感冒，咳嗽
几天后再问：我身体最近咋样来着
```

确认她不会把很旧的临时状态当永久事实。

### 4. 短期记忆

连续聊几轮，再换话题，看她是否能自然承接最近上下文。

### 5. 图片消息

```text
发送一张图片
```

确认她不会机械描述画面，而是自然回应。

### 6. 图片加补充文字

```text
发送一张图片
这个好看吗？
还有这个适合当头像吗？
```

确认她会等待并合并理解多条补充消息，不会普通聊天和视觉各回一次。

### 7. 拍一拍

在微信里拍一拍小悠。

确认不会出现原始 CoW 自我介绍和 `#help`，而是小悠自然回复。

### 8. 分段回复

发送容易触发稍长回复的话题，观察分段间隔是否自然。

### 9. 主动消息

发送：

```text
我先去忙一会儿
```

等待超过 `PROACTIVE_IDLE_SECONDS` 设置的时间，并确认免打扰时间段内不会主动联系。

### 10. MCP 查询

```text
帮我查一下今天有什么新闻
明天杭州天气怎么样
重庆附近有什么好吃的
从重庆到成都怎么走
```

确认搜索、天气、地点和路线工具是否正常。

### 11. 时间感知

```text
我现在有点困
今晚回家提醒我一下
明天早上叫我起床
```

确认小悠能结合当前时间自然理解，但不会机械报时。

### 12. 提醒功能

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
plugins/short_memory/short_memory.json
plugins/proactive_love/proactive_state.json
plugins/reminder_love/reminders.json
```

### 3. 不要提交本地禁用插件备份

```text
disabled_plugins/
```

这个目录可能包含旧插件、旧记忆、本地实验文件，只应保留在服务器本地。

### 4. Hosted MCP URL 和 token 不要公开泄露

Hosted MCP URL 可能属于专属连接地址，不建议公开到 README、issue、日志或截图中。

如果 MCP 服务商额外要求 token，也不要提交相关 token。

### 5. 个人微信登录风险

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

### 4. 阿里云记忆库会自动提炼而非原文保存

`AddMemory` 不是简单把对话原样存入数据库，而是按记忆规则提炼、更新、合并或忽略。

这适合长期记忆，但也意味着：

- 一次性玩笑可能不会被记住
- 露骨或高风险内容可能被忽略或抽象
- 重要关系偏好最好通过控制台规则抽象成干净描述
- 需要永久精确保存的内容不应只依赖自动提炼

### 5. MCP 工具能力取决于 Hosted MCP 服务

MCP 工具是否可用取决于对应 Hosted MCP endpoint、鉴权配置、网络环境和服务稳定性。

### 6. 小悠仍然依赖个人微信协议稳定性

微信 Web 登录和消息收发能力不是官方稳定机器人接口，可能受风控、版本和账号状态影响。

---

## 版本记录

### v1.2

已完成：

- 新增 `XiaoyouChat` 普通文字接管
- 删除原始 CoW 插件污染源
- 长期记忆切换到阿里云记忆库 `AliyunMemory`
- 长期记忆注入记录时间和更新时间
- 增加阿里云用户画像 / 记忆片段规则建议
- 新增全局现实时间上下文
- MCP 移除时间服务，精简为搜索 + 高德天气 / 地点 / 路线
- 修复图文合并时普通聊天抢答问题
- 支持多条图片后续文字合并理解
- 新增微信拍一拍自然回复
- 主动消息增加固定目标会话配置
- 更新安全提交和 `.gitignore` 规则
- GitHub 仓库同步到 `main`

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

后续可以考虑：

- 语音识别
- 语音回复
- 更自然的语音消息模拟
- 主动消息基于作息动态调整
- 更强的提醒解析
- 多用户隔离
- Web 管理后台
- 插件配置可视化
- 本地私有关系记忆补充层
- 数据库化存储短期记忆与提醒
- 长期记忆质量审计和清理工具

---

## 致谢

本项目基于：

- chatgpt-on-wechat
- itchat
- 阿里云百炼 / DashScope
- 阿里云百炼记忆库
- Qwen 系列模型
- 阿里云百炼 MCP / WebSearch / 高德地图服务

---

## 免责声明

本项目仅供学习、研究和个人自用。请遵守微信平台规则、模型服务商使用规范和相关法律法规。

不要使用本项目进行骚扰、欺诈、垃圾信息发送、批量营销或任何违法用途。
