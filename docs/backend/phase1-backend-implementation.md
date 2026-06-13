# 阶段1最小后端实现说明

本次新增 `DreamJourneyBackend/`，用于支撑真实测试和后续云部署。

## 已覆盖能力

- 服务端运行配置：`GET /config/runtime` 只暴露能力状态，不泄露密钥。
- 火山实时对话配置：`POST /voice/realtime-token` 返回可下发给客户端的实时连接配置，敏感 token 只返回引用。
- 火山 TTS 代理：`POST /tts` 默认转发到火山 TTS，`dryRun=true` 可查看脱敏请求。
- 高德行政区代理：`GET /maps/district` 默认转发高德 WebService，`dryRun=true` 可查看脱敏 URL。
- KBLite 同步：`POST /kb/sync` 过滤 `localOnly`，保留可同步图谱。
- 记忆、档案、亲友最小接口：默认写入 Postgres JSONB 表，支持服务重启后继续测试。

## 未完成但已预留

- Redis 异步任务队列。
- 照片对象存储。
- DeepSeek chat / image analyze 统一代理。
- Safety Guard 后端化。
- iOS 端把现有 LocalConfig key 切到后端代理。

## 验收

核心服务使用标准库 `unittest` 验证，不依赖 FastAPI 安装：

```bash
PYTHONPATH=DreamJourneyBackend python3 -m unittest discover DreamJourneyBackend/tests
```

FastAPI 本机 smoke 可显式切到内存模式，避免本机未启动 Postgres 时阻塞：

```bash
STORE_BACKEND=memory PYTHONPATH=DreamJourneyBackend python - <<'PY'
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
assert client.get("/health").json()["status"] == "ok"
PY
```
