# DreamJourney Live 入口与令牌 503 修复交付报告

日期：2026-09-10

## 1. 结论

| 问题 | 当前结论 | 证据 |
| --- | --- | --- |
| 文字会话结束后 Live 按钮显示勾且不可点击 | 代码与模拟器回归已解决，待真机确认 | 冷启动、进程重启各完成 10 轮，结束后均为 `idle` 且麦克风可用 |
| `/voice/realtime-token` 返回 503 | 已取得真实业务错误码、修复根因并部署，待真机通过真实鉴权请求确认 200 | 实际错误码为 `formalMemorySnapshotUnavailable`；Projection 重建后快照可生成 20 条事实 |
| 整理失败 / Dead Letter | 未混入本次修复，继续单独跟踪 | 未重放历史任务，未清理数据 |

## 2. 已确认根因

### 2.1 Live 入口不可点击

文字回答完成后，交互状态停留在 `.replied`。会话整理协调器虽然开始后台整理，但界面没有结束本轮 interaction，按钮因此继续呈现“已完成”勾选态。

修复后，结束文字会话按以下顺序执行：

1. 将完整文字会话交给原有整理协调器并保留后台任务。
2. 清理当前会话临时上下文。
3. 使旧 interaction lifecycle token 失效，阻止迟到回调复活旧状态。
4. 将 Echo ViewModel 恢复到 `.idle`，立即恢复 Live 入口。
5. 整理状态仍单独显示，不再占用麦克风交互状态。

### 2.2 令牌接口 503

先从生产失败请求和只读快照构建中取得实际业务错误码：

`formalMemorySnapshotUnavailable`

生产目标的 Projection 状态为 `rebuilding`，缺少 checkpoint，且 memory revision 不存在。文字查询仍可使用另一条已可用检索路径，所以出现“文字能回答、Live 令牌失败”的表面差异。火山凭据和公网连通性不是本次 503 根因。

修复使用现有维护命令，仅从当前已授权正式记忆重建派生 memory/search Projection：

- 命中目标：1
- 正式记忆条目：20
- 搜索文档：20
- 失败：0
- 重建后剩余 eligible：0
- 只读 Live 快照：`ready`
- 快照事实数：20
- 快照字符数：12,354

该操作没有修改 Source、Candidate、Memory 或 MemoryVersion，也没有重放历史整理任务。

## 3. 代码变化

### iOS

- `DreamJourney/Sources/Modules/Echo/EchoViewController.swift`
  - 结束文字会话后显式结束 interaction 并恢复 `.idle`。
  - 使旧 Live/回复生命周期回调失效。
  - 保留后台会话整理协调器。
  - 对令牌失败只记录白名单化 `stage`、业务错误码和 HTTP 状态，不记录正文、身份、令牌或供应商凭据。
- `DreamJourneyTests/AudioOwnerLeaseModelTests.swift`
  - 增加 `.replied -> .idle -> Live listening` 回归。
  - 增加 503 业务错误分类及未知错误脱敏回归。
- `Scripts/QA/prd-stitch-ui/*echo-continuous-turn*`
  - 增加 10 轮文字结束状态循环、麦克风可用性和旧回调隔离断言。
- `Scripts/QA/prd-stitch-ui/run-installable-simulator-uiqa.sh`
  - 修复空 `XCCONFIG_ARGS` 在系统 Bash `set -u` 下的兼容问题。

### 后端

- `app/main.py`
  - 为 Live runtime config / token 失败增加安全阶段分类日志。
  - 保持既有 HTTP 合同与 fail-closed 行为，不绕过正式记忆快照。
- `tests/test_credential_response_boundary.py`
  - 验证 `formalMemorySnapshotUnavailable` 返回 503。
  - 验证日志只含安全阶段、业务码、状态，不含用户标识、火山 token 或正文。

没有修改火山原生 Live 音频路径、打断、持续聆听或正式记忆绑定逻辑。

## 4. 自动化与编译证据

| 验证 | 结果 |
| --- | --- |
| 后端令牌、正式记忆快照、Realtime Proxy 定向测试 | 29/29 PASS |
| Projection 维护命令测试 | 4/4 PASS |
| iOS Live 状态机和音频路由定向测试 | 14/14 PASS |
| iOS 控制器 UIQA 冷启动 | 10/10 文字结束循环 PASS |
| iOS 控制器 UIQA 进程重启 | 10/10 文字结束循环 PASS |
| iOS 通用 arm64 Debug 编译 | `BUILD SUCCEEDED` |
| 后端 Python compileall（修复镜像） | PASS |
| 生产 API `/health` | PASS |
| 生产 API `/ready` | database/schema/auth/incident 全部 ready |

全仓 `unittest discover` 额外执行 2,552 项，出现 46 个既有测试隔离错误：前序测试将全局 Store 切换成 PostgreSQL 后未启动连接池，后续测试报 `PoolClosed`。本次相关测试在独立进程中全部通过；该全仓隔离问题未通过修改预期掩盖，也不归为本次功能已解决。

UIQA 原始结果：

- `ios-uiqa-cold-start.json`
- `ios-uiqa-process-restart.json`
- `ios-uiqa-report.md`

## 5. 生产部署锚点

- 目标：`dreamjourney-cloud`
- 公网入口：`https://www.mmdd10.tech/dreamjourney-api`
- API 镜像：`sha256:4964872c16889bd6b89b90ef80cc691c7259a39f3494e1260cafeeb865c1462b`
- 镜像 revision：`be9670b6ec05-livefix-20260910`
- 源码包 SHA-256：`4c64083be27d7d144245170a320748d7dc5a24c159df2d22f0bfefb7ab019285`
- API：running / healthy / restart 0
- 数据库 schema：`0119`
- 六个既有 Worker：继续运行，未重建、未重启
- 回滚镜像标签：`dreamjourneybackend-api:rollback-livefix-20260910`

部署前完成数据库备份，备份 service 结果为 success。服务器 Git 工作区保持干净；本次使用已授权的无密钥源码包构建，未推送 GitHub。

## 6. 真机待确认

连接 iPhone 后执行两组最小验证：

1. 输入一条文字问题，结束文字会话，确认按钮立即恢复为麦克风并可进入 Live。
2. 启动 Live，确认 `/voice/realtime-token` 返回成功、正式记忆已绑定、可持续聆听和打断。

真机期间同时观察 iOS 安全诊断与后端 `liveSnapshotIssued` / `realtimeVoiceRuntimeConfigRejected`，不得仅凭界面主观判断接口是否成功。

## 7. 剩余事项

- 整理失败 / Dead Letter 继续作为独立问题处理；本轮没有重放或清理。
- 全仓测试的全局 PostgreSQL Store 污染需要独立修复测试隔离。
- 未执行 Git commit 或 GitHub push。
