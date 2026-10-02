# B：44 个方法 / 46 个 PoolClosed 逐项对照

生成器对全部原始错误、子测试数量、旧 HEAD / 当前异常类型及四种绿结果执行断言；任一不符即停止。

|方法|当前源码行|合同意图|原错误数|旧/当前错误装配|旧/当前正确 fixture|四模块顺序对照|
|---|---:|---|---:|---|---|---|
|`test_core_services.AccountDeletionAPITests.test_account_delete_requires_two_confirmations_and_restore_rejects_expired_window`|3011|账号软删除/恢复 API 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.AccountDeletionAPITests.test_account_delete_soft_deletes_and_login_restores_once_by_phone`|2959|账号软删除/恢复 API 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.ArchiveImageAnalysisAPITests.test_archive_image_analysis_dry_run_redacts_secret`|6643|图片分析输入校验/隐私/无密钥/dry-run 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.ArchiveImageAnalysisAPITests.test_archive_image_analysis_rejects_non_generation_allowed_privacy`|6707|图片分析输入校验/隐私/无密钥/dry-run 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.ArchiveImageAnalysisAPITests.test_archive_image_analysis_requires_image_base64`|6678|图片分析输入校验/隐私/无密钥/dry-run 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.ArchiveImageAnalysisAPITests.test_archive_image_analysis_requires_user_and_archive_item`|6688|图片分析输入校验/隐私/无密钥/dry-run 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.ArchiveImageAnalysisAPITests.test_archive_image_analysis_without_key_returns_unavailable`|6723|图片分析输入校验/隐私/无密钥/dry-run 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.CareSnapshotAPITests.test_care_snapshot_api_404_for_missing_user`|3145|关怀摘要 API/权限/脱敏合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.CareSnapshotAPITests.test_care_snapshot_api_never_persists_raw_conversation_payload`|3305|关怀摘要 API/权限/脱敏合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.CareSnapshotAPITests.test_care_snapshot_api_rejects_missing_required_fields`|3335|关怀摘要 API/权限/脱敏合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.CareSnapshotAPITests.test_care_snapshot_api_rejects_raw_text_inside_allowed_fields`|3349|关怀摘要 API/权限/脱敏合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.CareSnapshotAPITests.test_care_snapshot_api_requires_active_family_viewer`|3186|关怀摘要 API/权限/脱敏合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.CareSnapshotAPITests.test_care_snapshot_api_saves_and_returns_latest_by_viewer`|3110|关怀摘要 API/权限/脱敏合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.CareSnapshotAPITests.test_care_snapshot_history_api_returns_recent_snapshots_by_viewer`|3152|关怀摘要 API/权限/脱敏合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.CareSnapshotAPITests.test_care_snapshot_member_reads_require_requester_phone`|3268|关怀摘要 API/权限/脱敏合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.EchoDelayedReplyAPITests.test_echo_delayed_reply_api_rejects_invalid_minutes_and_trigger`|3806|延迟回复和设备 token 参数校验合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.EchoDelayedReplyAPITests.test_echo_delayed_reply_api_rejects_missing_required_fields`|3788|延迟回复和设备 token 参数校验合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.EchoDelayedReplyAPITests.test_push_device_token_api_rejects_invalid_payloads`|3604|延迟回复和设备 token 参数校验合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.FamilyAPITests.test_family_invitation_code_accept_api_marks_member_active`|7009|家庭成员 API/邀请/产品限制合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.FamilyAPITests.test_family_member_accept_api_marks_member_active`|6982|家庭成员 API/邀请/产品限制合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.FamilyAPITests.test_family_member_api_defaults_hidden_digital_human_contract`|6910|家庭成员 API/邀请/产品限制合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.FamilyAPITests.test_family_member_api_persists_digital_human_mode_contract`|6933|家庭成员 API/邀请/产品限制合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.FamilyAPITests.test_family_member_api_rejects_invalid_digital_human_mode_contract`|6966|家庭成员 API/邀请/产品限制合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.FamilyAPITests.test_family_member_direct_accept_still_works_after_blocked_revoke_attempt`|7061|家庭成员 API/邀请/产品限制合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.FamilyAPITests.test_family_member_revoke_api_is_blocked_by_product_rule`|7038|家庭成员 API/邀请/产品限制合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.MailboxAPITests.test_mailbox_letters_api_archives_letter_for_owner_only`|6846|信箱 API/归属/隐私合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.MailboxAPITests.test_mailbox_letters_api_marks_letter_read_for_owner_only`|6803|信箱 API/归属/隐私合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.MailboxAPITests.test_mailbox_letters_api_rejects_private_or_local_letters`|6889|信箱 API/归属/隐私合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.MailboxAPITests.test_mailbox_letters_api_saves_sanitized_metadata_and_lists_by_user`|6758|信箱 API/归属/隐私合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.PasswordAPITests.test_password_change_rejects_invalid_and_unconfigured_credentials`|2936|密码认证 API 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.PasswordAPITests.test_password_login_sets_credential_and_change_requires_old_password`|2897|密码认证 API 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.ProfileAPITests.test_profile_api_rejects_missing_user_empty_nickname_and_invalid_gender`|2878|个人档案 API 保存/读取/校验合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.ProfileAPITests.test_profile_api_saves_and_returns_account_metadata`|2851|个人档案 API 保存/读取/校验合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.TokenAndProxyTests.test_kb_extract_endpoint_dry_run_returns_allowlisted_metadata`|1289|知识提取参数/证据过滤/Provider mock 或 dry-run 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.TokenAndProxyTests.test_kb_extract_endpoint_rejects_non_ai_privacy_scope`|1274|知识提取参数/证据过滤/Provider mock 或 dry-run 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.TokenAndProxyTests.test_kb_extract_legacy_transcript_keeps_provider_entities_without_v2_evidence`|1325|知识提取参数/证据过滤/Provider mock 或 dry-run 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.TokenAndProxyTests.test_kb_extract_v2_dry_run_returns_evidence_policy_without_context_text`|1352|知识提取参数/证据过滤/Provider mock 或 dry-run 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.TokenAndProxyTests.test_kb_extract_v2_filters_provider_entities_before_responding`|1388|知识提取参数/证据过滤/Provider mock 或 dry-run 合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_core_services.TokenAndProxyTests.test_kb_extract_v2_rejects_malformed_turn_fields`|1440|知识提取参数/证据过滤/Provider mock 或 dry-run 合同|3|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_family_visitor_v4_routing.FamilyVisitorV4PrivateEchoBoundaryTests.test_context_build_rejects_family_private_and_legacy_fallback`|88|家庭访客私有上下文拒绝合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_family_visitor_v4_routing.FamilyVisitorV4PrivateEchoBoundaryTests.test_echo_answer_rejects_family_private_before_generation`|99|家庭访客私有上下文拒绝合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_provider_cost_evidence.ProviderCostEvidenceRuntimeTests.test_evidence_recorder_failure_does_not_change_provider_response`|167|已 mock Provider 的费用记录故障隔离合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_provider_redaction.ProviderRedactionBoundaryTests.test_dry_runs_return_allowlisted_metadata_without_private_input`|42|Provider dry-run 脱敏和错误脱敏合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|
|`test_provider_redaction.ProviderRedactionBoundaryTests.test_provider_dry_run_error_is_value_free`|106|Provider dry-run 脱敏和错误脱敏合同|1|PoolClosed / PoolClosed|PASS / PASS|PASS / PASS|

共同归因：启动 API 合同测试时未选用仓库规定的内存 fixture，默认构造了未打开连接池的 PostgresStore；普通 TestClient 请求不运行应用 lifespan，事务中间件先于目标 API 取得连接而失败。

本表没有将 PostgreSQL 持久化测试降为内存测试。上述方法与原断言均未改动，真实 PostgreSQL Worker/事务/正式记忆验证由独立 C 项及既有 final-06 链提供。
