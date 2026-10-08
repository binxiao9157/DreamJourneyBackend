-- Owned by DreamJourneyBackend/deploy/admin-observer-v1.sql.
-- Installed explicitly by DBA; additive views only; no business table mutation.
BEGIN;
SET LOCAL lock_timeout='2s';
SET LOCAL statement_timeout='10s';
CREATE SCHEMA IF NOT EXISTS dj_admin_contract;
REVOKE ALL ON SCHEMA dj_admin_contract FROM PUBLIC;
CREATE OR REPLACE VIEW dj_admin_contract.accounts_v1 WITH(security_barrier=true) AS
 SELECT md5('dj-admin-v1:'||id) account_id, status, created_at, updated_at FROM public.subjects;
CREATE OR REPLACE VIEW dj_admin_contract.sources_v1 WITH(security_barrier=true) AS
 SELECT s.id::text id,md5('dj-admin-v1:'||s.owner_subject_id) account_id,s.vault_id,
 s.source_kind,s.state,s.source_version,s.row_version,s.created_at,s.updated_at,
 s.metadata->>'sessionId' session_id,
 CASE WHEN s.metadata->>'captureMode'='live' THEN 'live'
 WHEN s.source_kind='text' AND s.metadata->>'origin'='ownerTextCaptureV1' THEN 'archive'
 WHEN s.source_kind='conversation' AND (s.metadata->>'captureMode' IN ('naturalInput','text') OR s.metadata->>'origin'='interviewReviewBatchCandidateProposal') THEN 'text'
 ELSE 'unknown' END entry,
 s.metadata->>'completeness' completeness
 FROM owner_truth.sources s;
CREATE OR REPLACE VIEW dj_admin_contract.source_results_v1 WITH(security_barrier=true) AS
 SELECT s.*,
 (SELECT jsonb_agg(jsonb_build_object('id',e.id,'status',e.status,'failure_code',e.failure_code,'created_at',e.created_at,'completed_at',e.completed_at) ORDER BY e.created_at,e.id)
  FROM owner_truth.extraction_results e WHERE e.source_id=s.id::uuid AND e.vault_id=s.vault_id) extractions,
 (SELECT jsonb_agg(jsonb_build_object('id',c.id,'decision',c.decision_status,'version',c.row_version,'updated_at',c.updated_at))
  FROM owner_truth.memory_candidates c WHERE c.source_id=s.id::uuid AND c.vault_id=s.vault_id) candidates,
 (SELECT jsonb_agg(jsonb_build_object('id',m.id,'status',m.status,'version',m.row_version,'current_version',v.version_number,'updated_at',m.updated_at))
  FROM owner_truth.memories m LEFT JOIN owner_truth.memory_versions v ON v.memory_id=m.id AND v.vault_id=m.vault_id AND v.is_current
  WHERE m.source_id=s.id::uuid AND m.vault_id=s.vault_id) memories,
 (SELECT count(*) FROM owner_truth.answer_citations a WHERE a.source_id=s.id::uuid AND a.vault_id=s.vault_id) citation_count,
 (SELECT jsonb_agg(jsonb_build_object('id',t.id,'state',t.state,'version',t.current_version))
  FROM owner_truth.live_memory_topics t JOIN owner_truth.live_memory_topic_revisions r ON t.id=r.topic_id AND t.current_version=r.version
  WHERE r.source_id=s.id::uuid AND t.vault_id=s.vault_id) topics,
 (SELECT jsonb_build_object('id',r.id,'revision',r.revision,'state',r.state,'failure_code',r.failure_code,'completeness',r.publication_manifest->>'completeness','created_at',r.created_at)
  FROM owner_truth.live_recovery_snapshots r WHERE r.source_id=s.id::uuid ORDER BY r.revision DESC LIMIT 1) publication
 FROM dj_admin_contract.sources_v1 s;
CREATE OR REPLACE VIEW dj_admin_contract.sessions_v1 WITH(security_barrier=true) AS
 WITH source_rows AS MATERIALIZED (SELECT * FROM dj_admin_contract.source_results_v1 WHERE created_at>=now()-interval '90 days')
 SELECT 'session:'||s.id id,md5('dj-admin-v1:'||s.owner_subject_id) account_id,s.vault_id,
 CASE WHEN t.entry_mode='live' THEN 'live' WHEN t.entry_mode IN ('naturalInput','recommendation','resume') THEN 'text' ELSE 'unknown' END entry,
 s.state,s.created_at,s.updated_at,s.continuous_client_sequence saved_watermark,
 s.close_requested_client_sequence requested_watermark,
 (SELECT count(*) FROM owner_truth.conversation_messages m WHERE m.session_id=s.id AND m.vault_id=s.vault_id) body_count,
 (SELECT count(*) FROM owner_truth.conversation_messages m WHERE m.session_id=s.id AND m.vault_id=s.vault_id AND m.author='owner') user_turns,
 COALESCE((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.created_at,r.id) FROM source_rows r WHERE r.session_id=s.id::text AND r.vault_id=s.vault_id),'[]'::jsonb) sources,
 COALESCE((SELECT jsonb_agg(jsonb_build_object('id',r.id,'state',r.state,'failure_code',r.failure_code,'updated_at',r.updated_at,'request_count',r.provider_request_count))
  FROM owner_truth.live_memory_runs r WHERE r.vault_id=s.vault_id AND r.product_session_id=COALESCE(s.product_session_id,s.id::text)),'[]'::jsonb) runs,
 (SELECT jsonb_build_object('id',p.id,'revision',p.revision,'state',p.state,'completeness',p.publication_manifest->>'completeness') FROM owner_truth.live_recovery_snapshots p WHERE p.session_id=s.id ORDER BY p.revision DESC LIMIT 1) session_publication
 FROM owner_truth.interview_sessions s JOIN owner_truth.conversation_threads t ON t.id=s.current_thread_id AND t.vault_id=s.vault_id
 UNION ALL
 SELECT 'source:'||r.id,r.account_id,r.vault_id,r.entry,r.state,r.created_at,r.updated_at,
 NULL::bigint,NULL::bigint,NULL::bigint,NULL::bigint,jsonb_build_array(to_jsonb(r)),'[]'::jsonb,NULL::jsonb
 FROM source_rows r
 WHERE NOT EXISTS(SELECT 1 FROM owner_truth.interview_sessions s WHERE s.id::text=r.session_id AND s.vault_id=r.vault_id);
CREATE OR REPLACE VIEW dj_admin_contract.source_content_v1 WITH(security_barrier=true) AS
 SELECT s.id::text id,md5('dj-admin-v1:'||s.owner_subject_id) account_id,s.vault_id,s.state,
 CASE WHEN s.state='active' THEN s.content_payload ELSE NULL END content
 FROM owner_truth.sources s;
REVOKE ALL ON ALL TABLES IN SCHEMA dj_admin_contract FROM PUBLIC;
COMMIT;
