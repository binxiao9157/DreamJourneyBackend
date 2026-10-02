"""Durable private batch themes. No Source, candidate, or formal-memory write.

Independent unit leases and the run's existing exposure budget survive restart.
These local batch summaries are drafts: later cross-batch corrections are resolved
again before the final snapshot may publish them.
"""
from types import SimpleNamespace
from hashlib import sha256
from app.async_effects.owner_truth_live_recovery_worker import RecoveryThemeAssembler, RecoveryThemePageFailed
from app.domain.owner_truth.live_topics import supported_atom_catalog, digest, LiveThemeConflict
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_live_long_memory import LiveLongMemoryUnitPlan


class RecoveryPrivateThemePlanner(RecoveryThemeAssembler):
    stage_prefix = 'private:'

    def _admit(self, lease):
        with self.host._store.owner_truth_live_topic_repository()._cursor() as cur:
            coordinates=self.host._store.owner_truth_live_recovery_repository()._read(
                cur,lease.session_id,lease.context,lease.epoch)
            if coordinates['generation']!=lease.generation:
                raise LiveThemeConflict('privateThemeGenerationChanged')
            cur.execute('''SELECT 1 FROM owner_truth.live_memory_work_units WHERE id=%s
                AND state='running' AND lease_owner=%s AND lease_generation=%s
                AND lease_expires_at>NOW() FOR UPDATE''',(lease.job_id,lease.owner,lease.lease_generation))
            if cur.fetchone() is None:raise LiveThemeConflict('privateThemeLeaseLost')

    def run_once(self):
        store=self.host._store
        with store.request_unit_of_work(correlation_id='private-theme-claim',command_id='private-theme-claim'):
            repo=store.owner_truth_live_long_memory_repository()
            with repo._cursor() as cur:
                # Retain bounded retry wait and make abandoned private jobs
                # eligible again without changing their identity or budget.
                cur.execute('''SELECT u.id,u.run_id,u.ordinal,u.input_hash,i.id AS session_id,
                        r.vault_id,r.owner_subject_id,r.authority_epoch,r.capture_generation
                    FROM owner_truth.live_memory_work_units u
                    JOIN owner_truth.live_memory_runs r ON r.id=u.run_id
                    JOIN owner_truth.interview_sessions i ON i.product_session_id=r.product_session_id
                        AND i.vault_id=r.vault_id AND i.owner_subject_id=r.owner_subject_id
                    JOIN owner_truth.live_recovery_sessions s ON s.session_id=i.id
                        AND s.authority_epoch=r.authority_epoch
                        AND (s.coordinates->>'generation')::integer=r.capture_generation
                    JOIN owner_truth.vaults v ON v.vault_id=r.vault_id AND v.owner_subject_id=r.owner_subject_id
                        AND v.authority_epoch=r.authority_epoch AND v.status='active'
                    WHERE u.kind='atomExtraction' AND u.state='completed'
                      AND EXISTS(SELECT 1 FROM owner_truth.live_memory_atoms a WHERE a.unit_id=u.id)
                      AND NOT EXISTS(SELECT 1 FROM owner_truth.live_memory_work_units d
                        WHERE d.parent_unit_id=u.id AND d.kind='privateThemeDraft'
                          AND (d.state IN ('completed','failed') OR d.lease_expires_at>NOW()))
                    ORDER BY u.created_at,u.id LIMIT 1 FOR UPDATE OF u SKIP LOCKED''')
                row=cur.fetchone()
                if row is None:return dict(status='idle',reason='noPrivateThemeDraftDue')
                parent=str(row['id']);run_id=str(row['run_id'])
                plan=LiveLongMemoryUnitPlan(run_id=run_id,ordinal=int(row['ordinal']),kind='privateThemeDraft',
                    generation=1,ownership=({'parentInputHash':row['input_hash']},),parent_unit_id=parent)
                repo.record_unit(plan)
                cur.execute("SELECT set_config('dreamjourney.live_recovery_worker','v1',true)")
                cur.execute('''UPDATE owner_truth.live_memory_work_units SET state='running',
                    lease_owner=%s,lease_generation=lease_generation+1,
                    lease_expires_at=NOW()+INTERVAL '300 seconds',updated_at=NOW()
                    WHERE id=%s AND state IN ('planned','running')
                      AND (lease_expires_at IS NULL OR lease_expires_at<=NOW()) RETURNING lease_generation''',
                    (self.host._worker_id,plan.unit_id))
                claimed=cur.fetchone()
                if claimed is None:return dict(status='idle',reason='privateThemeAlreadyClaimed')
                lease=SimpleNamespace(job_id=plan.unit_id,session_id=str(row['session_id']),
                    context=OwnerTruthCommandContext(vault_id=row['vault_id'],owner_subject_id=row['owner_subject_id'],actor_subject_id=row['owner_subject_id']),
                    epoch=int(row['authority_epoch']),generation=int(row['capture_generation']),
                    owner=self.host._worker_id,lease_generation=int(claimed['lease_generation']))
                cur.execute('SELECT id,memory,state FROM owner_truth.live_memory_atoms WHERE unit_id=%s ORDER BY id',(parent,))
                atoms=cur.fetchall()
                indices=sorted({i for a in atoms for i in a['memory']['sourceTurnIndices']})
                cur.execute('''SELECT id,author,content_payload,client_sequence_number FROM owner_truth.conversation_messages
                    WHERE session_id=%s AND vault_id=%s AND owner_subject_id=%s AND authority_epoch=%s
                      AND client_sequence_number=ANY(%s) ORDER BY client_sequence_number''',
                    (lease.session_id,lease.context.vault_id,lease.context.owner_subject_id,lease.epoch,indices))
                turns=[dict(index=int(m['client_sequence_number']),messageId=str(m['id']),
                    role='user' if m['author']=='owner' else 'assistant',text=m['content_payload']['text'],
                    contentHash=sha256(m['content_payload']['text'].encode()).hexdigest()) for m in cur.fetchall()]
        try:
            catalog=supported_atom_catalog(atoms=atoms,turns=turns)
            themes,omitted,blocked=self.organize_pages(lease=lease,run_id=run_id,revision=plan.ordinal,
                source_id='private:'+parent,catalog=catalog)
            coverage=dict(scope='privateBatchTheme',parentUnitId=parent,notAuthoritativeUntilFinalRelations=True,
                themes=[t.payload() for t in themes],omittedAtomIds=list(omitted),blockedThemes=list(blocked))
            with self._uow('DraftCommit',lease):
                self._admit(lease)
                store.owner_truth_live_long_memory_repository().record_unit_result(plan=plan,atoms=[],
                    output_hash=digest(coverage),coverage=coverage,lease_owner=lease.owner,lease_generation=lease.lease_generation)
            return dict(status='completed',reason='privateThemeDraftSaved',themeCount=len(themes),unitId=plan.unit_id)
        except Exception as error:
            from app.async_effects.owner_truth_candidate_extraction_worker import _classify_candidate_extraction_failure
            failure=_classify_candidate_extraction_failure(error)
            terminal=isinstance(error,RecoveryThemePageFailed) or not(failure.retryable or failure.contract_retry_eligible)
            with self._uow('DraftFailure',lease):
                self._admit(lease)
                store.owner_truth_live_long_memory_repository().record_unit_failure(plan=plan,
                    failure_code=failure.code,terminal=terminal,lease_owner=lease.owner,
                    lease_generation=lease.lease_generation,retry_seconds=max(5,failure.retry_after_seconds or 0) if not terminal else 0)
            return dict(status='failed' if terminal else 'retryWait',reason=failure.code,unitId=plan.unit_id)
