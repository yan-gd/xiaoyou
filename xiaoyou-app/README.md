# 小悠 App

这是小悠现有 Python 服务的 Android/iOS 客户端。App 不包含模型密钥、人格副本或独立记忆库；所有对话仍由服务器上的同一套插件和 `data/` 数据处理，App 与微信共用固定会话 `yoyo`。

## 当前体验

- 只有“小悠”一个联系人，采用完整的聊天软件界面
- 自动恢复聊天记录、连接状态、正在输入状态与消息发送状态
- 键盘、输入栏和对话列表同步平滑上移，最后一条消息不再二次跳动
- 客户端消息幂等 ID、失败重试和实际接收后的送达回执
- 相册、拍摄、表情包发送，用户图片会进入小悠现有的视觉理解链路
- 按下立即录音、上滑取消、语音转写、历史语音播放
- 聊天记录搜索、定位高亮、长按回复/复制、新消息计数与未发送草稿恢复
- 搜索支持今天、近 7/30 天、指定日期以及文字、图片、语音、表情包类型筛选
- Android 原生后台消息服务、系统真实授权状态、测试通知、通知声音/振动/正文预览，以及字体、消息密度、配色和气泡圆角 DIY
- 将仓库根目录 `assets/xiaoyou-avatar.png` 原样收录为 App 内部资源，作为统一头像并在个人页完整展示人脸
- 使用 `assets/applogo-transparent.png` 生成无外部留白的 Android 透明应用图标；iOS 使用同源无白边的
  不透明版本以符合 App Store 图标规范
- 更紧凑的顶部联系人栏和底部输入栏；表情面板可独立收起，不会强制弹出键盘
- 取消语音发送时显示短时玻璃动画提示；图片预览提供安全区工具栏、缩放查看和保存到系统相册
- App 语音输入会收到小悠的 App 专属语音回复；微信通道不受影响
- HTTPS Bearer 鉴权与设备注册
- 首次连接后自动保存服务地址、设备名和令牌
- 令牌保存在系统安全存储中，不写入聊天记录或普通偏好设置
- 可选指纹、面容或设备锁解锁；应用重新进入前台时自动上锁
- App 与微信共用固定会话 `yoyo`

## 首次连接

填写：

- 服务地址：`https://xiaoyou.yoyoyan.cn/xiaoyou-app`
- 连接令牌：服务器 `.env` 中的 `XIAOYOU_APP_TOKEN`
- 设备名称：任意便于识别的名称，例如 `yoyo-phone`

连接成功后，下次启动会自动恢复连接。可在右上角设置中开启“应用锁”、立即锁定、修改连接或清除本机登录信息。

## 开发与构建

安装 Flutter、Android SDK 和 Android Studio JDK 后，在本目录执行：

```bash
flutter pub get
flutter analyze
flutter test
```

开发运行：

```bash
flutter run
```

Android 构建：

```bash
flutter build apk --debug
flutter build apk --release
```

生成结果位于 `build/app/outputs/flutter-apk/`。当前 `release` 构建暂时使用 Android 调试证书，只适合个人安装测试；正式分发或上架前必须配置独立 release keystore，后续更新也必须持续使用同一把发布密钥。

Android 端使用 `flutter_secure_storage` 保存连接令牌，使用 `local_auth` 调用系统生物识别或设备锁。iOS 端已声明 Face ID 用途和 Keychain 权限，但仍需在 macOS/Xcode 环境完成正式签名构建。

Android 后台通知由原生 `remoteMessaging` 前台服务独立拉取 AppChannel，不再依赖可能被系统暂停的
Flutter 定时器。开启“后台消息提醒”后，系统通知栏会保留一条低打扰的“后台提醒已开启”常驻通知；离开
App 后，服务约每 4 秒检查一次新事件并生成消息通知。返回前台后由 Flutter 补齐聊天记录，后台服务暂停
拉取，避免重复通知。

Android 13 及以上首次开启通知时会通过原生权限接口请求系统授权。App 内“后台消息提醒”只控制小悠是否
提醒，不会关闭 Android 系统授权；再次开启时会先读取真实权限，已经授权就直接恢复后台服务，不再重复跳转
系统设置。设置页会分别显示 Android 实际授权状态，并提供“发送测试通知”检查通知分类、声音和振动。

通知偏好还会记录用户是否在 App 内明确关闭：系统权限已打开且用户未明确关闭时，启动、恢复前台或打开设置页
都会自动同步为开启；用户主动关闭后则不会被系统权限同步反向打开。

原生前台服务必须展示常驻通知，这是 Android 对持续后台网络任务的系统要求。强制停止 App、手机重启后尚未
重新打开 App，或厂商系统禁止后台活动时，仍无法保证即时提醒；未读内容不会丢失，仍保存在
`data/app_channel/app.db` 并在下次打开时补齐。若以后需要取消常驻通知并覆盖强制停止以外的离线场景，
应进一步接入 FCM/APNs 服务端推送。

## 服务端要求

服务器 `.env`：

```dotenv
XIAOYOU_APP_ENABLED=true
XIAOYOU_APP_TOKEN=使用 openssl rand -hex 32 生成的随机值
XIAOYOU_APP_VOICE_ENABLED=true
XIAOYOU_APP_TTS_API_KEY=火山语音控制台的API_Key
```

语音识别默认使用 `qwen3-asr-flash`，语音合成使用
火山引擎 `seed-tts-2.0` 和 `zh_female_xiaohe_uranus_bigtts`
（小荷 2.0）。ASR 继续复用服务器已有的百炼 `KEY`，TTS 使用独立的
`XIAOYOU_APP_TTS_API_KEY`；旧版火山账号也可以改填
`XIAOYOU_APP_TTS_APP_ID` 和 `XIAOYOU_APP_TTS_ACCESS_KEY`。所有密钥
均只留在服务器，不会进入 APK。语音转写作为用户原话进入同一套记忆链路，
音频文件保存在 `data/app_channel/media/`。用户发送的图片与表情包也保存在该目录，单张限制 8 MiB。
火山 V3 接口返回 MP3 与真实时长；合成失败会退回文字，不影响本轮回复。

启动时需叠加仓库根目录的 `docker-compose.app.yml`。它只把容器端口映射到宿主机 `127.0.0.1:8787`，必须使用 Nginx/Caddy 提供公网 HTTPS，App 不应直接连接明文 HTTP 端口。

接口协议和反向代理示例见 [`../docs/app-channel.md`](../docs/app-channel.md)。
