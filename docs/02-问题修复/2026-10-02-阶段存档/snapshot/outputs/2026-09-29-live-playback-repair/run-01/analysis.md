# 播放完成缺失：根因证据与修复边界

本轮针对9/29 run-02 SHORT03。该场真实ASR、解码、正文保存和停止后两条候选发布均成功，原生3019/3020事件缺失。没有把它归为保存或DeepSeek容量问题。

## 已核对的SDK边界

锁定版本0.0.14.6.1-bugfix，未升级。头文件声明3019/3020，但arm64静态库Dialog Processor在SpeechMessageCallback只转发core2000音频数据，core2001/2002没有转发分支并直接返回。底层PlayerProcessor在实际ring buffer从空到非空、从非空到空时产生2001/2002。对照官方0.0.14.7稳定包也没有相应转发，因此不以盲目升级作为修复。

[静态库指纹与对照](evidence/sdk-contract-comparison.json)、[锁定版本中间层反汇编](evidence/sdk-pinned-dialog-disassembly.txt)、[底层播放器反汇编](evidence/sdk-pinned-player-disassembly.txt)、[新版对照](evidence/sdk-14.7-dialog-disassembly.txt)。这是当前SDK事件转发缺口证据，与此前真机缺少相应事件相符；不证明历史seq5或所有保存失败同因。

官方[SDK接口文档](https://docs.volcengine.com/docs/DoubaoVoice/End-to-endAndroidSDKinterfacedocumentation?lang=zh)区分播放器音频回调（按播放进度）和解码器回调（解码即发）。文档主要是Android接口，iOS的具体事件缺口以上述本机已链接静态库为准。

## 修复

仍由SDK内置播放器输出，不增加应用第二播放器。开启真实player PCM与decoder PCM观察；按engine generation和reply身份核对两路非零Int16样本的SHA256与精确数量，过滤的仅为SDK填充的精确零。合成结束后，还必须观察到播放器输出的200ms连续纯零样本才能完成。不是200ms定时器，缺回调、错内容、缺尾段、错代次或只合成结束都不能通过。只保存增量摘要与计数，不保留声音正文；单轮最多5分钟非零样本，超限fail closed。

生效范围为provider-owned Live；其PCM证明通过后走现有onTTSFinished恢复聆听。旧的合成+原生播放事件路径保留，已验证完成后拒绝重复结束；停止、打断、新问题与引擎结束重置证明。原正文/停止/候选/审核链不改。

工具独立保留native playbackCompletions和verifiedPlayerPCMCompletions，不把派生PCM排空伪造为SDK3020事件。只有产品实际验证PCM并走完成分支时工具才收到观察信号，且每轮仍要求真实ASR、音频、文字和恢复聆听。额外只读诊断可追查匹配进度。

## 验收边界

局部配置同断言修前红、修后绿见policy-same-assertion-red-green.json；它只证明原配置没有打开实际播放PCM，不冒称完整旧真机业务红。上一轮真实短场失败是完整现场反例。新增13项专项包括错内容、截尾、代次、重置、重复完成、分片与SDK补零，全部本地通过；完整回归及真机结果另行记录。

精确样本核对证明SDK播放数据已消耗相应内容，静音数字流不证明物理扬声器/麦克风声学效果。需要完整短场审核与正式回查才可启动20分钟；遇到首错保留证据，不跳过门禁。

## 首次新短场发现的工具合同缺口

SHORT本场两轮实际播放器PCM排空通过，4条正文均服务端确认、关闭意图持久化、end/ACK/admission成功，状态pendingReview，4条候选可由App真实确认Controller读取。工具在审核前报candidateSourceOrBudgetMismatch。只读核对本场已持久化memory_changeset_proposals：4份均为单一add、无targetMemory、无依赖。V5即使新增记忆也提供proposal，原工具将所有非nil proposal拒绝，误把合法新增当作潜在历史写。

修复仅在测试工具：候选数量预算和Source引用单独断言；V5必须有经产品解码绑定的proposal，所有operation只允许add、不得有目标memory/version、不得有依赖。错误类型、混合操作、空操作、目标引用、缺失V5方案继续拒绝。真实Controller审核与后置正式记忆回查不变。10项实际Swift策略测试通过，host安全测试16项通过。SHORT原失败不改写、不继续旧场写操作；STAGE03重新冻结后另行独立短场SHORT02。

首次LONG20本地语音准备因macOS say超时停止，仅本地合成文件，没有启动手机长场或调用模型。新目录LONG20B重新准备，实际执行仍以同版SHORT02回执通过为前提。

## SHORT02进一步定位的产品合同缺口

本場4条正文完整保存、3条V5候选生成并由真实确认Controller读取；审核POST返回400，仍pending3/formal0。iOS的OwnerTruthInterviewCandidateConfirmationSingleCommand只携带commandId、candidateVersion和action，没有expectedMemoryRevision/expectedChangeSetId/expectedProposalHash；后端同一正式confirmation单条接口已支持并要求V5绑定。通过实际UseCase抓取所发command.backendPayload，在未修代码上两个同断言反例均失败（绑定缺失、无方案仍发送）；不是仅用报告推测。

本次仅为该单条确认命令补当前显示候选的方案绑定，校验candidate身份和版本；V5缺失/不可审核/不匹配方案时不发写。accept/reject使用原方案；correct必须有独立纠正预览，本旧入口没有该能力，因此继续失败关闭，不拿原方案冒充纠正后的方案。没有新增纠正功能。后端无需变更或重新部署；通用档案审核链不改。新增同断言2/2绿，完整最终回归及真机另记。保留SHORT02失败，不向原场重复审核。

测试工具增加只读的确认动作phase/notice和activation失败状态；不返回候选正文，不改变业务完成条件。

## 最终本地补充与归因校正

SHORT03在V5绑定修复后仍返回400，不能把绑定缺失视作这两次现场400的唯一解释。继续核对发现Live单条及批量UseCase使用裸UUID；后端这两个合同要求命令编号首字符为字母，数字开头UUID必然失败。已只改默认工厂为固定字母前缀，保留命令稳定性及未知写规则。

新建隔离PG，使用正式Live确认路由而非通用QA审核入口对照：数字UUID返回400且无写；合法编号但缺绑定返回409且无写；完整绑定和合法编号返回201，正式激活201，1条正式记忆与1个版本，同命令重放200且不重复。原手机失败命令没有保存编号，因此只将该问题标为确定的可复现缺陷、现场400的待验证解释。

最新iOS回归786/0/3、STAGE05签名构建、最终短场A→逻辑20→短场B→逻辑65及重启回查全部通过。PG专项及最终四场都不调用真实Provider。最终修复未再次安装，手机按用户安排可断开。最终结果与证据见[交付报告](README.md)。
