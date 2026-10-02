# 后端 7 项路由失败基线独立复核

- 复核日期：2026-09-19。
- 结论：已提交 HEAD 基线中 **7/7 复现完全相同的路由 259/260 断言失败**，均不是本轮候选整理修复引入的回归。
- HEAD：`ffd02f37e0e50c43e23420f1a69e69a0ccdb08cc`。
- 隔离方式：`git archive HEAD` 只读导出到 `/private/tmp/dj-route-baseline-elpyyrvs`；未建 git worktree，未改原工作区。
- 环境：复用后端 `.venv`，`STORE_BACKEND=memory`，未访问网络、生产或手机。

## 7 项测试

- `test_auth_sessions.AuthSessionAPITests.test_runtime_exposes_cross_account_policy_without_claiming_enforce_ready`：FAIL，`AssertionError: 260 != 259`。
- `test_route_authentication.RouteAuthenticationPolicyTests.test_every_route_has_one_typed_authentication_contract`：FAIL，`AssertionError: 260 != 259`。
- `test_route_authentication.RouteAuthenticationStartupTests.test_current_application_is_complete_in_production_enforce_mode`：FAIL，`AssertionError: 260 != 259`。
- `test_route_authentication.RouteAuthenticationStartupTests.test_deployed_route_smoke_inventory_matches_registry`：FAIL，`AssertionError: 259 != 260`。
- `test_route_ownership_registry.RouteOwnershipRegistryTests.test_audit_summary_contains_only_templates_and_counts`：FAIL，`AssertionError: 260 != 259`。
- `test_route_ownership_registry.RouteOwnershipRegistryTests.test_registry_covers_every_fastapi_business_route_exactly_once`：FAIL，`AssertionError: 260 != 259`。
- `test_runtime_capabilities.RuntimeCapabilityConfigTests.test_runtime_config_exposes_complete_route_authentication_inventory`：FAIL，`AssertionError: 260 != 259`。

## 执行命令

工作目录：`/private/tmp/dj-route-baseline-elpyyrvs`。

```sh
STORE_BACKEND=memory PYTHONPATH=.:tests /Users/gaominge/Documents/Codex/Video/DreamJourneyBackend/.venv/bin/python -m unittest -v test_auth_sessions.AuthSessionAPITests.test_runtime_exposes_cross_account_policy_without_claiming_enforce_ready test_route_authentication.RouteAuthenticationPolicyTests.test_every_route_has_one_typed_authentication_contract test_route_authentication.RouteAuthenticationStartupTests.test_current_application_is_complete_in_production_enforce_mode test_route_authentication.RouteAuthenticationStartupTests.test_deployed_route_smoke_inventory_matches_registry test_route_ownership_registry.RouteOwnershipRegistryTests.test_audit_summary_contains_only_templates_and_counts test_route_ownership_registry.RouteOwnershipRegistryTests.test_registry_covers_every_fastapi_business_route_exactly_once test_runtime_capabilities.RuntimeCapabilityConfigTests.test_runtime_config_exposes_complete_route_authentication_inventory
```

原始本地结果：`/private/tmp/dj-route-baseline-elpyyrvs/route-baseline-seven.log`。

补充只读核查：同一 HEAD 的生产 enforce 启动校验返回 `routeCount=260, unclassifiedCount=0, enforcementMode=enforce`。2026-09-15 提交 `f3ebc8a4937a5be8bc27762369d4193ea683d0ac` 增加了 `GET /v2/vaults/{vault_id}/interview-sessions/{session_id}/live-delivery-status` 的应用路由和 typed registry；路由清单测试及烟测数字仍为 259。

本结论仅分离既有清单漂移；不把 PostgreSQL 门禁或本轮新增业务验证缺口判为通过。本轮未修改路由、测试期望或业务代码。
