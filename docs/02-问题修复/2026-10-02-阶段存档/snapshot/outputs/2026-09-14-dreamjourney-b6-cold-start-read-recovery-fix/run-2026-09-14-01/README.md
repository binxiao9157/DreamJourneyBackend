# DreamJourney B6 冷启动只读恢复交付索引

日期：2026-09-14\
本地状态：`A_LOCAL_PASS / READY_FOR_B6_DEVICE_RETEST`\
现场状态：`ISSUE-B6-01/B6 = FAIL`，真实 iPhone 复测 `NOT_RUN`

## 交付文件

- 完整报告：`2026-09-14-DreamJourney-B6会后任务冷启动只读恢复-本地交付报告.md`
- T01-T32 清单：`T01-T32-执行清单.md`
- 修复前证据：`evidence/pre-fix/`
- 修复后测试与 UIQA：`evidence/post-fix/`
- 构建证据：`evidence/build/`

## 结果摘要

- 冷启动恢复改为只读状态核实，不再重放 end/ack/admit。
- 检查点、follow-up 和 outbox 按同一工作流合并并单飞恢复。
- follow-up V2 原子迁移保留 V1 回退，完成阶段保持单调和幂等。
- OwnerTruth 回归：423/423 PASS。
- iOS 全量回归：576/576 PASS。
- 三次真实模拟器进程恢复：只读 GET，恢复写 0，未启动麦克风或新会话。
- 隔离 PostgreSQL：状态读取前后业务表计数变化 0。
- 模拟器与通用 iOS 设备目标编译：PASS。
- 本轮没有后端业务改动、部署、生产访问、iPhone 安装、commit 或 push。

## 尚未关闭

- 真机冷启动恢复、候选来源与重复性仍需后续授权复测。
- 历史现场首次失败的最早层级仍未定位，不把策略过期认定为唯一根因。
- 真机复测通过前，B6 保持 FAIL。
