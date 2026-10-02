# DJ-API-READ-STALL-01：登录后读取停摆与超时误分类

登记日期：2026-09-24。用户要求：先记录，后续合适的维护窗口处理。本文件是问题台账与取证要求，不是已经证明根因的修复设计。

## 当前状态

**OPEN / SERVICE_TEMPORARILY_RECOVERED / ROOT_CAUSE_UNRESOLVED / FIX_NOT_IMPLEMENTED**。

现场 API 同版本受控重启后恢复健康，用户确认正式记忆页面可读。长期稳定性、候选为零是否符合当前账号预期、记录页与内容完整性均未据此关闭。不得把此次事件与历史 Live seq5 或候选整理合同失败合并归因。

现场来源：[独立事件报告](../../outputs/2026-09-24-dreamjourney-postlogin-read-incident/run-01/incident-report.md)。后端发布版本 `8141ff271228b78c35237f9947f4bf026332af43`，schema `0124`。本次登记与代码核对没有再次访问生产、重启服务或修改业务源码。

## 分开处理的事项

| 子项 | 状态 | 事实与边界 |
|---|---|---|
| A：API 进程持续无响应 | 未定位首因，未修复 | 现场报告记录公网、服务器本机、容器内 `/live` 和 `/ready` 均超时；running/unhealthy，重启后恢复。没有故障期 Python 线程栈，不能指定死锁、SQL、线程池或模型为唯一根因。 |
| B：健康检查与同步数据库诊断耦合 | 代码确认，未修复 | `/live` 跳过请求 UoW，但最外层统计中间件在返回响应前调用同步数据库 sink，执行 evidence_events INSERT。异常捕获不能为等待提供期限；该路径可能扩大故障影响，但尚未证明是 A 的现场触发点。 |
| C：读取超时被误分类为权限拒绝 | 代码确认，未修复 | `readDeadlineExceeded` 被包装为 `featurePolicyDenied`，显示“发布策略拦截”；部分领域映射进一步成为 permissionUnavailable/releasePolicyDisabled。不能只替换字符串。 |
| D：失活取证与有界运维恢复 | 待补强 | 现有 Compose 的 unhealthy 状态不会单凭 `restart: unless-stopped` 自动恢复仍存活的进程。需要先留故障证据、再执行有界恢复的策略；不能靠重试业务写或无上限重启处理。 |
| E：恢复后数据展示核对 | 待只读核对 | 待确认显示0，需检查同一账号、vault、过滤条件及历史清理预期；正式记忆可打开不等于内容完整已验收。没有证据时不能宣布丢失，也不能宣布数据完整。 |

## 当前代码证据

- `DreamJourneyBackend/app/main.py:7779`：async request UoW middleware 内同步获取连接、BEGIN、commit/rollback；认证中间件也有同步数据库查询。
- `app/main.py:7935`：`/live` 是同步 handler，仍依赖同步执行资源；bypass 仅适用于指定请求 UoW。
- `app/main.py:7881` → `app/observability/operation_metrics.py:168` → `app/services/postgres_store.py:994`：响应发回前同步写入诊断数据库。
- `app/main.py:5931`：运行能力刷新在 RLock 内收集状态，需纳入锁/数据库等待排查，不能仅凭锁存在认定死锁。
- `DreamJourney_dev/DreamJourney/Sources/Services/DreamJourneyBackendClient.swift:9566`、`:6948`：超时错误包装和通用误导文案；`:17897` 的分类属性将整个枚举认作策略拒绝。
- `DreamJourney/Sources/Domain/OwnerTruth/OwnerTruthContracts.swift:21889`：部分下游映射 permissionUnavailable；Echo 另有 timeout 特判，不能笼统声称所有恢复流程都误判。

以上行号为登记时快照，后续应按符号重新定位。后端 metrics/UoW 基础路径在 `8141ff2` 父提交已存在，部分可追溯至7月；这不排除新负载暴露旧风险，也不证明新版本引入该故障。

## 后续处理顺序

1. 先在隔离环境，以真实 API lifespan、实际 middleware/认证/数据库装配，复现登录后并发只读请求。分别控制取池等待、SQL/提交等待、诊断 sink 等待与同步线程占用；记录 `/live`、`/ready`、业务响应和事件循环延迟。使用同环境旧/新版本对照，不凭报告将其认作基线。
2. 若现场再现，在恢复进程前尽量采集 Python 线程栈、事件循环/线程池/连接池占用、数据库等待事件及阻塞关系；只留脱敏关联和数量，不导出正文或密钥。
3. 基于证据设计最小修复，分别处理健康探针隔离、共享阻塞和客户端错误类型。不得先扩大连接池、延长超时或仅改提示，再宣称首因已解决。
4. 修复客户端分类时保护：真正策略 deny、账户切换、期限/预算、完成一次、迟到回调隔离、现有策略刷新白名单、未知业务写不重发。
5. 若涉及保存/认证/读恢复实际依赖，继续执行同版 `short-A → logical20 → short-B → logical65` 门禁，验证候选、审核、正式记忆和冷启回查；每次长场前独立短场不可省略。
6. 本地开发与真实 Provider、部署、真机分开记录。真机仍由用户主动发起，缺少手机不能阻断本地工作。不得自动操作生产/历史、重放未知写或 commit/push。

## 关闭条件

- 对确认的局部问题有同断言修前失败/修后通过证据；已知故障等待不能拖死健康检查及无关请求，且资源释放/超时收尾有界。
- 超时与真实策略拒绝在类型、界面和后续恢复分支中分离，保留已有权限保护与重试预算。
- 所有受影响保存链和账号回归通过；运维恢复有取证及次数边界。
- 当前阶段仅可标对应 LOCAL_PASS；没有现场证据不得关闭历史根因或宣称生产长期稳定。

参考：[Docker 重启策略](https://docs.docker.com/engine/containers/start-containers-automatically/)、[Starlette 同步线程执行](https://starlette.dev/threadpool/)。
