# 小悠服务器版 v1.1 优化说明

本版本基于 v1.0 服务器版继续优化，重点增强小悠在微信日常聊天中的真实感、记忆能力、图片理解、主动联系和联网工具调用能力。

v1.1 的核心目标不是增加花哨功能，而是让小悠更像一个会记事、会等你补话、会避开打扰时间、会自然分段回复、也能在需要时查信息的真实陪伴型女友。

## v1.1 主要变化

### 1. 移除废弃的 timetask 插件

v1.1 已移除旧的 `timetask` 插件，并清理插件配置中的残留项。

提醒相关能力统一交给现有的 `ReminderLove` 插件处理，避免多个定时/提醒插件重复触发、逻辑打架。

### 2. 长期记忆优化

长期记忆插件 `MemoryLite` 不再依赖简单关键词判断。

v1.1 改为由语言模型判断当前聊天内容是否值得写入长期记忆，例如：

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

### 3. 新增短期记忆插件

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

### 4. 分段回复延迟优化

v1.0 的分段回复几乎连续发送，容易在微信侧显得机械，也可能增加风控风险。

v1.1 将分段回复改为按字符长度延迟：

```yaml
SPLIT_REPLY_DELAY_PER_CHAR: '0.2'
```

含义：每一段发送前，根据上一段文字长度计算等待时间，每个字符约延迟 0.2 秒。

例如上一段 20 个字符，则下一段大约等待 4 秒再发送，更接近真人打字节奏。

相关配置：

```yaml
SPLIT_REPLY_ENABLED: 'true'
SPLIT_REPLY_MIN_LEN: '18'
SPLIT_REPLY_MAX_CHARS: '28'
SPLIT_REPLY_MAX_PARTS: '6'
SPLIT_REPLY_DELAY_PER_CHAR: '0.2'
SPLIT_REPLY_TINY_MERGE: '6'
```

### 5. 图片视觉分析优化

v1.1 优化了 `QwenVision` 的图片处理逻辑和提示词。

旧逻辑的问题：

- 收到图片后容易先机械描述画面
- 像图片识别工具，不像女朋友看男朋友发图
- 等待下一条消息的逻辑不稳定，多发或不发都会影响体验

新逻辑：

- 收到图片后先等待一小会儿
- 如果用户没有补充文字，就根据图片内容自然回复
- 如果用户继续发文字，就继续短暂等待
- 多条补充消息会合并理解后再回复
- 回复重点不再是“描述图片”，而是像女朋友一样给反应、判断、调侃、关心或追问

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
- 自拍、头像、穿搭、截图、文字图会分别按不同场景自然回应

### 6. 主动发消息优化

v1.1 优化了 `ProactiveLove` 主动联系逻辑。

主要变化：

- 新增免打扰时间段 `PROACTIVE_QUIET_HOURS`
- 支持 `02:30-08:00` 和 `2.30-8.00` 两种写法
- 主动消息冷却时间调整
- 限制每日主动次数
- 记录最近主动消息，减少固定开头反复出现
- 提示词要求模型避免重复“失踪人口回归”这类高频句式

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

`PROACTIVE_QUIET_HOURS` 表示“不主动打扰”的时间段，不是允许主动发消息的时间段。

### 7. 新增 MCP 工具插件

v1.1 新增 `XiaoyouMCP` 插件，让小悠可以在自然聊天中调用魔搭社区 MCP 广场的 Hosted MCP 服务。

当前接入方向：

- 必应中文搜索：联网搜索、实时信息查询
- 高德地图：天气查询、地点搜索、路线规划、地图导航
- 时间服务：当前时间、时区时间

小悠会根据用户消息判断是否需要调用工具，例如：

- “帮我查一下今天有什么新闻”
- “明天杭州天气怎么样”
- “从这里到某个地方怎么走”
- “现在纽约几点”

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

### 8. 插件加载顺序

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

### 9. Docker 与部署注意事项

项目推荐仍部署在云服务器路径：

```bash
/opt/cow-legacy
```

构建镜像：

```bash
cd /opt/cow-legacy
docker build -t cow-legacy-local:vision-no-think .
```

重启容器：

```bash
docker compose up -d --force-recreate
docker logs -f cow-legacy
```

如果插件目录出现权限问题，例如：

```text
PermissionError: [Errno 13] Permission denied: './plugins/plugins.json'
```

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

### 10. 数据文件说明

以下数据会写在插件目录中，建议保留 `./plugins:/app/plugins` 挂载，避免容器重建后丢失：

```text
plugins/memory_lite/memory.json
plugins/short_memory/short_memory.json
plugins/proactive_love/proactive_state.json
plugins/reminder_love/reminders.json
```

## v1.1 推荐测试清单

升级后建议按下面顺序测试：

1. 普通文字聊天：确认小悠能正常回复。
2. 长期记忆：告诉她一个重要偏好，稍后验证是否能记住。
3. 短期记忆：连续聊几轮，再换话题，看她是否能自然承接最近上下文。
4. 图片消息：只发图片，确认她不会机械描述画面。
5. 图片加补充文字：先发图，再连续补几句话，确认她会等待并合并理解。
6. 分段回复：发送容易触发长回复的话题，观察分段间隔是否变自然。
7. 主动消息：确认免打扰时间段内不会主动联系。
8. MCP 查询：测试搜索、天气、地图、时间问题。
9. 提醒功能：设置一个简单提醒，确认到点触发。

## v1.1 总结

v1.1 的重点是把小悠从“会回复的机器人”继续往“有记忆、有节奏、有边界感、会主动、能查信息的真实陪伴对象”推进。

核心优化包括：

- 长期记忆改为模型判断
- 新增七天短期模糊记忆
- 图片理解更像女朋友看图
- 分段回复加入按字符延迟
- 主动消息支持免打扰和反重复
- 新增 MCP 联网、天气、地图、时间工具
- 清理废弃 timetask 插件

