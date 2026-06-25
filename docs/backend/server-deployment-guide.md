# DreamJourney 后端服务器部署流程

本文档基于当前云服务器交接信息整理，用于把 `DreamJourneyBackend` 部署到已有服务器上，并尽量不影响当前已经运行的 `miao` 服务。

参考信息来源：

- `/Users/yxj/Documents/Codex/AiStudio/Miao/docs/SERVER_DOCKER_DEPLOYMENT_INFO.md`
- `DreamJourneyBackend/docker-compose.yml`
- `DreamJourneyBackend/.env.example`

服务器已经完成初次部署后的日常更新，优先参考：

- `docs/backend/server-update-operations.md`

## 1. 当前服务器现状

服务器：

| 项目 | 值 |
| --- | --- |
| 公网 IP | `124.221.2.31` |
| 内网 IP | `10.0.0.11` |
| 主机名 | `VM-0-11-ubuntu` |
| 系统 | `Ubuntu 24.04.4 LTS` |
| CPU | `4 核 AMD EPYC 7K62` |
| 内存 | `3.6 GiB`，另有 `1.9 GiB` Swap |
| 磁盘 | `40G`，根目录约 `28G` 可用 |

登录方式：

```bash
ssh ubuntu@124.221.2.31
```

本地如果已配置 alias，也可以：

```bash
ssh miao-server
```

当前已占用端口：

| 端口 | 服务 | 说明 |
| --- | --- | --- |
| `22` | SSH | 登录服务器 |
| `80` | Nginx | HTTP |
| `443` | Nginx | HTTPS |
| `3000` | Node / miao | 当前 miao 应用 |
| `53` | systemd-resolved | 本机 DNS |

当前服务器尚未安装 Docker：

```text
docker: command not found
```

约束：

- 不要改 `/home/miao/app`。
- 不要占用 `80`、`443`、`3000`。
- DreamJourney 后端容器只绑定本机端口 `127.0.0.1:3100`。
- 外部访问通过现有 Nginx HTTPS 反向代理。
- Postgres 和 Redis 不暴露公网端口，只允许 Docker 内部访问。

## 2. 部署形态

Docker Compose 会启动三个服务：

| 服务 | 作用 | 对外端口 |
| --- | --- | --- |
| `api` | DreamJourney FastAPI 后端 | 宿主机 `127.0.0.1:3100` |
| `postgres` | 持久化用户、知识库、记忆、档案、亲友数据 | 不暴露 |
| `redis` | 预留异步任务和长任务状态 | 不暴露 |

推荐最终访问链路：

```text
iOS 真机
  -> https://待确认的 DreamJourney 后端域名
  -> Nginx
  -> http://127.0.0.1:3100
  -> DreamJourneyBackend/api 容器
  -> postgres / redis 容器内网
```

配置名注意：

- `OpenAvatarChatBaseURL` 是当前 iOS 代码里已有的旧 OpenAvatarChat Python 后端配置，默认接口形态是 `/api/knowledge/inject`、`/api/knowledge/status`、`/api/knowledge/search`。
- 本文档部署的是新的 `DreamJourneyBackend`，当前接口形态是 `/health`、`/auth/login`、`/kb/sync`、`/kb/snapshot/{user_id}`、`/tts`、`/maps/district`。
- 因此不要直接把 `OpenAvatarChatBaseURL` 当成 DreamJourneyBackend 的标准地址，除非后端额外补齐 OpenAvatarChat 兼容接口。
- 后续 iOS 更清晰的做法是新增 `DreamJourneyBackendBaseURL`，专门指向本文档部署的后端；`OpenAvatarChatBaseURL` 只在继续使用开源 OpenAvatarChat 服务时保留。

已确认：

- DreamJourney 后端域名使用 `dreamjourney-api.liftora.cn`。
- 服务器部署代码使用 Git 拉取。
- `DreamJourneyBackendBaseURL` 是 DreamJourney 自有业务后端入口。
- `OpenAvatarChatBaseURL` 保留为旧 OpenAvatarChat 开源工程兼容配置，不作为阶段1正式后端入口。

## 3. 需要先确认的事项

部署前需要确认这些信息：

1. 后端使用哪个公网 HTTPS 地址。
   已确认使用 `dreamjourney-api.liftora.cn`，需要解析到 `124.221.2.31`。
2. DreamJourney 代码使用 Git 拉取还是本机打包上传。
   已确认使用 Git 拉取，需要确认仓库地址和服务器可访问权限。
3. 生产环境 `.env` 的真实值。
   文档只列 key 名，不写真实密钥。
4. 是否允许在服务器安装 Docker Engine 和 Docker Compose plugin。
5. 是否需要把 `ubuntu` 和 `miao` 加入 `docker` 用户组。
   只部署 DreamJourney 后端时，`ubuntu` 足够。

## 4. 安装 Docker

登录服务器后执行：

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg lsb-release
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable docker
sudo systemctl start docker
docker --version
docker compose version
```

如果 `docker compose version` 不存在：

```bash
sudo apt install -y docker-compose-plugin
docker compose version
```

把 `ubuntu` 加入 `docker` 组：

```bash
sudo usermod -aG docker ubuntu
```

重新登录 SSH 后验证：

```bash
docker ps
```

如果暂时不重新登录，也可以继续用 `sudo docker ...`。

## 5. 准备部署目录

不要使用现有 miao 目录：

```text
/home/miao/app
```

建议使用独立目录：

```bash
sudo mkdir -p /opt/services
sudo chown -R ubuntu:ubuntu /opt/services
cd /opt/services
```

### 服务器拉 Git 仓库（已确认）

```bash
git clone <DreamJourney仓库地址> dreamjourney
cd /opt/services/dreamjourney/DreamJourneyBackend
```

如果仓库是私有仓库，需要先在服务器配置 SSH key 或使用有权限的 HTTPS token。

### 备用方式：本机打包上传

在本机仓库根目录执行：

```bash
tar --exclude='Pods' \
  --exclude='DerivedData' \
  --exclude='.git' \
  --exclude='*.xcuserdata' \
  -czf dreamjourney.tar.gz .

scp dreamjourney.tar.gz ubuntu@124.221.2.31:/tmp/
```

服务器解压：

```bash
mkdir -p /opt/services/dreamjourney
tar -xzf /tmp/dreamjourney.tar.gz -C /opt/services/dreamjourney
cd /opt/services/dreamjourney/DreamJourneyBackend
```

## 6. 配置环境变量

```bash
cp .env.example .env
nano .env
```

推荐配置：

```bash
APP_ENV=production
PUBLIC_BASE_URL=https://dreamjourney-api.liftora.cn
STORE_BACKEND=postgres

DATABASE_URL=postgresql://dreamjourney:dreamjourney@postgres:5432/dreamjourney
REDIS_URL=redis://redis:6379/0

DEEPSEEK_API_KEY=你的DeepSeekKey
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1/chat/completions

VOLCENGINE_API_KEY=你的火山APIKey
VOLCENGINE_VOICE_TYPE=zh_female_cancan_mars_bigtts
VOLCENGINE_APP_ID=你的实时对话AppID
VOLCENGINE_APP_KEY=PlgvMymc7f3tQnJ6
VOLCENGINE_APP_TOKEN=你的实时对话AccessToken
VOLCENGINE_REALTIME_RESOURCE_ID=volc.speech.dialog
VOLCENGINE_REALTIME_ADDRESS=wss://openspeech.bytedance.com
VOLCENGINE_REALTIME_URI=/api/v3/realtime/dialogue

AMAP_WEB_SERVICE_KEY=你的高德WebServiceKey

TENCENT_DIGITAL_HUMAN_APP_KEY=你的腾讯数智人appkey
TENCENT_DIGITAL_HUMAN_ACCESS_TOKEN=你的腾讯数智人accesstoken
TENCENT_DIGITAL_HUMAN_ASSET_VIRTUALMAN_KEY=你的asset_virtualman_key
# 或者使用项目建流，二选一即可：
# TENCENT_DIGITAL_HUMAN_VIRTUALMAN_PROJECT_ID=你的virtualman_project_id

# 只有启用腾讯 ASR 时才需要：
# TENCENT_DIGITAL_HUMAN_APP_ID=你的腾讯账号AppId
# TENCENT_DIGITAL_HUMAN_SECRET_ID=你的腾讯云SecretId
# TENCENT_DIGITAL_HUMAN_SECRET_KEY=你的腾讯云SecretKey
```

注意：

- `.env` 只放服务器，不提交 Git。
- `AMAP_WEB_SERVICE_KEY` 是高德 Web 服务 Key，不是 iOS SDK Key。
- `VOLCENGINE_APP_KEY` 对端到端实时对话 WebSocket 是固定值时，填 `PlgvMymc7f3tQnJ6`。
- `PUBLIC_BASE_URL` 在域名确认前可以先填 `http://127.0.0.1:3100`，但真机联调应改为 HTTPS 域名。
- 腾讯数智人云渲染只有在 `TENCENT_DIGITAL_HUMAN_APP_KEY`、`TENCENT_DIGITAL_HUMAN_ACCESS_TOKEN`，以及 `TENCENT_DIGITAL_HUMAN_ASSET_VIRTUALMAN_KEY` 或 `TENCENT_DIGITAL_HUMAN_VIRTUALMAN_PROJECT_ID` 齐全时，`/config/runtime` 才会返回 `digitalHuman.providerMode=cloudRender`。
- `TENCENT_DIGITAL_HUMAN_APP_ID`、`TENCENT_DIGITAL_HUMAN_SECRET_ID`、`TENCENT_DIGITAL_HUMAN_SECRET_KEY` 不是云渲染建流必填项，只在后续接腾讯 ASR 时启用。

## 7. 启动 DreamJourney 后端

当前 `docker-compose.yml` 已按服务器现状设计：

```yaml
ports:
  - "127.0.0.1:3100:8080"
```

这表示 API 容器内部监听 `8080`，服务器本机用 `3100` 访问，公网不能直接访问 `3100`。

启动：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
docker compose up -d --build
```

查看容器：

```bash
docker compose ps
```

查看 API 日志：

```bash
docker compose logs -f api
```

第一次启动时，API 会自动初始化 Postgres 表：

- `users`
- `kb_snapshots`
- `memories`
- `archive_items`
- `family_members`

## 8. 本机 smoke test

这些命令在服务器上执行。

健康检查：

```bash
curl http://127.0.0.1:3100/health
```

预期：

```json
{"status":"ok","service":"DreamJourney Backend","environment":"production","store":"postgres"}
```

登录：

```bash
curl -X POST http://127.0.0.1:3100/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13800000000","nickname":"测试用户"}'
```

运行配置：

```bash
curl http://127.0.0.1:3100/config/runtime
```

数智人云渲染配置检查：

```bash
curl http://127.0.0.1:3100/config/runtime | python3 -m json.tool
```

配置齐全时，`digitalHuman` 应包含：

```json
{
  "provider": "tencent",
  "providerMode": "cloudRender",
  "realProviderReady": true,
  "sdkAuthMode": "appkeyAccessToken",
  "assetMode": "asset"
}
```

创建数智人 session：

```bash
curl -X POST http://127.0.0.1:3100/digital-human/sessions \
  -H 'Content-Type: application/json' \
  -d '{
    "userId": "user_9157",
    "personaId": "persona_mother_001",
    "scene": "echo",
    "deviceId": "ios-qa",
    "lifecycleMode": "sunlight"
  }'
```

配置齐全时，响应应包含 `providerMode=cloudRender`、`credential.appkey`、`credential.accesstoken`，以及 `providerAssetId` 或 `providerProjectId`。不要把该响应里的凭证写入日志或提交到仓库。

KBLite 同步：

```bash
curl -X POST http://127.0.0.1:3100/kb/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "userId": "user_9157",
    "graph": {
      "people": [
        {"id":"p1","name":"测试用户","privacyMetadata":{"scope":"generationAllowed"}},
        {"id":"p2","name":"本机私密人物","privacyMetadata":{"scope":"localOnly"}}
      ],
      "places": [],
      "events": [],
      "facts": []
    }
  }'
```

读取快照：

```bash
curl http://127.0.0.1:3100/kb/snapshot/user_9157
```

预期只看到 `测试用户`，不会看到 `localOnly` 的私密人物。

高德代理 dry run：

```bash
curl 'http://127.0.0.1:3100/maps/district?keyword=绍兴市&dryRun=true'
```

TTS dry run：

```bash
curl -X POST 'http://127.0.0.1:3100/tts?dryRun=true' \
  -H 'Content-Type: application/json' \
  -d '{"userId":"user_9157","text":"你好，我想听一段家族回忆。"}'
```

## 9. 配置 Nginx HTTPS 反代

当前服务器已经安装并使用 Nginx：

```text
/etc/nginx/sites-enabled/miao
/etc/nginx/sites-enabled/miao-liftora
```

不要改坏现有 miao 配置。建议新增一个独立站点文件。

### 方案 A：推荐，使用独立子域名

正式域名：

```text
dreamjourney-api.liftora.cn
```

先把域名 A 记录解析到：

```text
124.221.2.31
```

新增 Nginx 配置：

```bash
sudo nano /etc/nginx/sites-available/dreamjourney-api
```

内容：

```nginx
server {
    listen 80;
    server_name dreamjourney-api.liftora.cn;

    location / {
        proxy_pass http://127.0.0.1:3100;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

启用：

```bash
sudo ln -s /etc/nginx/sites-available/dreamjourney-api /etc/nginx/sites-enabled/dreamjourney-api
sudo nginx -t
sudo systemctl reload nginx
```

申请 HTTPS 证书：

```bash
sudo certbot --nginx -d dreamjourney-api.liftora.cn
```

验证：

```bash
curl https://dreamjourney-api.liftora.cn/health
```

iOS 配置建议：

```text
DreamJourneyBackendBaseURL = https://dreamjourney-api.liftora.cn
```

不要把这个地址直接填到 `OpenAvatarChatBaseURL`，除非已经在 DreamJourneyBackend 中实现了 OpenAvatarChat 兼容接口。

### 方案 B：临时使用现有域名路径

如果暂时没有新子域名，可以挂到现有域名路径，例如：

```text
https://www.mmdd10.tech/dreamjourney-api/
```

在现有 `www.mmdd10.tech` 的 Nginx server 块里增加：

```nginx
location /dreamjourney-api/ {
    proxy_pass http://127.0.0.1:3100/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

验证：

```bash
sudo nginx -t
sudo systemctl reload nginx
curl https://www.mmdd10.tech/dreamjourney-api/health
```

iOS 配置建议：

```text
DreamJourneyBackendBaseURL = https://www.mmdd10.tech/dreamjourney-api
```

路径反代可以用于临时测试，但长期更推荐子域名，迁移和排障更清晰。

## 10. 安全组与防火墙

当前服务器 UFW 是 inactive，但腾讯云安全组仍可能限制公网访问。

建议公网只开放：

- `22`：SSH
- `80`：HTTP，用于证书签发和跳转
- `443`：HTTPS

不建议公网开放：

- `3100`
- `5432`
- `6379`
- `3000`

确认端口：

```bash
sudo ss -tulpn
```

预期 DreamJourney API 只出现：

```text
127.0.0.1:3100
```

## 11. 常用运维命令

进入服务目录：

```bash
cd /opt/services/dreamjourney/DreamJourneyBackend
```

查看容器：

```bash
docker compose ps
```

查看 API 日志：

```bash
docker compose logs -f api
```

查看数据库日志：

```bash
docker compose logs -f postgres
```

重启 API：

```bash
docker compose restart api
```

更新代码并重建：

```bash
git pull
docker compose up -d --build
```

停止服务，保留数据：

```bash
docker compose down
```

清空数据库卷，谨慎使用：

```bash
docker compose down -v
```

检查 Nginx：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

查看现有 miao 服务：

```bash
sudo -iu miao pm2 status
sudo -iu miao pm2 logs miao --lines 100
```

## 12. 排障

Docker 未安装：

```bash
docker --version
docker compose version
```

API 起不来：

```bash
docker compose logs api
```

常见原因：

- `.env` 缺失或格式错误。
- `DATABASE_URL` 写错。
- Postgres 容器还未 ready。
- 内存不足导致容器退出。

数据库连接失败：

```bash
docker compose ps
docker compose logs postgres
```

本机 API 通，公网不通：

- `curl http://127.0.0.1:3100/health` 是否成功。
- `sudo nginx -t` 是否成功。
- Nginx `server_name` 是否和域名一致。
- 域名 A 记录是否指向 `124.221.2.31`。
- 腾讯云安全组是否开放 `80/443`。

证书失败：

- 域名是否已经解析生效。
- `80` 是否被 Nginx 正常监听。
- `sudo certbot certificates` 查看证书状态。

## 13. 当前限制与后续演进

当前后端已经具备：

- Postgres 持久化。
- 用户登录最小链路。
- KBLite 同步与隐私过滤。
- 记忆、档案、亲友数据基础 API。
- 高德 District 与火山 TTS 后端代理雏形。

仍需继续完善：

- iOS 端完全切换到后端 API。
- DeepSeek 对话和照片分析统一走后端代理。
- Safety Guard 后端化。
- 图片对象存储。
- Redis 队列接入长任务。
- 后端鉴权、限流、审计日志。

## 14. 已确认与待确认项

已确认：

1. 后端最终域名：`dreamjourney-api.liftora.cn`。
2. 部署方式：服务器端 Git 拉取代码。
3. iOS 正式业务后端配置：新增 `DreamJourneyBackendBaseURL=https://dreamjourney-api.liftora.cn`。
4. `OpenAvatarChatBaseURL`：仅保留为旧 OpenAvatarChat 开源工程兼容配置，不作为正式业务后端入口。
5. API 端口：宿主机只绑定 `127.0.0.1:3100`，不直接开放公网。

仍需在服务器部署前确认：

1. `dreamjourney-api.liftora.cn` 是否已解析到 `124.221.2.31`。
2. DreamJourney Git 仓库地址，以及服务器是否已有拉取权限。
3. 是否允许安装 Docker Engine 和 Docker Compose plugin。
4. `.env` 中真实密钥是否已经准备好并可填入服务器。
