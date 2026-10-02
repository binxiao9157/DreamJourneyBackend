# Live 两项修复设计的证据索引

日期：2026-09-15。状态：设计已交付，未修改业务代码或安装/部署。

- memory/tests：7 项现有 iOS XCTest，真实模拟器复用已编译测试产物，关键源码指纹核实；7 PASS，不代表本次新修复绿。
- backend/existing-contract-tests.log：7 项现有后端 service/repository/API 内存测试 PASS，不是 PostgreSQL 或模型调用。
- backend/read-and-long-input-probe.py：当前真实只读 builder 缺口与未部署 B7 长输入请求构造边界；使用合成数据，不是历史现场或模型失败证明。
- audio/README.md、source-manifest.json、source-probe-results.json：逐字提取源码片段与真实 reducer，11 项受控机制观察，底层 SDK 为 spy；不是完整 App 或车机复现。
- bluetooth/2026-09-15-原生Live蓝牙路由与SDK音频合同独立审计.md：实际音频所有权/路由代码与 Apple 文档；真实 SDK 自动打断、可听完成和车机复测尚未运行。
- handoff-validation.json：两份正式设计的指纹、所有本地链接检查与 11 个相关源码文件指纹匹配结果。

正式交付位于 02-设计文档/02-问题修改/记忆系统：整场记忆持续采集与结束交接修复设计、长回答朗读中断修复设计。两项共用有序 canonical staging 与完整消息投递接口，分别验收。新修改的红绿、隔离 PostgreSQL、真实 SDK 与原车验收全部 NOT_RUN；历史停音具体触发仍 UNKNOWN。

用户最新范围调整：手机交互优先，原车机专项验收不属于本阶段要求；蓝牙相关历史事实和机制证据保留。正式两份设计已同步更新，旧审计矩阵不作为当前开发清单。
