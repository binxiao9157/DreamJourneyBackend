# 账号到期终态清理运维说明

日期：2026-08-09

当前部署证据：

- 后端提交 `c2ce275` 已部署，迁移 head 保持 `0085`，`/ready` 与 deployed readiness smoke 通过。
- 部署容器已使用临时 PostgreSQL 数据库通过终态清理 smoke；没有修改生产业务数据。
- 已验证截止日恢复、一次恢复限制、保留令阻断/释放、重复清理幂等、终态不可复活和脱敏回执。
- systemd 单元已安装并通过语法检查；timer 当前保持 `disabled`，等待生产不可逆删除审批后再启用。

## 目标与边界

`dreamjourney-account-terminal-purge.timer` 每小时触发一次终态清理。作业只处理已经进入 `softDeleted`、恢复期限已到且没有有效保留令的账号。

- 清理时间只能来自服务器 UTC 时钟，不能由客户端或请求参数指定。
- 账号在 30 天恢复期内不会被清理；恢复机会仍由持久化的 `restoreCount <= 1` 约束。
- 重复运行是幂等的，已经进入 `purged` 的账号不会再次处理。
- journal 输出只包含截止时间、清理数量和合同版本，不包含用户 ID、手机号或资源明细。
- 应用数据库中的媒体、声音和数字人记录会终止或清除；第三方 Provider 的异步删除仍以数据权利回执为准，不得把“已请求”表述为“第三方已删除”。

## 安装

安装单元不会执行清理。`enable --now` 会使后续到期账号进入不可逆删除，必须先取得生产数据删除审批。

```bash
sudo install -m 644 deploy/systemd/dreamjourney-account-terminal-purge.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/dreamjourney-account-terminal-purge.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dreamjourney-account-terminal-purge.timer
```

确认定时器：

```bash
sudo systemctl status dreamjourney-account-terminal-purge.timer --no-pager
sudo systemctl list-timers dreamjourney-account-terminal-purge.timer --all --no-pager
```

## 手动验证

部署后先运行使用临时 PostgreSQL 数据库的终态清理 smoke。它不会修改生产业务数据：

```bash
sudo docker compose exec -T \
  -e DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 \
  -e BACKEND_BASE_URL=https://dreamjourney-api.liftora.cn \
  api bash scripts/run-backend-account-terminal-purge-deployed-smoke.sh
```

然后手动触发一次实际作业。没有到期账号时 `purgedCount` 应为 `0`：

```bash
sudo systemctl start dreamjourney-account-terminal-purge.service
sudo journalctl -u dreamjourney-account-terminal-purge.service -n 30 --no-pager
```

日志不得包含用户 ID、手机号、Token 或被删除资源列表。作业失败时保留失败状态，修复依赖后再次启动即可；不得通过修改截止时间跳过恢复期。
