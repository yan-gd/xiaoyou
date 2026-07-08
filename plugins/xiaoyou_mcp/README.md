# XiaoyouMCP

让小悠在自然聊天中调用魔搭社区 MCP 广场的 Hosted MCP 服务：

- 必应中文搜索：联网搜索、网页结果获取
- 高德地图：天气、地图导航、地点搜索
- 时间服务：当前时间、时区转换

## 必填配置

在 `/opt/cow-legacy/docker-compose.yml` 中，把魔搭右侧服务配置里的 `url` 完整复制到对应环境变量：

```yaml
XIAOYOU_MCP_SEARCH_ENDPOINT: '必应中文搜索 Hosted URL'
XIAOYOU_MCP_AMAP_ENDPOINT: '高德地图 Hosted URL'
XIAOYOU_MCP_TIME_ENDPOINT: '时间服务 Hosted URL'
```

魔搭 Hosted URL 是专属连接地址，截图里通常只显示一部分，请用右侧复制按钮复制完整地址。

如果服务地址本身已经包含鉴权信息，`XIAOYOU_MCP_TOKEN` 可以留空。
如果服务商额外要求 token，再配置：

```yaml
XIAOYOU_MCP_TOKEN: '你的 token'
XIAOYOU_MCP_AUTH_HEADER: 'Authorization'
XIAOYOU_MCP_AUTH_SCHEME: 'Bearer'
```

## 工具名

插件会先读取 MCP 的 `tools/list`，再自动匹配工具名。默认值如下，必要时可按工具测试页里的真实名称修改：

```yaml
XIAOYOU_MCP_SEARCH_TOOL: 'bing_search'
XIAOYOU_MCP_AMAP_WEATHER_TOOL: 'maps_weather'
XIAOYOU_MCP_AMAP_ROUTE_TOOL: 'maps_direction_driving'
XIAOYOU_MCP_AMAP_SEARCH_TOOL: 'maps_text_search'
XIAOYOU_MCP_TIME_CURRENT_TOOL: 'get_current_time'
```

## 可选配置

```yaml
XIAOYOU_DEFAULT_LOCATION: '你的默认城市'
XIAOYOU_MAP_DEFAULT_ORIGIN: '你的默认出发地'
XIAOYOU_TIMEZONE: 'Asia/Shanghai'
```
