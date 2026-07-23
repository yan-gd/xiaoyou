# 小悠 App

这是小悠现有 Python 服务的 Android/iOS 客户端。它不包含模型密钥、人格副本或独立记忆库；所有对话仍由服务器上的同一套插件和 `data/` 数据处理。

当前第一版支持：

- HTTPS Bearer 鉴权
- 设备注册
- 文字消息与客户端消息幂等 ID
- 聊天历史
- 轮询实时事件
- 文字和小悠生活照显示
- App 实际接收后的不可变终态送达回执
- App 与微信共用固定会话 `yoyo`

## 开发与构建

仓库已经包含 Android/iOS 平台外壳。安装 Flutter、Android SDK 和 Android Studio JDK 后，在本目录执行：

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

如果当前网络无法稳定访问 Google Maven，可在 PowerShell 中让主工程和 Flutter 的包含式 Gradle 构建共同使用仓库内的国内镜像配置：

```powershell
.\android\gradlew.bat `
  -I .\tooling\gradle-mirrors.init.gradle `
  -p .\android app:assembleRelease
```

生成结果位于 `build/app/outputs/flutter-apk/`。当前 `release` 构建暂时使用 Android 调试证书，只适合个人安装测试；正式分发或上架前必须配置独立 release keystore，后续更新也必须持续使用同一把发布密钥。

App 首次启动会要求输入 HTTPS 服务地址、连接令牌和设备名称。令牌仅保存在当前运行内存中。后续版本应改成一次性配对码换取系统安全区中的设备凭证，不能把服务器令牌写死在 Dart 源码或 APK 中。

## 服务端要求

在服务器 `.env` 中设置：

```dotenv
XIAOYOU_APP_ENABLED=true
XIAOYOU_APP_TOKEN=使用 openssl rand -hex 32 生成的随机值
```

启动时需要同时叠加仓库根目录的 `docker-compose.app.yml`。它只把容器端口映射到宿主机 `127.0.0.1:8787`。必须使用 Nginx/Caddy 提供公网 HTTPS，App 不应直接连接明文 HTTP 端口。

接口协议和反向代理示例见 [`../docs/app-channel.md`](../docs/app-channel.md)。
