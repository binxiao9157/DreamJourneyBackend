# 2026-09-30 真机恢复验收 run-02

状态：DEVICE_SHORT_PASS / DEVICE_LONG_FAIL。本轮没有部署后端；已安装当前签名测试包。真机及真实模型实际运行，不以本地结果替代现场。

- 同音专名验收按用户裁定，仅把合成“新桥+相同六位编号”与“星桥+相同六位编号”比较等同；未改原文或保存哈希。工具27项主机测试及Swift专项通过，原run-01失败证据保留。
- 新短A：两用户轮、四条正文、候选发布、实际确认及新进程正式记忆读取PASS，随后才启动长场。
- 长场：43分20.151秒发生调度停顿，72轮完整播放验证，第73用户轮；73用户+73助手正文保存，恢复发布FAIL。80轮目标未完成。候选和正式记忆闭环未通过，不能记为20/40分钟全链PASS。
- 停止后继续观察了恢复发布，未因音频错误立即结束取证。当前Live已关闭，无须用户再点结束。
- 最高优先级：97条事实主题超过内部关联阶段64条限制，在请求前失败；低优先级：超过40分钟送帧间隔0.517609秒，耗时首因待查。
- 未运行额外SHORT-B/LONG40；其目录仅为准备产物。无历史重处理、无新根因产品修改、无commit/push。

[正式问题记录](../../../02-问题修复/记忆系统/2026-09-30-长场主题关联容量不一致导致发布失败/问题记录与原因分析.md)

证据：SHORT-A/result.json、cold-readback.json、short-receipt.json；LONG20/result.json；server-failure-metadata.json；relation-shape-proof.json；server-code-fingerprints.json；host-progress.jsonl。测试工具原始首次沙箱测试失败保留在tools-test.log，主机重跑通过在tools-test-host.log。构建身份见STAGE/lab-build.json与binary.json。证据指纹见evidence-sha256.json。
