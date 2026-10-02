# DreamJourney 生产服务器磁盘安全清理记录

- 日期：2026-09-20
- 服务器：`VM-0-11-ubuntu`
- 操作授权：用户明确要求清理与当前 DreamJourney 项目运行无关的内容，且不得影响当前项目
- 操作状态：`PASS`

## 1. 清理前状态

| 项目 | 清理前 |
| --- | ---: |
| 根分区总容量 | 40 GB |
| 已使用 | 33 GB |
| 可用 | 5.1 GB |
| 使用率 | 87% |
| Docker 构建缓存 | 4.241 GB，578 项 |
| systemd journal | 约 2.3 GB |

Docker 其余占用只做了只读盘点：

- 镜像逻辑大小：9.23 GB，其中一部分为共享层。
- Local Volumes：1.187 GB。
- DreamJourney 历史 release：单个约 19-26 MB，未删除。
- `/var/lib/dreamjourney` 私有配置及代码备份：约 1.1 MB，未删除。

## 2. 实际删除内容

只执行了以下两类可重建或过期内容清理：

1. Docker 未使用的构建缓存
   - 命令类别：`docker builder prune --all --force`
   - 删除对象：578 项未被运行容器使用的 build cache object。
   - Docker 报告释放：4.241 GB。
   - 影响：后续镜像第一次重建可能重新下载或重新编译部分层；不影响当前运行镜像和容器。

2. 过期 systemd archived journal
   - 命令类别：`journalctl --vacuum-size=512M`
   - 删除对象：超出 512 MB 保留上限的归档系统日志。
   - journal 报告释放：1.9 GB。
   - 影响：较旧的系统历史日志不再保留；当前 journal 和当前服务运行不受影响。

## 3. 明确未删除内容

本次没有执行 `docker system prune`，也没有删除或修改以下内容：

- 任何 Docker image，包括当前镜像和既有回滚标签；
- 任何运行中或已停止容器；
- 任何 Docker volume；
- PostgreSQL 数据、Redis 数据或数据库 schema；
- DreamJourney 当前源码目录和所有 release 目录；
- `.env`、`.runtime.env`、私有配置备份和代码热修备份；
- 生产候选、正式记忆、审核历史、异步任务、Dead Letter 或用户业务数据；
- Nginx、证书或其他项目文件。

## 4. 清理后状态

| 项目 | 清理后 |
| --- | ---: |
| 根分区总容量 | 40 GB |
| 已使用 | 27 GB |
| 可用 | 11 GB |
| 使用率 | 72% |
| Docker 构建缓存 | 0 B，0 项 |

按文件系统口径，可用空间由 5.1 GB 增至 11 GB，约增加 5.9 GB。该数值与两个工具分别报告的释放量存在少量显示口径差异，属于 Docker 共享层和文件系统取整造成的正常差异。

## 5. DreamJourney 保持性验证

清理前后对比的关键容器：

| 容器 | 容器 ID 是否变化 | 状态 | RestartCount |
| --- | --- | --- | ---: |
| API | 未变化 | running | 0 |
| Candidate Extraction Worker | 未变化 | running | 0 |
| PostgreSQL | 未变化 | running | 0 |
| Redis | 未变化 | running | 0 |

清理后调用本机 `http://127.0.0.1:3100/ready`：

- 顶层状态：`ready`
- database：`ready`
- schema：`ready`
- auth：`ready`
- incident：`ready`

结论：本次仅删除可重建构建缓存和过期归档日志，没有重启或替换 DreamJourney 容器，没有触碰数据库、业务数据、release 或回滚镜像。当前项目运行保持正常。
