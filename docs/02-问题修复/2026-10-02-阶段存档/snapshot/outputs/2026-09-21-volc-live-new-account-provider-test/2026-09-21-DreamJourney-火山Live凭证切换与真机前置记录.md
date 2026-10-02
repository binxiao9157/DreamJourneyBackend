# DreamJourney 火山 Live 凭证切换与真机前置记录

日期：2026-09-21（Asia/Shanghai）

## 范围与授权

- 用户明确授权将已通过独立 Provider 冒烟的新火山 Live 凭证接入当前 DreamJourney 后端。
- 本次仅更新实时 Live 所需的六个环境项，并重建 API 容器。
- 未读取或修改生产业务数据、候选、正式记忆、审核历史、Dead Letter 或数据库。
- 未修改源码、数据库迁移、Worker、Git 工作树或远程仓库。

## 切换前证据

- iPhone 当前安装包仅包含后端基址，不内嵌火山 App ID、App Key 或 Session Token。
- Live 启动时由 iOS 调用 `/voice/realtime-token`，后端再返回运行时配置。
- 新账号 Provider 冒烟此前使用本地私密配置通过，但报告明确记录生产后端未切换。
- 本次真机切换前，点击 Live 稳定提示“语音服务暂时不稳定，请再试一次”。
- 设备日志连接尝试遇到 CoreDeviceService 初始化超时；该工具故障未作为业务根因。

## 实际变更

活动 release：

`/opt/services/dreamjourney/releases/live-long-memory-run04-20260920-2230`

仅同步以下 allowlist 键：

- `VOLCENGINE_APP_ID`
- `VOLCENGINE_APP_KEY`
- `VOLCENGINE_APP_TOKEN`
- `VOLCENGINE_REALTIME_RESOURCE_ID`
- `VOLCENGINE_REALTIME_ADDRESS`
- `VOLCENGINE_REALTIME_URI`

活动 release 与部署源目录的 `.env` 均已更新。六个键中两项发生实际变化，四项保持原值。两个目标文件权限均为 `0600`。

服务器本地回退备份：

- `/opt/services/dreamjourney/current/.env.backup-volc-20260921T041906Z`
- `/opt/services/dreamjourney/DreamJourneyBackend/.env.backup-volc-20260921T041906Z`

更新后仅执行 API 容器强制重建；PostgreSQL、Redis 和所有 Worker 未重启。

## 切换后检查

| 检查 | 结果 |
|---|---|
| 活动 `.env` 六项完整 | PASS，6/6 |
| API 容器六项完整 | PASS，6/6 |
| 容器值与活动 `.env` 一致 | PASS |
| API container health | PASS，healthy |
| API restart count | PASS，0 |
| 近十分钟启动错误模式 | PASS，0 |
| 公网 `/live` | PASS，HTTP 200 |
| 公网 `/ready` | PASS，HTTP 200 |
| 公网 `/health` | PASS，HTTP 200 |

## 当前状态

`CREDENTIAL_SWITCH_DEPLOYED / API_HEALTH_PASS / DEVICE_LIVE_RETEST_PENDING`

上述结果证明新配置已经被当前 API 加载，不等于 iPhone SDK、麦克风、ASR、TTS、持续聆听或完整记忆链已经通过。下一步只执行一场最小真机 Live 启动与连续一轮验证。

## 局部回退

若真机验证出现可归因于新凭证的异常：

1. 将两个 `.env.backup-volc-20260921T041906Z` 分别原子恢复到原路径；
2. 保持权限 `0600`；
3. 仅强制重建 `dreamjourneybackend-api-1`；
4. 重新检查容器配置一致性、健康状态和三个匿名探活；
5. 不回退数据库、Worker、源码或历史业务数据。
