# V4 M0 统一非真机发布门

脚本：`scripts/run-v4-m0-non-device-release-gate.sh`

该门禁只覆盖 V4 M0 的非真机部分，复用已有的测试与 smoke，不创建第二套业务路径。默认 fail-closed：隔离 Postgres 或已部署 API 环境缺失时不能生成 `passed` 证据。

## 覆盖范围

- 后端全量单元测试、合同门、编译与 `git diff --check`。
- 隔离 Postgres：迁移升级/重复执行、正式 Candidate 确认的事务回滚与重放、访谈连续性、家庭静态贡献、Context 持久化。
- 已部署 API：`/ready`、刷新 token 重放拒绝、自然输入、路由认证、Owner A/B 隔离、数据权利、事件审计和公开范围默认关闭。
- 结果输出 `manifest.json` 与 `report.md`；不包含 token、电话号码或业务正文。

## 服务器执行

后端容器已取得 `.env` 中的数据库连接和机器 token 时，在服务器项目目录执行：

```bash
sudo docker compose exec -T api sh -lc '
  cd /app && \
  RUN_ID="v4-m0-non-device-$(date +%Y%m%d-%H%M%S)" \
  OUTPUT_ROOT=/tmp/dreamjourney-v4-m0-gate \
  BACKEND_BASE_URL=http://127.0.0.1:8080 \
  RUN_ISOLATED_POSTGRES=1 \
  RUN_DEPLOYED=1 \
  scripts/run-v4-m0-non-device-release-gate.sh
'
```

脚本会创建并清理名称以 `dj_` 开头的临时数据库；不读取或修改真实用户记录。执行完成后从容器复制本轮目录，再交给 iOS 汇总门禁：

```bash
container_id="$(sudo docker compose ps -q api)"
sudo docker cp "$container_id:/tmp/dreamjourney-v4-m0-gate/<run-id>" /tmp/
```

## 明确保留的后续 Gate

- `EXTERNAL_BLOCKED`：真实短信/身份 Provider。当前线上 smoke 仅证明身份入口默认拒绝、内部 fixture session 的刷新轮换和重放拒绝。
- `DEVICE_REQUIRED`：Wave 7 的麦克风、照片权限、前后台、通知跳转和设备性能验收。

这两个状态不能在本脚本中被标记为通过。
