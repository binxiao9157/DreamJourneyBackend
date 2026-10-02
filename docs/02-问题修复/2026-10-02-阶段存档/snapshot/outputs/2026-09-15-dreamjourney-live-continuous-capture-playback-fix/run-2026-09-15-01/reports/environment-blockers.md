# 环境阻塞与未执行项

## 隔离 PostgreSQL

状态：`BLOCKED`

- `DATABASE_URL`: not configured
- `TEST_DATABASE_URL`: not configured
- `POSTGRES_URL`: not configured
- `PGHOST`: not configured
- `psql`: unavailable
- `pg_isready`: unavailable
- `docker`: unavailable
- `podman`: unavailable

已完成的替代证据是直接调用生产 `PostgresOwnerTruthConversationRepository` 的脚本化 cursor 合同测试，能捕获漏查询、错查询位置和响应组装错误；它不能冒充真实 PostgreSQL 的事务、锁、并发幂等、回滚与零写副作用。

## 外部运行环境

- 真实火山 SDK 联网会话：`NOT_RUN`
- iPhone 安装及真机长场：`NOT_RUN`
- iPhone 扬声器可听长答：`NOT_RUN`
- 原车蓝牙：`NOT_RUN`
- 真实 Provider 长输入：`NOT_RUN`
- 生产部署及生产数据：按授权边界未执行。
