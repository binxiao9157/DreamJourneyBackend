# 头像USB验收期间发现认证暂停

2026-10-14，关联既有DJ-LIVE-AUTH-01。只取证，未修改认证实现。

## 时间与事实

- 2026-10-13 23:43:20：服务器POST /auth/refresh返回200，sessionRotated/refreshConsumed，版本13；客户端激活日志紧接着记录suspended/authRefreshUnavailable，generation807。
- 本轮头像修复包安装前，通过USB保存的偏好中已无dj_current_user。此证据与安装后的副本分别保留，未输出凭据。
- 23:55:14：普通包覆盖安装后正常启动，激活日志为signedOut/coldStartNoCachedIdentity，generation808。用户反馈“你把我登出了”。
- 服务器只读结果：最新会话active、revoke_reason=null。本次没有19:27旧事故的refreshReuseDetected证据，不能套用旧根因。
- 用户补充在登录页多次点击“重新获取”；代码该按钮执行runtime配置与/v2/auth/challenges，不等同/auth/refresh。点击与上述事件缺少对应时间证据，不能归因于用户。
- 测试账号登录码通过既有测试服务轮换一次后，用户正常登录；00:00:37只读日志active/verifiedLoginActivated/generation809。

## 当前判断与缺口

已确认认证接续异常早于本次修复包安装；未确认服务器成功响应为何没有被客户端接续。需要后续独立检查响应解析、CAS存储、钥匙串、重复/迟到回调以及挂起清理路径，本次不热改认证。安装前未先判读暂停状态，造成登录仍会保留的错误预期，是本轮执行疏漏。

## 执行边界

仅恢复当前测试账号正常登录，不清理资料或记忆，不把服务器凭据写回手机，不注入旧会话。仅USB，没有镜像。后续安装前必须先检查active及本地身份存在；悬挂时先报告，不直接重启验证。

证据：`outputs/2026-10-13-profile-policy-expiry/device/login-transition.json`、`auth-server.log`、`auth-metadata.json`、`relogin-policy.json`。敏感偏好副本在private/tmp，正文不收录。
