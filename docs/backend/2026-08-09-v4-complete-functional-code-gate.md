# V4 完整功能代码 Gate

统一入口：

```bash
scripts/run-v4-complete-functional-code-gate.sh
```

该脚本复用现有业务测试和 Gate，按以下顺序验证：

1. 后端全量验证基线。
2. OTP Provider 和 COS/媒体处理的 fail-closed 合同。
3. Worker、Ownership、迁移和 V4 Context Authority。
4. 家庭贡献、导出、删除、closed-pilot 和通知基础。
5. 声音复刻 C0/C1/R5、数字人 readiness。
6. M2 Publication/Visitor/canary 默认关闭边界。
7. `git diff --check`。

输出位于：

```text
tmp/qa/v4-complete-functional-code-gate/<run-id>/
```

`passedCodeGate` 只表示不依赖真实 Provider 和真机的代码闭环通过，不等于生产
发布完成。报告固定列出 OTP、COS、APNs、声音身份/Provider、M2 法律与产品批准
五类外部 Gate，避免将 default-off、fake 或 shadow 证据误标为真实能力。

本地快速复核可跳过重复的全量验证：

```bash
RUN_FULL_VERIFY=0 scripts/run-v4-complete-functional-code-gate.sh
```

正式交接必须使用默认值，包含完整 `verify_backend.sh`。
