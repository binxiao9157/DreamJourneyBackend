# 2026-09-29 Live 播放恢复与候选确认修复

本轮局部修复与受控本地验收完成：**LOCAL_PASS**。实际 iPhone 三次短场均已停止，完整闭环结果仍为 **DEVICE_SHORT_FAIL**；最终确认请求修复 **DEVICE_RETEST_NOT_RUN**，物理20/65分钟 **NOT_RUN**。用户已明确可以暂时拔掉手机，后续连接与新场由用户主动发起。

不是“长对话已经修好”的结论。本轮真实交互已成功走到待确认候选，当前实际失败发生在后续确认步骤；最终本地修复尚未在手机验证。历史 seq5 首发原因仍未决。

## 1. 证据与根因边界

| 问题 | 证据 | 修复及结论 |
|---|---|---|
| SDK 原生播放完成事件缺失，首轮无法恢复聆听 | 锁定SDK底层播放器发出core2001/2002，但Dialog事件转发没有对应分支；三个新短场原生完成计数仍为0 | 保持SDK内置播放器，按同reply/generation核对实际player/decoder PCM非零样本数量和SHA256，再要求合成结束后200ms实际player静音样本。三个真实短场各两轮均完成并恢复聆听。没有伪造3020或使用固定延时 |
| 测试工具拒绝合法V5新增候选 | SHORT有4条候选，全部持久化proposal为add且无既有目标，工具却拒绝所有非空proposal | 改为仅允许经产品绑定校验的纯add方案，禁止既有memory/version目标及依赖；不扩大历史操作权限 |
| Live专用单条确认漏传V5绑定 | 两项真实UseCase同断言修前红；隔离PG正式路由缺绑定返回409且无写入 | 携带当前显示方案的memoryRevision/changeSetId/proposalHash；错候选、错版本、缺方案提前阻止写入 |
| Live确认命令编号可能违反后端规则 | iOS默认裸UUID可能以数字开头；后端明确要求首字母。隔离PG同一路由数字UUID返回400、无写入 | 单条和批量确认默认编号分别增加interview-single-/interview-batch-前缀，保留同命令幂等及未知写保护 |

最后两项均是可复现合同缺陷。但SHORT02/03的现场400未捕获完整请求，**不能将其中任一项单独宣布为历史400的已证实唯一原因**。SHORT03已包含V5绑定修复但仍400，进一步说明只补绑定不足；新增测试诊断仅记录编号是否合法、HTTP状态和安全错误分类，便于下一次现场定位。

更详细SDK证据与限制见[分析](analysis.md)。本轮没有SDK升级、第二播放器或后端产品代码修改。独立“文字回响”入口、档案上传入口和物理麦克风/扬声器声学效果不在本次数字流覆盖范围。

## 2. 真机结果保持原样

| 本轮场次 | 实际用户轮数 | 正文服务端确认 | 候选 | 正式记忆 | 首个失败 |
|---|---:|---:|---:|---:|---|
| SHORT / STAGE02 | 2 | 4 | 4 | 0 | 工具candidateSourceOrBudgetMismatch |
| SHORT02 / STAGE03 | 2 | 4 | 3 | 0 | 正式单条确认POST 400 |
| SHORT03 / STAGE04 | 2 | 4 | 4 | 0 | 正式单条确认POST 400 |

三场各有2次真实播放器PCM完成证明、原生完成计数均0。实际ASR、回复文字、非静音音频、恢复聆听、end/ACK/admission和候选发布已取得证据。真实火山及候选整理调用确实运行过，因此不能把整轮Provider标成NOT_RUN；也不能把局部成功写成完整Provider/真机验收通过。

原失败不覆盖、不续写审核。未生成有效short-receipt，故没有启动物理20分钟。LONG20本地语音准备曾因say超时失败，LONG20B已准备完成但从未执行。没有处理历史任务、清理账号或重新提交未知业务写。

证据：[SHORT](SHORT/result.json)、[SHORT02](SHORT02/result.json)、[SHORT03](SHORT03/result.json)、[本场服务器核对](evidence/short03-server.json)、[本场请求状态](evidence/short03-request-status.json)。

## 3. 最终本地验收

| 验证 | 结果 | 证据 |
|---|---|---|
| PCM/播放策略专项 | 13项通过 | [结果](playback-local-result.json) |
| V5实际UseCase同断言 | 修前2项失败，修后2项通过 | [红](confirmation-red-result.json) / [绿](confirmation-green-result.json) |
| 最终iOS完整回归 | 786通过、0失败、3独立入口跳过 | [final-ios05](final-ios05-result.json) |
| 工具主机安全测试 | 16/16通过 | [日志](tool-tests-final05.log) |
| V5纯新增工具策略 | 10项实际Swift策略通过 | [证据](evidence/add-only-policy-validation.json) |
| Live专用正式接口+隔离PG | 10项通过：坏编号400、缺绑定409均无写；完整请求201；正式激活201；1条memory/1个version；同命令重放200且不重复 | [结果](evidence/live-confirmation-pg.json) / [可重跑脚本](verify_live_confirmation_pg.py) |
| 最终同版四场 | short-A→logical20→short-B→logical65全部通过 | [顺序结果](four-scene-final/artifacts/runner-complete.json) |
| logical20 | 110用户+110助手；4条长场候选，5条正式记忆含短场；API进程重启后回查通过 | [完整证据](four-scene-final/green/logical20-full-chain.json) |
| logical65 | 150用户+150助手；17条长场候选，18条正式记忆含短场；API进程重启后回查通过 | [完整证据](four-scene-final/green/logical65-full-chain.json) |
| STAGE05隔离签名构建 | BUILD_PASS；未安装手机 | [构建日志](build05.log) |
| 指纹、diff检查 | 产品/工具/隔离源码/完整包匹配；产品与工具diff --check通过 | [核对](evidence/final-fingerprint-check.json) |

四场运行真实iOS模拟器客户端、独立默认API及Worker、隔离PG；模型是loopback受控transport。逻辑时长不等于物理20/65分钟，不能替代真实模型或手机验收。Live专用V5确认PG专项使用显式管理store pool的TestClient及合成种子，只证明正式确认合同和持久化，不冒充实时采集或完整默认lifespan测试；后者由四场独立进程链覆盖。

四场工具来自既有通过版本，唯一环境调整为本机PG端口55520→55529；原端口已占用，没有停止其他服务。[复制来源及指纹](local-chain-tools/provenance.json)。本轮独立PG已关闭；专项临时数据库已删除，四场证据及独立PG数据目录保留。未重跑后端全量套件，不宣称历史7项路由基线问题已消失。

## 4. 具体改动和最终指纹

产品仅4个文件：DialogEngineManager.swift（实际PCM排空验证）、AudioOwnerLeaseModelTests.swift（播放专项）、OwnerTruthContracts.swift（V5绑定和命令编号）、OwnerTruthContractsTests.swift（合同反例）。[本轮增量patch](implementation-only.patch)相对进入本轮时的dirty快照生成，未将已有修改冒称本轮成果；[逐文件前后SHA256](changed-files.json)。

工具4个代码文件：stage.py、LiveDeviceLabRuntime.swift、DialogEngineLabBridge.swift、ArchiveLabBridge.swift。增加独立PCM证明计数、V5纯新增审核门禁、只读确认阶段与安全错误分类；原业务Controller、FeatureGate、确认与激活路径保留。只在隔离副本DEBUG && LIVE_DEVICE_AUTOMATION下注入观察。

STAGE05源指纹：`99584fab50899d2c1dcd4effa200a42e9e5de4f629bd13b1c810918e54dcc1e7`。

工具指纹：`af77e4109cf16399b3adee152a9fcd7e7331693d9f6330360c5d6ad7725dd0e1`。

完整签名包：`fd3548f775844b2b12b3a9f4c55b1f8b651d74d2ffe57a2b56cc0507d63fd06d`。

[stage清单](STAGE05/lab-build.json)、[二进制清单](STAGE05/binary.json)、[四场源码与构建清单](four-scene-final/artifacts/source-build-manifest.json)。二者指纹算法范围不同，不应直接拿哈希字符串作相等比较；各自均绑定该最终源码及对应构建。

V5旧Live单条入口的correct操作仍要求独立纠正预览，该入口没有此能力，故继续失败关闭，不用原方案冒充纠正后方案。没有声称完成新纠正功能；本次自动化只执行授权的纯新增accept。

## 5. 下一次真机安排

无需手机的本地工作已完成，当前可拔掉并正常使用手机。以后用户主动安排后，重新核对源码、工具、签名有效期及后端版本；最新STAGE05未安装，不能将手机上STAGE04视作最终修复。

先运行全新独立短场，必须完成两轮回复、候选可见、审核、正式激活和冷启动精确回查。全部通过才生成并消费同版short-receipt，执行物理至少20分钟。若短场失败，保留首错，长场继续NOT_RUN；不为收集日志重发旧POST。未来65分钟前再做独立短场。物理麦克风/扬声器另行有声验证。

本轮没有commit/push，也没有新增后端部署；最新确认修复仅完成本地验证。操作步骤已更新至[Sol真机指导](../../../02-问题修复/测试与验收/2026-09-22-Sol-iPhone-Live自动化真机测试操作指导.md)，问题已登记至[问题登记册](../../../02-问题修复/DreamJourney问题登记册.md)。
