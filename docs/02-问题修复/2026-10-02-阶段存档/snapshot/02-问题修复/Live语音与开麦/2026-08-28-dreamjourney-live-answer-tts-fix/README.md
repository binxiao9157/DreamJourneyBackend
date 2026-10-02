# DreamJourney Live 答案未朗读修复包

> 日期：2026-08-28（Asia/Shanghai）\
> 用途：交给独立 Codex 开发任务实施\
> 本目录只定义 Live 答案已经生成、已经提交，但火山没有朗读的修复\
> 本目录不包含代码修改

## 1. 从这里开始

执行 Codex 必须按以下顺序阅读：

1. 本文件；
2. [`2026-08-28-DreamJourney-Live-答案已生成但未朗读-详细修改指导.md`](2026-08-28-DreamJourney-Live-答案已生成但未朗读-详细修改指导.md)；
3. 需要回看 ASR 定稿、正式记忆检索和音频路由时，再读上级目录的 [`2026-08-27-dreamjourney-live-echo-query-and-audio-bugfix-guide.md`](../2026-08-27-dreamjourney-live-echo-query-and-audio-bugfix-guide.md)。

若两份指导在 Live TTS 提交方式上冲突，以 2026-08-28 文档为准。

## 2. 本次问题的一句话定义

`/echo/answers` 已经生成回答 A，iOS 也已经把 A 交给火山 SpeechEngine，但当前使用的 `ChatTtsText + UseClientTriggerTts` 组合没有进入实际合成和播放。

本次修复不是：

- 修改 DeepSeek；
- 修改回答 A；
- 修改正式记忆；
- 让火山重新生成回答；
- 把 A 变成另一段文字 B；
- 接入 Apple 系统 TTS。

本次修复只是让当前 Fire Live 会话直接朗读 A。

## 3. 源码仓库

### iOS

```text
/Users/gaominge/Documents/Codex/Video/DreamJourney_dev
```

核对本文时的状态：

```text
branch: feature/prd-stitch-ui-adaptation
HEAD:   843ecb19 fix: centralize echo audio session ownership
remote: origin/feature/prd-stitch-ui-adaptation at 8f0a9aad
local:  ahead 3，并存在已暂存和未暂存修改
```

执行前必须重新运行 `git status --short --branch`，不得假设状态没有变化。

### 后端

```text
/Users/gaominge/Documents/Codex/Video/DreamJourneyBackend
```

核对本文时的状态：

```text
branch: main
HEAD:   0227e3d
status: 与 origin/main 一致，工作树干净
```

本次原则上不修改、不部署后端。只有证据表明 `/echo/answers` 没有返回 A 时，才重新打开后端调查；当前已确认不是这个问题。

## 4. 工作树保护

iOS 当前已有用户工作和前序修复，执行 Codex 必须遵守：

1. 禁止 `git reset --hard`；
2. 禁止 `git checkout -- <file>`；
3. 禁止用远程分支完全覆盖本地；
4. 禁止自动 stash、pull、rebase 或切分支；
5. 修改前分别阅读 staged diff 和 unstaged diff；
6. 只编辑详细指导中列出的文件；
7. 不得回退 `843ecb19`、`0dc79423`、`51730c10` 的音频所有权和播放回执修复；
8. 未经用户明确要求，不 commit、不 push。

## 5. 预期修改范围

主要文件：

```text
DreamJourney/Sources/Services/DialogEngineManager.swift
DreamJourneyTests/AudioOwnerLeaseModelTests.swift
```

仅在确有必要补充诊断或回执时，才允许小范围修改：

```text
DreamJourney/Sources/Modules/Echo/EchoViewController.swift
```

不应修改：

```text
DreamJourneyBackend/**
DreamJourney/Sources/Services/DreamJourneyBackendClient.swift
正式记忆/Candidate/MemoryVersion/Projection 相关代码
DeepSeek Prompt
回答风格
登录、家庭管理、Voice Clone、数字人业务逻辑
```

## 6. 执行完成的最低交付物

执行 Codex 必须报告：

1. 根因是否与本文一致；
2. 实际修改的文件；
3. 删除或替换了哪些旧指令；
4. 单元测试结果；
5. iOS 编译结果；
6. 真机日志中是否依次出现提交、TTS Start、Player Start、Player Finish；
7. 回答 A 是否原样进入语音链路；
8. 播放完成后 Live 麦克风是否仍可继续下一轮；
9. 是否修改或部署后端；
10. 当前 Git diff 和残余风险。

## 7. 可直接交给 Codex 的任务提示

```text
请先阅读：
/Users/gaominge/Documents/liftora/outputs/2026-08-28-dreamjourney-live-answer-tts-fix/README.md

然后严格按照：
/Users/gaominge/Documents/liftora/outputs/2026-08-28-dreamjourney-live-answer-tts-fix/2026-08-28-DreamJourney-Live-答案已生成但未朗读-详细修改指导.md

修复 DreamJourney Live 中“/echo/answers 已返回答案 A，界面能得到 A，但火山没有朗读 A”的问题。

先检查 iOS 当前 Git 状态、staged diff 和 unstaged diff，保留所有已有修改。不得 reset、覆盖、stash、pull、切分支或修改后端。

本次只修复答案 A 到火山 TTS 播放的交付链路。不要修改正式记忆、DeepSeek、/echo/answers、回答内容、回答风格、ASR 语义或无关 UI。

优先将当前 Live 会话中的回答 A 改为通过已验证能出声的 SEDirectiveEventSayHello 提交，保持同一个 Fire Live 会话和 dreamJourneyBackend 回答权威。删除未经运行时证明的 ChatTtsText + UseClientTriggerTts 两步假设。提交成功不能等同播放成功，必须继续以 TTSSentenceStart、PlayerStart 和 PlayerFinish 为回执。

完成代码、测试和编译后先报告结果。除非我在该任务中明确要求，否则不要 commit、push、部署或真机安装。
```
