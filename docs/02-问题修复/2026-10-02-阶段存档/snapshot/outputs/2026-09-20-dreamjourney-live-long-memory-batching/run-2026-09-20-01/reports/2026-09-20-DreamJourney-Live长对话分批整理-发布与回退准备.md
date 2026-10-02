# 发布与回退准备

本文件仅是准备材料。本轮未执行部署、迁移或开关变更。

## 推荐发布顺序

1. 备份并核对生产迁移头，不执行历史任务重放。
2. 应用兼容且只新增的 `0122_owner_truth_live_long_memory_pipeline` 迁移。
3. 部署能读取新旧数据结构的后端 API、Store 和 Worker，保持新 pipeline 开关关闭。
4. 验证普通 Source、短 Live、候选审核、正式记忆和未知写只读恢复不退化。
5. 部署 iOS 的有界 180 秒状态观察版本。
6. 仅对新场次灰度启用 `OWNER_TRUTH_LIVE_LONG_MEMORY_PIPELINE_ENABLED`；Run 创建时固定 pipelineVersion。
7. 如需长 Live profile，单独灰度 `REALTIME_VOICE_LONG_LIVE_PROFILE_ENABLED`；由服务端选择，客户端不能自选。
8. 完成 Provider、小流量 20 分钟真机和指标核对后再扩大范围；65 分钟以上另行验收。

## 运行配置

- 新 pipeline 默认关闭。
- 长 Live profile 默认关闭。
- 长 profile 默认最大 7200 秒、1 GiB。
- Provider 调用仍使用现有服务端凭据，不把 token 写入客户端、报告或日志。
- Worker 单 Run Provider 并发上限 2，全场预算和恢复预算不可通过增加 Worker 放大。

## 局部回退

1. 关闭新 Run 开关，停止创建新的长记忆 Run。
2. 不删除 `0122` 表、不回滚已写入的数据迁移。
3. 已创建的新版本 Run 继续由兼容 Worker 在合法权限内完成，或保留可恢复进度；不得切回旧整场 8 条路径。
4. 权限撤销时停止新增 Provider 调用和候选提交，但保留原文和恢复坐标。
5. iOS 状态观察可独立回退，不得因此重发 start/append/end/ACK/admit。
6. 若候选发布异常，先关闭新 Run 开关并保留 manifest/Source；不删除候选、正式记忆或历史审计。

## 迁移和兼容判断

- 需要后端迁移和后端/Worker 发布。
- 需要 iOS 发布以获得新的有界观察体验，但服务端候选完整性不依赖客户端无限轮询。
- 旧 pipeline、非 Live Source 和普通 20000 字限制保持兼容。
- 迁移是 additive，不应在紧急回退中 drop 表或清理数据。
