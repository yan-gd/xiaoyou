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

## 服务端要求

服务器 `.env`：

```dotenv
XIAOYOU_APP_ENABLED=true
XIAOYOU_APP_TOKEN=使用 openssl rand -hex 32 生成的随机值
XIAOYOU_APP_VOICE_ENABLED=true
```

语音识别默认使用 `qwen3-asr-flash`，语音合成使用
`cosyvoice-v3-flash` 和 `longyan_v3`。它们复用服务器已有的百炼
`KEY`，密钥不会进入 APK。语音转写作为用户原话进入同一套记忆链路，
音频文件保存在 `data/app_channel/media/`。用户发送的图片与表情包也保存在该目录，单张限制 8 MiB。

启动时需叠加仓库根目录的 `docker-compose.app.yml`。它只把容器端口映射到宿主机 `127.0.0.1:8787`，必须使用 Nginx/Caddy 提供公网 HTTPS，App 不应直接连接明文 HTTP 端口。

接口协议和反向代理示例见 [`../docs/app-channel.md`](../docs/app-channel.md)。
