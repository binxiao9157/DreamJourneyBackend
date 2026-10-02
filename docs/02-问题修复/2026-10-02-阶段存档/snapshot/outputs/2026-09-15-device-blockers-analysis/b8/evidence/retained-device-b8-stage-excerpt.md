# 保留现场日志白名单摘录

来源：Sol 原任务已保留 CommandExecution；非新真机测试。

| 原行 | UTC时间（若原事件有） | 本地trace别名 | 事件 | 阶段/结果 |
| --- | --- | --- | --- | --- |
| 1438 | 未记录 | — | captureStateChanged | from=live; to=saving; ownerTurnCount=1; persistedOwnerTurnCount=1; queuedTurnCount=0 |
| 1440 | 2026-09-15T15:24:17.788+00:00 | W1 | authChecked | stage=preflight |
| 1441 | 2026-09-15T15:24:17.788+00:00 | W1 | recoveryChecked | stage=preflight |
| 1442 | 2026-09-15T15:24:17.789+00:00 | W1 | featureRevalidated | stage=preflight |
| 1443 | 2026-09-15T15:24:17.790+00:00 | W1 | requestCreated | stage=transport |
| 1444 | 2026-09-15T15:24:17.791+00:00 | W1 | taskCreated | stage=transport |
| 1445 | 2026-09-15T15:24:17.791+00:00 | W1 | taskResumed | stage=transport |
| 1446 | 2026-09-15T15:24:17.834+00:00 | W1 | transportCompleted | stage=transport |
| 1447 | 2026-09-15T15:24:17.835+00:00 | W1 | responseReceived | stage=http; httpStatus=201 |
| 1448 | 2026-09-15T15:24:17.836+00:00 | W1 | jsonDecoded | stage=json |
| 1449 | 未记录 | — | captureStateChanged | from=saving; to=queued; ownerTurnCount=1; persistedOwnerTurnCount=1; queuedTurnCount=0 |
| 1450 | 未记录 | — | endedReceiptDuplicateIgnored | stage=organizationAlreadyOwned; duplicateIgnored=1 |
| 1451 | 2026-09-15T15:24:17.886+00:00 | W2 | authChecked | stage=preflight |
| 1452 | 2026-09-15T15:24:17.886+00:00 | W2 | recoveryChecked | stage=preflight |
| 1453 | 2026-09-15T15:24:17.887+00:00 | W2 | featureRevalidated | stage=preflight |
| 1454 | 2026-09-15T15:24:17.887+00:00 | W2 | requestCreated | stage=transport |
| 1455 | 2026-09-15T15:24:17.887+00:00 | W2 | taskCreated | stage=transport |
| 1456 | 2026-09-15T15:24:17.887+00:00 | W2 | taskResumed | stage=transport |
| 1457 | 2026-09-15T15:24:17.915+00:00 | W2 | transportCompleted | stage=transport |
| 1458 | 2026-09-15T15:24:17.916+00:00 | W2 | responseReceived | stage=http; httpStatus=201 |
| 1459 | 2026-09-15T15:24:17.916+00:00 | W2 | jsonDecoded | stage=json |
| 1460 | 未记录 | — | captureStateChanged | from=queued; to=unavailable; ownerTurnCount=1; persistedOwnerTurnCount=1; queuedTurnCount=0 |
| 1464 | 未记录 | — | runtimeDiagnosticsSnapshotRecorded |  |

W1/W2没有endpoint/resource标签；按照原报告与源码调用顺序分别对应end、ack，日志本身不能独立完成业务命令绑定。

两次写响应仅记录jsonDecoded stage=json，没有typed receipt验收、ack绑定或ack磁盘提交事件。没有admission或具体candidate policy拒绝事件；此前GET的typedDecode不能挪用到这两个写。

因此新证据补齐原场实际时间链，仍不能将过期route机制认定为该次真机根因，亦不能将缺日志当作未发送证明。
