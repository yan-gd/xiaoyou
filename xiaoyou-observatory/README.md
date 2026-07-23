# 小悠 · 命轨观测台

一个独立于小悠本体运行的私有观测站。它只读取 `cow-legacy` 容器的状态与脱敏日志，并只允许启动、停止、重启这个固定容器。

> 它不是“小悠控制台”。网页没有人格、记忆内容、模型、插件、主动行为、提醒事项或环境变量的编辑入口，也不会读取或写入小悠的数据目录。关闭观测台不会影响小悠；停止小悠容器后，观测台仍然在线。

## 能看到什么

- 容器运行状态、启动时间、CPU、内存和重启次数
- 微信连接、模型调用、长期/短期记忆链路、视觉能力的健康脉冲
- 最近一次输入、输出与近期错误数量
- 已加载插件的版本星图
- 已脱敏的最近日志
- 容器重启后，从当前日志中提取的最新微信登录二维码
- 管理员登录和容器操作审计
- 无需输入的一键游客观测入口

游客只能查看公开的实时状态与展示页面。二维码、脱敏日志、操作审计和容器命仪不仅在前端隐藏，后端接口也会返回 `403`。

唯一的写操作是 `cow-legacy` 的启动、停止和重启。所有操作均需管理员密码、TOTP、有效会话、CSRF 凭证和二次确认。

## 技术结构

```text
浏览器
  └─ HTTPS / xiaoyou.yoyoyan.cn
      └─ 宝塔 Nginx
          ├─ /              React 静态页面
          └─ /api/*         127.0.0.1:8765
                              └─ FastAPI（独立 systemd 服务）
                                  └─ sudo 精确白名单
                                      └─ root 所有的 xiaoyou-ctl
                                          └─ 固定容器 cow-legacy
```

后端绝不挂载或暴露 `/var/run/docker.sock`，也没有 shell、文件浏览、任意容器名或任意参数接口。

## 目录

```text
backend/                    FastAPI 后端、SQLite、认证与日志分析
frontend/                   React + TypeScript + Vite 前端
frontend/dist/              本地构建后的静态文件（构建后出现）
deploy/xiaoyou-ctl          固定容器控制助手
deploy/*.service            systemd 单元
deploy/*.sudoers            最小 sudo 白名单
deploy/*.nginx.conf         xiaoyou.yoyoyan.cn 站点配置
deploy/check-install.sh     只读部署自检
```

# 宝塔 Linux 部署指南

以下命令默认以 `root` 执行，服务器上的小悠容器名必须为 `cow-legacy`。部署目录固定为 `/opt/xiaoyou-observatory`，网页目录固定为 `/www/wwwroot/xiaoyou-observatory`。

## 1. 部署前检查

```bash
docker inspect cow-legacy --format '{{.Name}} {{.State.Status}}'
python3.11 --version
nginx -v
```

如果没有 Python 3.11，可在宝塔「软件商店 → Python 项目管理器」安装 Python 3.11，或使用系统包管理器安装 `python3.11`、`python3.11-venv`。不要把 Windows 下生成的 `.venv` 上传到 Linux。

## 2. 上传项目

在宝塔文件管理器中建立 `/opt/xiaoyou-observatory`，上传本目录中的：

```text
backend/
deploy/
.env.example
pytest.ini
README.md
```

如果准备在服务器构建前端，再一并上传 `frontend/`，但无需上传 `frontend/node_modules/`。

创建专用系统账号、数据目录和 Python 环境：

```bash
id xiaoyou-observer >/dev/null 2>&1 || useradd --system --home-dir /opt/xiaoyou-observatory --shell /usr/sbin/nologin xiaoyou-observer
cd /opt/xiaoyou-observatory
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt
install -d -o xiaoyou-observer -g xiaoyou-observer -m 0700 /opt/xiaoyou-observatory/data
chown -R root:root /opt/xiaoyou-observatory/backend /opt/xiaoyou-observatory/deploy
```

## 3. 创建观测台机密配置

先生成独立密钥：

```bash
openssl rand -hex 32
```

复制输出，然后安装并编辑配置：

```bash
install -o root -g xiaoyou-observer -m 0640 .env.example /etc/xiaoyou-observatory.env
ln -sfn /etc/xiaoyou-observatory.env /opt/xiaoyou-observatory/.env
nano /etc/xiaoyou-observatory.env
```

把 `OBSERVATORY_APP_SECRET` 改成刚生成的 64 位十六进制字符串，其他生产配置保持如下：

```dotenv
OBSERVATORY_ENVIRONMENT=production
OBSERVATORY_DATABASE_PATH=/opt/xiaoyou-observatory/data/observatory.db
OBSERVATORY_ALLOWED_HOSTS=xiaoyou.yoyoyan.cn,127.0.0.1,localhost
OBSERVATORY_COOKIE_SECURE=true
OBSERVATORY_CONTROLLER_PATH=/usr/local/sbin/xiaoyou-ctl
OBSERVATORY_MOCK_MODE=false
OBSERVATORY_TRUSTED_PROXY=true
```

这个密钥只属于观测台，不要复用小悠的 API Key，也不要把它放进 Git。密钥丢失会导致现有 TOTP 密文无法解密；请将 `/etc/xiaoyou-observatory.env` 和数据库一起安全备份。

## 4. 安装最小权限容器助手

```bash
install -o root -g root -m 0755 deploy/xiaoyou-ctl /usr/local/sbin/xiaoyou-ctl
install -o root -g root -m 0440 deploy/xiaoyou-observatory.sudoers /etc/sudoers.d/xiaoyou-observatory
visudo -cf /etc/sudoers.d/xiaoyou-observatory
sudo -u xiaoyou-observer sudo -n /usr/local/sbin/xiaoyou-ctl status
```

最后一条应返回 JSON。再确认专用账号无法附加任意参数：

```bash
sudo -u xiaoyou-observer sudo -n /usr/local/sbin/xiaoyou-ctl restart cow-legacy
```

这条命令必须失败。不要把 `xiaoyou-observer` 加入 `docker` 组；docker 组等同于 root 权限。

## 5. 创建唯一管理员并绑定 TOTP

```bash
cd /opt/xiaoyou-observatory
sudo -u xiaoyou-observer -H .venv/bin/python -m backend.app.cli create-admin --username yoyo
```

密码至少 12 位，包含大写字母、小写字母和数字。终端会显示 TOTP 二维码、手动密钥和一次性恢复码：

- 用 2FAS、Google Authenticator、Microsoft Authenticator 等应用扫描；
- 把恢复码离线保存，页面和数据库不会再次显示明文；
- 初始化命令只允许创建一个管理员，防止远程抢注。

## 6. 安装并启动后端服务

```bash
install -o root -g root -m 0644 deploy/xiaoyou-observatory.service /etc/systemd/system/xiaoyou-observatory.service
systemctl daemon-reload
systemctl enable --now xiaoyou-observatory
systemctl status xiaoyou-observatory --no-pager
curl -fsS http://127.0.0.1:8765/api/health
```

健康接口应返回 `{"ok":true,...}`。确认它只监听本机：

```bash
ss -lntp | grep 8765
```

必须看到 `127.0.0.1:8765`，不能是 `0.0.0.0:8765`。

## 7. 部署前端静态文件

### 方法 A：使用工作区已经构建好的文件

把本机的 `frontend/dist/` **里面的全部内容**上传到：

```text
/www/wwwroot/xiaoyou-observatory/
```

最终应存在 `/www/wwwroot/xiaoyou-observatory/index.html`，不要多套一层 `dist` 目录。

前端构建会同时包含 `music.mp3`。页面底部的命轨声场会循环播放，并使用实时音频频谱驱动氛围条；由于Chrome、Safari和微信内置浏览器会阻止首次有声自动播放，第一次访问通常需要点击页面或音乐条一次，之后可直接在氛围条暂停或继续。

### 方法 B：在服务器构建

Vite 7 需要 Node.js 20.19+ 或 22.12+：

```bash
cd /opt/xiaoyou-observatory/frontend
npm ci
npm run build
install -d -o root -g root -m 0755 /www/wwwroot/xiaoyou-observatory
cp -a dist/. /www/wwwroot/xiaoyou-observatory/
find /www/wwwroot/xiaoyou-observatory -type d -exec chmod 0755 {} \;
find /www/wwwroot/xiaoyou-observatory -type f -exec chmod 0644 {} \;
```

### 视频 CDN（推荐独立媒体域名）

前端支持把四个背景视频文件放到独立 CDN，同时让登录、状态、日志、二维码和容器操作继续直连观测台，不经过静态资源缓存。PC 与手机都会按照“对应设备的原视频 → `xiaoyou3.mp4` → `xiaoyou4.mp4`”循环，并提前用空闲视频层预解码下一段后交叉淡入。

1. 建立 `media.yoyoyan.cn`，先在“仅DNS”状态下申请源站证书，再开启CDN代理；宝塔生成的证书与 `well-known` 自动续签段必须保留，可参考 `deploy/xiaoyou-media.nginx.conf` 收紧公开路径；
2. CDN 只缓存 `xiaoyou-desktop.mp4`、`xiaoyou-mobile.mp4`、`xiaoyou3.mp4` 与 `xiaoyou4.mp4`，必须支持 HTTPS、HTTP Range 和查询字符串缓存键；主站 `xiaoyou.yoyoyan.cn` 可保持仅DNS，不让管理接口经过CDN；
3. 在服务器网页根目录的 `observatory-config.js` 中填写：

```javascript
window.__XIAOYOU_OBSERVATORY__ = Object.freeze({
  mediaBaseUrl: "https://media.yoyoyan.cn",
  mediaVersion: "20260715-playlist1"
});
```

以后替换视频时只需修改 `mediaVersion`，浏览器和 CDN 就会请求新版本，不必重新构建 React。任一 CDN 视频请求失败时，页面只把该片段回退到主站同名文件，不影响播放列表中的其他片段。`observatory-config.js` 中只能放公开配置，禁止写密码、TOTP 密钥、Cookie 或 API Key。

若使用 Cloudflare，可只把 `media.yoyoyan.cn` 设为橙云：为上述四条精确 MP4 路径建立 Cache Rule，设置 `Eligible for cache`、Edge TTL 30天、Browser TTL 1天，并使用 `Full (strict)` 连接持有有效证书的源站。主站保持灰云时无需为 `/api/*` 建缓存规则。源站视频返回 `Accept-Ranges: bytes`，可避免每次缓冲都重新下载整个文件；首次请求 `cf-cache-status` 通常为 `MISS`，再次请求应为 `HIT`。

## 8. 在宝塔申请免费 HTTPS

你已经将 `xiaoyou.yoyoyan.cn` 解析到服务器，可直接使用免费的 Let's Encrypt：

1. 宝塔「网站 → 添加站点」，域名填写 `xiaoyou.yoyoyan.cn`，根目录填写 `/www/wwwroot/xiaoyou-observatory`，选择纯静态；
2. 确认云厂商安全组和宝塔防火墙已放行 TCP 80、443；
3. 进入该站点「SSL → Let's Encrypt」，选择域名并申请；
4. 申请成功后开启「强制 HTTPS」；
5. 查看证书详情，确认自动续签任务存在。

然后打开站点「配置文件」，参考 `deploy/xiaoyou-observatory.nginx.conf` 配置反向代理与安全头。宝塔通常把证书放在：

```text
/www/server/panel/vhost/cert/xiaoyou.yoyoyan.cn/fullchain.pem
/www/server/panel/vhost/cert/xiaoyou.yoyoyan.cn/privkey.pem
```

若面板生成的路径不同，以面板原配置为准。保存前执行：

```bash
nginx -t
systemctl reload nginx
```

不要开放服务器的 8765 端口。密码、TOTP、会话 Cookie 和微信二维码都不应通过明文 HTTP 传输；因此生产环境不提供“临时 HTTP 登录”方案。

## 9. 完整自检

```bash
install -o root -g root -m 0755 deploy/check-install.sh /usr/local/sbin/xiaoyou-observatory-check
/usr/local/sbin/xiaoyou-observatory-check
curl -I https://xiaoyou.yoyoyan.cn
```

浏览器访问 `https://xiaoyou.yoyoyan.cn` 后依次验证：

1. 错误密码或错误 TOTP 会被拒绝，并受到频率限制；
2. 正确登录后能看到容器和四条能力脉冲；
3. 打开「脱敏日志」看不到 API Key、Token、记忆库 ID 或微信登录 URL；
4. 重启容器后，如果微信要求登录，「重连之门」会出现二维码；登录成功后旧二维码自动失效；
5. 启动、停止、重启均有二次确认与审计记录；
6. 停止 `cow-legacy` 后，观测台网页本身仍保持在线。

## 日常维护

查看后端日志：

```bash
journalctl -u xiaoyou-observatory -n 150 --no-pager
journalctl -u xiaoyou-observatory -f
```

重启观测台本身：

```bash
systemctl restart xiaoyou-observatory
```

更新后端：

```bash
cd /opt/xiaoyou-observatory
.venv/bin/pip install -r backend/requirements.txt
systemctl restart xiaoyou-observatory
```

更新前端只需重新构建并替换 `/www/wwwroot/xiaoyou-observatory/` 内的静态文件，不需要重启小悠。

更新本次游客观测版本时，需要覆盖以下后端文件并重启观测台：

```text
backend/app/database.py
backend/app/security.py
backend/app/schemas.py
backend/app/main.py
backend/app/runtime.py
deploy/xiaoyou-observatory.service
```

本次日志修复还更新了固定容器助手。覆盖代码后必须重新安装它，否则热登录时启动阶段的插件注册记录仍可能被近期日志挤出：

```bash
install -o root -g root -m 0755 deploy/xiaoyou-ctl /usr/local/sbin/xiaoyou-ctl
```

然后将新版 `frontend/dist/` 的全部内容覆盖到 `/www/wwwroot/xiaoyou-observatory/`：

```bash
cd /opt/xiaoyou-observatory
install -o root -g root -m 0644 deploy/xiaoyou-observatory.service /etc/systemd/system/xiaoyou-observatory.service
systemctl daemon-reload
systemctl restart xiaoyou-observatory
```

建议定期备份且只允许 root 读取：

```text
/etc/xiaoyou-observatory.env
/opt/xiaoyou-observatory/data/observatory.db
```

## 常见问题

### 页面显示 502

```bash
systemctl status xiaoyou-observatory --no-pager
journalctl -u xiaoyou-observatory -n 100 --no-pager
curl -v http://127.0.0.1:8765/api/health
```

### 容器操作失败

```bash
visudo -cf /etc/sudoers.d/xiaoyou-observatory
sudo -u xiaoyou-observer sudo -n /usr/local/sbin/xiaoyou-ctl status
docker inspect cow-legacy --format '{{.State.Status}}'
```

如果日志出现 `The "no new privileges" flag is set`，说明 systemd 单元中启用了会隐式强制
`NoNewPrivileges=yes` 的沙箱选项。重新安装仓库中的最新版服务单元并重启：

```bash
install -o root -g root -m 0644 deploy/xiaoyou-observatory.service /etc/systemd/system/xiaoyou-observatory.service
systemctl daemon-reload
systemctl restart xiaoyou-observatory
systemctl show xiaoyou-observatory -p NoNewPrivileges
```

最后一条必须显示 `NoNewPrivileges=no`。不要通过把服务账号加入 `docker` 组来绕过此问题。

### 重启后没有二维码

只有微信明确要求重新登录、且当前这次启动的日志里出现有效登录地址时才会显示。若日志已经出现 `Wechat login success`，旧二维码会立即作废。先检查：

```bash
sudo /usr/local/sbin/xiaoyou-ctl logs | tail -n 100
```

不要把原始登录 URL 发到公开渠道。

### 登录成功后又回到登录页

确认使用的是 `https://`，`OBSERVATORY_COOKIE_SECURE=true`，Nginx 传递了 `X-Forwarded-Proto https`，并检查浏览器系统时间是否准确。

## 本地开发与测试

后端测试：

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/python -m pytest -q
```

前端：

```bash
cd frontend
npm ci
npm run build
```

本地联调时使用单独的开发配置，将 `OBSERVATORY_MOCK_MODE=true`、`OBSERVATORY_COOKIE_SECURE=false`、`OBSERVATORY_ENVIRONMENT=development`，不要连接生产容器。

## 与小悠本体的隔离保证

- 不挂载、不读取、不修改 `/opt/cow-legacy/data` 或小悠容器内文件；
- 不调用小悠的记忆写入接口，也不读写本地长期记忆数据库；
- 不解析对话正文作为网页数据，只识别固定 Trace 阶段和健康标志；
- SQLite 只存观测台管理员、会话和操作审计；
- 游客使用短时、带签名的只读会话，不会创建管理员记录，也没有任何控制权限；
- 日志展示前会屏蔽 API Key、Token、密码、Secret、记忆库 ID、人物设定和微信登录地址；
- 二维码仅从最新容器日志临时提取，登录成功即失效，不落盘到 SQLite。

因此，这个项目不会改变小悠的人格文件、短期记忆或长期记忆数据库。
