# Publication / Visitor 默认拒绝 G0 合同

对应 V4 `WI-S3-01-01` 的最小 G0 切片。本次只把 M2 发布和访客的安全边界写成可版本化、可验证的服务端政策；它不代表任何公开内容、访客访问、数字人公开互动或分享链接已经开放。

## 已实现

- `ReleasePolicySnapshot.publicationVisitorPolicy` 返回不可变、`extra=forbid` 的 `publication-visitor-policy-v1`。
- `publication` 与 `visitorAccess` 是两个独立的 M2 feature，均要求 `G0/G1/G4`，并由 ReleasePolicy 的默认关闭阶段强制 `enforce`。
- 两个 feature 默认 `enabled=false`、`releaseVisible=false`，且返回 `publicationVisitorNotApproved`；任何 capture 都在服务端拒绝。
- Publication policy 固定要求：在世发布主体、未成年人硬拒绝、默认没有允许内容类型（任何非空 `allowedContent` 都会被拒绝）、第三方内容拒绝、Owner 不可见 Visitor 问信正文、AI 披露/撤回/决策收据/安全评估/算法备案均为后续启用前的硬要求。
- Visitor policy 固定要求：成年和身份验证、未成年人硬拒绝、7 天 TTL、离线拒绝、紧急联系人、连续使用上限与依赖提醒均为 2 小时、确定性退出、举报必需、转发拒绝。
- `public_descriptor()` 同步携带同一只读政策，因此 runtime consumer 可显示“尚未批准”，不能仅根据本地开关推断可用。

## 明确未做

- 不新增 PublicationVersion、ShareGrant、VisitorSession、Public Index、public gateway、URL、内容副本或数据库迁移。
- 不复用 iOS `isPrivate=false`、guest 页面、Family 关系、KBLite share 导出或旧本地数组作为公开授权。
- 不持久化发布/访客决策收据；本版本只要求未来启用前必须具备该收据，当前没有任何可批准路径。
- 不增加 iOS feature、入口或 QA override。当前 iOS 对未知 feature 和缺失/过期政策均 fail closed，避免在尚无 Visitor identity/cache scope 时过早引入客户端语义。
- 不开放公开 Voice/Digital Human；该能力仍受独立 V0/M2/G4 限制。

## 验证

```bash
cd /Users/yxj/Documents/Codex/Video/DreamJourneyBackend
PYTHON_BIN=.venv/bin/python scripts/run-backend-publication-visitor-policy-gate.sh
PYTHON_BIN=.venv/bin/python ./scripts/verify_backend.sh
```

通过 G0 gate 仅证明政策默认拒绝、离线拒绝和没有新增公开 route。`G1` 仍需 Release/隐藏入口复核；`G4` 仍需 Product、Privacy、Legal、Security、Operations 的签字。后续 `WI-S3-01-02` 才能在这些前提下考虑独立公开副本与 Grant schema。

## 部署证据

- 后端 `main@0fdd55f` 已于 2026-07-23 部署到 `miao-server`，仅重建 `api` 容器；没有修改 `.env` 或打开任何 feature。
- `/ready` 返回 `ready`，API 容器处于 healthy。
- 匿名 `GET /v2/release-policy?audience=visitor&cohort=closedPilotAdultSelf&clientBuild=1&feature=visitorAccess` 返回 `enabled=false`、`releaseVisible=false`、`releaseStage=M2`、`reason=publicationVisitorNotApproved`、`requiredGates=[G0,G1,G4]`。
- 容器内以已有运行时凭据读取 `/config/runtime`，确认 `releasePolicy.publicationVisitorPolicy` 返回同一 `publication-visitor-policy-v1`，并保持 publication/visitor 全部 false、Visitor TTL 为 604800 秒、offline mode 为 deny。
- 本次不运行任何写入、迁移、Provider、公开 URL 或 Visitor 会话操作；G1/G4 保持未完成。
