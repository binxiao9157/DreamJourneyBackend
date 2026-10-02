# 本地环境能力记录

记录时间：2026-09-15（Asia/Shanghai）

## 隔离 PostgreSQL

- `docker`：unavailable
- `psql`：unavailable
- `pg_isready`：unavailable
- 项目当前 `store_backend`：`postgres`
- 配置中的数据库主机：`postgres`
- 配置中的数据库名：`dreamjourney`

结论：本机没有可用于新建、迁移和验证隔离 PostgreSQL 的工具；配置指向容器网络主机，不能把它当作隔离测试库，也没有访问生产数据库。B7-T15 至 T17、B8-T15 至 T17 标记 `BLOCKED`。

## 真实 Provider

- `DEEPSEEK_API_KEY` 是否配置：`False`

结论：真实 Provider 固定合成语料门禁无法执行，未使用 mock 结果冒充真实 Provider 证据。B7-T21 标记 `BLOCKED`。

## 安全边界

- 未输出数据库口令、连接串、token、请求头或 Provider 密钥。
- 未连接生产服务、未部署、未安装 iPhone、未操作生产候选或正式记忆。
