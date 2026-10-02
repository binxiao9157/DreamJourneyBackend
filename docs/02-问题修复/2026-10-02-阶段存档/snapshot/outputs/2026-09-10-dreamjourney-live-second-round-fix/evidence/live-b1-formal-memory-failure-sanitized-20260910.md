# DreamJourney Live B1 真机日志摘录（脱敏）

日期：2026-09-10（Asia/Shanghai）\
设备：已连接并信任的 iPhone，覆盖安装诊断构建，保留原 App 数据\
iOS 代码：`feature/prd-stitch-ui-adaptation@5fd061fd`，含未提交的 A/T06 修改\
后端：生产 API 与受影响 Worker 使用同一已核验镜像，数据库迁移头为 `0121`\

## 1. 记录边界

本文件只记录设备控制台中已经观察到的安全事件、计数和状态，不包含：

- 正式记忆正文或答案值；
- 用户账号、手机号、令牌、密钥；
- 供应商原始响应正文；
- Live 完整转写。

本轮普通设备控制台是流式监控，结束时未生成原始 `tee` 文件，因此下列内容是本次终端观察记录，不宣称为可重新解析的完整原始日志。T06 的独立证据见同目录 `t06-real-sdk-final-frame-20260910-2326.md`。

## 2. B0 预检日志

观察到：

```text
[DialogEngine] event=liveSnapshotDecoded contextHashMatch=true
  schemaVersion=formal-memory-conversation-v3
  factCount=65 snapshotBytes=54346 snapshotChars=42750

[DialogEngine] event=livePromptPrepared
  factCount=65 promptBytes=15310 promptChars=8088

[DialogEngine] provider websocket connected
[DialogEngine] provider session started
[DialogEngine] assistant opening audio observed
[Echo] user stopped Live
```

B0 用户侧结果：有开场白、有声音、进入聆听；手动停止后返回麦克风入口。B0 结果为 `PASS`。

## 3. B1 四轮正式记忆问答现象

用户依次询问：

1. 本科学校及毕业时间；
2. 硕士学校及专业；
3. 当前职业；
4. 当前饮食偏好。

用户侧结果：

| 轮次 | 语音识别/回复 | 是否有声音 | 是否使用正式记忆 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | 已完成 | 是 | 否，回答不知道 | FAIL |
| 2 | 已完成 | 是 | 否，回答不知道 | FAIL |
| 3 | 已完成 | 是 | 否，回答不知道 | FAIL |
| 4 | 已完成 | 是 | 否，回答不知道 | FAIL |

控制台观察到的安全事件序列：

```text
# 会话启动阶段
[DialogEngine] event=liveSnapshotDecoded contextHashMatch=true
  schemaVersion=formal-memory-conversation-v3 factCount=65
[DialogEngine] event=livePromptPrepared factCount=65
[DialogEngine] event=liveStartEngineSubmitted
[DialogEngine] event=liveStartEngineAccepted

# 四轮均重复出现以下业务序列
[Echo] final ASR accepted
[Echo] event=providerOwnedLiveTurnAccepted answerAuthority=provider
[DialogEngine] assistant response/audio observed

# 本轮未观察到逐轮正式记忆上下文事件
# event=contextBuilt                      NOT_OBSERVED
# event=turnKnowledgeContextSubmitted     NOT_OBSERVED
# SDK directive=ChatRagText(3009)         NOT_OBSERVED

# 会话结束
[Echo] Live transcript summary persisted transcriptTurns=9
[Echo] live-memory state: live -> saving -> queued -> saving
[TencentDigitalHuman] invalidated lifecycle reason=viewWillDisappear
```

九个转写轮次与“开场白 + 四个用户问题 + 四个供应商回答”一致。日志停止前，会后整理尚未观察到 `pendingReview`、`empty` 或明确失败终态，因此本文件不把会后整理判定为通过或失败。

## 4. T06 与 B1 的证据关系

T06 使用合成上下文，已经通过真实 SDK 出站捕获证明：

```text
upstreamStartSessionObserved
contractMatch=true
fieldPath=dialog.dialog.system_role
hashMatch=true
pathMatch=true
roleMatch=true
roleBytes=192
```

T06 证明“SDK 最终控制帧携带了预期合成文本及哈希”，但没有证明：

- 上游是否把 `dialog.dialog.system_role` 解释为有效系统角色；
- 生产正式记忆长文本是否被模型完整采用；
- 每轮用户问题是否收到查询相关的正式记忆上下文。

因此，T06 保持协议发送证据 `PASS`，B1 正式记忆采用验收为 `FAIL`，二者不互相替代。

## 5. 会话退出尾部日志

```text
[TencentDigitalHuman] invalidated lifecycle generation reason=viewWillDisappear generation=3
[TencentDigitalHuman] event=audioOwnerUpdated
  audioOwner=volcengineLocalTTS previousAudioOwner=volcengineLocalTTS
  providerSpeechInFlight=false reason=release:viewWillDisappear
[CFLite] event=runtimeDiagnosticsSnapshotRecorded
  digitalHumanState=none fallbackReason=none outputMode=unknown
  voiceExitEvidence=notSelected voiceExitState=unknown
[SpeechEngine] SpeechAudioRecorderIOS is not running
[SpeechEngine] dealloc
```

该尾部日志发生在用户已经手动结束并离开 Live 视图之后，目前没有证据表明它是四轮正式记忆回答失败的原因。
