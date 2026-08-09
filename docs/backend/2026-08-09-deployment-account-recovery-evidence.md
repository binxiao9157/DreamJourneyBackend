# DreamJourney F0-03 部署账号与恢复证据

日期：2026-08-09
状态：`PASSED_WITH_RECOVERY_NO_GO`

## 验证范围

本轮只统一部署账号、Git/特权边界、配置备份隔离、部署前检查与恢复证据，不修改业务 API、数据库 schema 或 Provider 配置。

## 脱敏结果

| 检查项 | 结果 |
| --- | --- |
| SSH 部署入口 | `ubuntu` |
| Git 仓库 owner / credential | `miao` |
| 特权边界 | 非交互 `sudo`，root 不持有 Git 私钥 |
| 服务器仓库 | `main@f84667a`，工作区 clean |
| 私密配置 | 活跃 `.env` 为 `root:root 0600` |
| 历史配置备份 | 42 份从 Git 工作区迁入 root-only 隔离目录；0 份删除 |
| Live deployment preflight | passed |
| `/ready` | database/schema/auth/incident ready |
| 数据库 migration | expected `0085`，applied `0085`，pending 为空 |
| PostgreSQL backup | 连续 2 份当前 head 加密备份通过 |
| Backup retention | audit-only；automatic deletion=false |
| Backup timers | backup 与 retention timer enabled |
| 恢复证据 | 已存在 2 份脱敏记录；最新仍为 `cutoverDecision=NO_GO` |

`NO_GO` 不表示当前 API 故障。它表示 receipt/root authority 尚未达到生产自动切流条件，恢复工具按设计阻止未经批准的切流。本轮没有伪造 `GO`，没有执行 down migration，没有删除生产数据库或旧备份。

## 本轮 Gate

- `bash scripts/deployment-preflight.sh --contract-only`：passed。
- `python3 -m unittest tests.test_deployment_operations_contract -v`：3 项通过。
- backup/recovery 相关 unittest：21 项通过。
- 本地 backup contract smoke：passed。
- 本地 recovery contract smoke：passed，危险恢复保持 fail-closed。
- 服务器 `bash scripts/deployment-preflight.sh`：passed。
- 服务器 readiness deployed smoke：passed。
- 服务器 backup deployed smoke：passed，schema head `0085`。

## 后续边界

1. 日常部署统一执行 `docs/backend/2026-08-09-deployment-account-recovery-runbook.md`。
2. 配置备份销毁需要独立审批和双人复核；当前隔离目录不自动删除。
3. 生产恢复切流必须先补齐可信 receipt replay/root authority，再单独执行恢复审批；不得把本轮 `NO_GO` 改写为完成。
