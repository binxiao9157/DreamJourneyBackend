# S4 run-02 部署与真机验证

2026-10-10。本报告保留本轮证据，前次失败报告不覆盖。本轮部署及真机取证完成；长场正式确认等待用户裁定。状态：**DEPLOY_PASS / DEVICE_S4_TARGETED_PASS / DEVICE_LONG_PUBLICATION_PARTIAL / LONG_CONFIRM_NOT_RUN / READING_REVIEW_PENDING**。不宣称 S4 整体完成。

## 版本和范围

部署至用户授权服务器 dreamjourney-cloud。镜像 `sha256:46efd412192d1fb50f971707c4125345615b8b41962bcf978d41ad229918384e`。API 与六个业务 Worker 的 332 个应用文件一致，schema 129 未变，非目标容器未变，逐服务回滚资料保留。后端 HEAD b30798643c0127011c7ecbabe187bf4a25edfe07，加本地已验收未提交修复；本轮未热改产品或 push。

iOS 复用已安装 S3 签名测试包，执行身份 `efb3e3c4c4341c36e7ea134a6c2c70aa259c1ecbb9664a9b6b2895e8e39b147e`；没有重复安装、卸载或修改登录。本轮真实火山/DeepSeek、实际 iPhone Controller；合成 PCM 输入不等于外部麦克风与扬声器声学验收。

证据根：[run-01](</Users/gaominge/Documents/liftora/outputs/2026-10-10-s4-topic-prose-device/run-01>)。发布包、部署回执、最终哈希、手机 manifest/result、服务器本场审计均在此目录。

## 专项结果

- 独立 Live 短场：2 用户轮、4 条正文，2 主题/2 正式事实，确认和新进程冷读通过。原始证据归档后精确清理。
- 档案分别输入折纸练习及蓝色收纳盒：2 正式事实归为同主题。真实模型正文：“每周六上午，我在家练习折纸。我把折纸放在蓝色收纳盒里。”7 项引用/归组/读取断言通过。
- 档案输入 2019 年咖啡、文字输入 2024 年咖啡：真实预览 temporalChange/sameStatementAtDistinctExplicitYears，准确指向 2019 正式版本；原 Source/candidate 继续确认成功、冷读通过。没有将错误 add 当作时期关系通过。
- 真实正文：“2019年和2024年，我都喜欢喝咖啡。”11 项局部更新检查通过：无关折纸/照片块完全相同、所有旧正式数据不变、主题和块身份稳定、版本更新、两年均保留、精确事实/版本/hash、新进程重读稳定。
- 同年重复输入：addEvidence 正式确认与冷读通过；仍为同一条 2024 正式记忆，版本递增，其他正式数据不变。5 项断言通过。

本次手机折纸实际 object=折纸，没有再次产生上次的 object=容器位置；完整旧 payload 分支的证明来自本轮部署前本地 PG 回放与真实模型样例，不能冒充本次手机重新产生了那个形态。

## 中断及限制

1. coffee-2024 初次测试清单遗漏 allowedTargets，确认前保护以 reviewWouldTouchExistingMemory 停止。手机 relation-preview 已证明 temporalChange 和旧目标正确；只补本场明确目标，使用新运行标识继续原 Source/candidate，未重新输入、未更换业务期望。原 FAIL 与 guard-analysis 保留。
2. coffee-repeat 业务确认通过后，devicectl 冷启动报 CoreDeviceError 10004，无法取得进程号。只补一次 GET-only 新进程冷读通过；原启动错误保留，未据此认定产品崩溃或认证已修复。
3. 模型成文前的 faithful 拼接在折纸两句之间未补句号，后续真实模型成文修正。此显示质量观察保留，不能把最终润色通过等同降级文案完美。
4. 同年补证使正文版本更新后暂回 faithful 两句，等待新版本派生任务；本轮未把旧 polished 文本当作新版本结果。

## 测试数据隔离

前三份旧合成测试来源及本轮首短场已逐一精确失效，原审计保留。专项完成后六个本场来源、五条正式事实精确失效，其他正式数据指纹不变。此为已授权测试数据清理，不是全账户物理删除。

## 自然十分钟保护回归

独立 short-04 两轮完整通过：4 条正文、1 主题/1 正式事实，确认/冷读通过。精确清理该来源后，使用绑定本版、当前账号、当前手机且单次消费的短场回执启动 long-10m。

- Live 612.908 秒（约10分13秒）；停止来源 `productTenMinuteNaturalClose`。不是工具在9分钟提前结束。
- 26 用户轮、52 条正文；全部 Source 包含且手机持久化完整。结束位置已知，receivedRanges=[1,52]，missingRanges=[]，保存队列0。
- 26 次真实回复的非静音播放验证通过；合成输入最大间隔0.110秒。没有本场开麦失败或音频错误；真实外部麦克风/扬声器声学仍NOT_RUN。
- 约693秒时读到发布结果：4主题、22候选；页面“整理完成，已存入待确认记忆\n少量内容暂未整理完成”。这是部分发布成功，不是全部整理失败。
- 完整候选覆盖断言 `themeMissingExpectedFacts` 未通过，原FAIL保留。长场正式确认及冷读NOT_RUN，不能用短场/专项确认结果替代。
- 原始ASR关键词断言未发现差异，但人工对照发现第22轮“贴进笔记本”转写为“贴近笔记本”，已单独提交用户裁定。
- 32轮为预备输入上限；自然结束后未输入的27—32轮不计算成已说内容丢失。

### 四项未发布的具体原因边界

第8轮绿色标签标待处理、第9轮黄色标签标完成、第11轮准备小台灯、第26轮小闹钟提醒起来走走均在原始正文，且已提取为原子事实。最终publication_manifest中两个blockedThemes为`labeling-and-tools`和`lamp-and-reminder`，均为`themeSupportUnresolved`，groupingRejected=true；四个atom列入omittedAtomIds。台灯调节角度（第12轮）仍在已发布候选。

另有关系审核返回`candidateExtraction.live.relationValidate.resolvedMemoryMissing`，manifest记录`relationResolutionFailed`且安全复核passed。现有降级逻辑继续发布其他内容。已确认未通过的阶段，不据这些代码断言模型为何作出不确定判断，亦未确定与本轮S4三文件修复有因果关系；没有新增模型调用/重试业务来掩盖本次结果。

本场run状态published，failure_code为空，provider_request_count=87；不能将中间一个rejected调用等同整体未发布。详见`long-10m/pipeline-audit.json`、`coverage-review.json`、`server-audit.json`及原result。

用户已获询问是否接受4项遗漏和1处同音差异；截至记录更新仍待回复。已准备4个精确主题、同一Source/candidate的手机续确认清单，尚未执行。不需要重开Live或重新输入26轮。

## 最终服务与数据状态

测试后七服务运行、0重启、332文件仍匹配部署镜像，ready通过；0 leased任务。本轮已通过专项和短场测试数据均归档后精确清理。当前账号保留本次长场22条pending、0 active正式，待用户裁定，不清除未确认候选。普通App已正常启动，未再次开麦；没有重新安装、注销账号、commit或push。

正文阅读确认仍待用户评审。本轮真实模型示例已发出评审问题；不能替用户宣布阅读质量满意。历史MIC04、登录问题及主题审核质量观察未关闭。

## 追加核实：分组失败，不是原始事实不确定

2026-10-10，用户仅接受“贴进→贴近”的差异；四项遗漏尚未获接受，22条正式确认仍未执行。本次只读分析，没有新增模型调用或修改产品。

此前将 `themeSupportUnresolved` 概括为“模型返回不确定”不准确。实际证据：

1. 会中，绿色/黄色标签归为同一流程，审核 supported 且 sameSubjectEvent=true；准备台灯、放闹钟分别成组后也全部通过。
2. 会后重新归组，将标签和抽屉收纳合为一组，将台灯和闹钟合为一组。两组均返回 supported，但 sameSubjectEvent=false；时间相容、纠正已解决均为true。因此代码拒绝分组，标记 themeSupportUnresolved/groupingRejected。
3. 有界重组救回抽屉收纳、台灯角度，但把其余四项又组成“标签标记与角落台灯闹钟安排”。本次返回 unsupported、sameSubjectEvent=false。返回没有文字理由，不能编造模型内部判断。
4. 重组后四项仍未通过，列入 omittedAtomIds。第0层失败处理未直接复用会中已通过的小主题；代码第1层及以上保留已审核子主题的保护不覆盖此处。

可证实的链路原因是：会后重新混组、重组未确保分开、失败后未兜回已验证小主题。不是正文丢失，也没有这两次主题审核超时或欠费的证据。尚不能据此称为本轮S4新回归；本次S4修复没有修改相关三份主题组织文件，当前源码与部署清单hash一致。

修复方向应保留有效事实，纠正分组，并在证据及版本匹配时有界复用已通过的小主题；不能取消事实证据和纠正/撤回检查。本条仅分析，没有启动修复。原始证据：`long-10m/theme-grouping-cause.json`、`pipeline-audit.json`；ASR接受记录：`coverage-review.json`。
