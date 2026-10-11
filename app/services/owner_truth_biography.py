"""Durable, bounded biography composition, independent of fact confirmation.

Discovery uses the existing durable memory revision ledger for explicitly
selected Vaults. No historical sweep or account discovery runs on startup.
Each unchanged topic has one job. GET reads artifacts; it never admits work.
"""
from copy import deepcopy
from uuid import uuid4
import json
from psycopg.types.json import Jsonb
from psycopg.rows import dict_row
from app.domain.owner_truth.person_biography import apply_composition, digest, MODEL_CONFIG_VERSION, MAX_SENTENCES, MAX_INPUT_CHARACTERS, input_within_budget

MAX_ATTEMPTS = 2
LEASE_SECONDS = 90


def biography_model_config(settings):
    # Includes configured transport identity without logging its potentially
    # private URL. A configuration change cannot reuse prior model output.
    return MODEL_CONFIG_VERSION + ":" + digest({"endpoint": settings.deepseek_base_url,
        "model": "deepseek-chat", "temperature": 0, "maxTokens": 1800})[:24]


class PostgresBiographyRepository:
    def __init__(self, connection):
        self.connection = connection

    def confirmed_topic_bindings(self, *, context, entries):
        """Read presentation grouping only through the current activation proof.

        Edited/replaced content never inherits the old candidate's theme by ID
        alone. No candidate summary is returned as a formal fact.
        """
        expected = {e['memoryVersionId']: e for e in entries}
        if not expected:
            return {}
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""
                SELECT m.id AS memory_id, v.id AS version_id, v.content_hash,
                       v.source_id, c.id AS candidate_id, t.id AS topic_id,
                       t.current_version AS topic_version, r.proposal_hash,
                       r.payload->'theme'->>'title' AS title
                FROM owner_truth.memories m
                JOIN owner_truth.memory_versions v ON v.memory_id=m.id AND v.vault_id=m.vault_id
                JOIN owner_truth.decision_receipts d ON d.id=v.decision_receipt_id AND d.vault_id=v.vault_id
                JOIN owner_truth.memory_candidates c ON c.id=d.candidate_id AND c.vault_id=d.vault_id
                JOIN owner_truth.live_memory_topic_members member ON member.candidate_id=c.id
                JOIN owner_truth.live_memory_topics t ON t.id=member.topic_id AND t.vault_id=m.vault_id
                JOIN owner_truth.live_memory_topic_revisions r ON r.topic_id=t.id
                    AND r.version=t.current_version AND member.version=r.version
                JOIN owner_truth.sources s ON s.id=v.source_id AND s.vault_id=v.vault_id
                WHERE m.vault_id=%s AND m.owner_subject_id=%s
                  AND c.owner_subject_id=m.owner_subject_id AND t.owner_subject_id=m.owner_subject_id
                  AND s.owner_subject_id=m.owner_subject_id
                  AND m.status='active' AND v.is_current AND s.state='active'
                  AND v.id=ANY(%s::uuid[]) AND t.state='accepted'
                  AND d.decision IN ('accepted','corrected')
                  AND c.decision_status IN ('accepted','corrected')
                  AND v.payload->>'candidateId'=c.id::text
                  AND member.candidate_proposal_hash=c.payload->>'proposalHash'
                  AND r.source_id=c.source_id AND r.source_id=v.source_id
                  AND v.payload->'content'=c.payload->'content'
                ORDER BY v.id,t.id
            """, (context.vault_id, context.owner_subject_id, list(expected)))
            rows = cursor.fetchall()
        result, conflicts = {}, set()
        for row in rows:
            entry = expected.get(str(row['version_id']))
            mid = str(row['memory_id'])
            if (not entry or mid != entry['memoryId']
                    or row['content_hash'] != entry['contentHash']
                    or str(row['source_id']) != entry.get('sourceId')):
                continue
            if mid in result:
                conflicts.add(mid)
            result[mid] = dict(memoryId=mid, memoryVersionId=str(row['version_id']),
                               contentHash=row['content_hash'], sourceId=str(row['source_id']),
                               candidateId=str(row['candidate_id']), topicId=str(row['topic_id']),
                               topicVersion=row['topic_version'], proposalHash=row['proposal_hash'],
                               title=row['title'])
        return {key: value for key, value in result.items() if key not in conflicts}

    def _one(self, query, params):
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, params)
            return cursor.fetchone() if cursor.description else None

    def ensure(self, *, context, block):
        self._one('''INSERT INTO owner_truth.biography_topic_jobs
            (vault_id, owner_subject_id, topic_id, source_revision, payload)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (context.vault_id, context.owner_subject_id, block['topicId'], block['sourceRevision'], Jsonb(block)))

    def claim(self, *, context):
        token = str(uuid4())
        row = self._one('''WITH next AS (
            SELECT vault_id, topic_id, source_revision FROM owner_truth.biography_topic_jobs
            WHERE vault_id=%s AND owner_subject_id=%s AND attempts < %s
              AND ((state='pending' AND available_at<=NOW()) OR (state='running' AND lease_until<NOW()))
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
            UPDATE owner_truth.biography_topic_jobs j SET state='running', attempts=j.attempts+1,
              lease_token=%s, lease_until=NOW()+(%s * INTERVAL '1 second')
            FROM next n WHERE (j.vault_id,j.topic_id,j.source_revision)=(n.vault_id,n.topic_id,n.source_revision)
            RETURNING j.*''', (context.vault_id, context.owner_subject_id, MAX_ATTEMPTS, token, LEASE_SECONDS))
        return row

    def settle(self, job, *, state, result=None, error=None, metadata=None):
        # Expired or stolen leases cannot commit. Attempts are never reset.
        return self._one('''UPDATE owner_truth.biography_topic_jobs
            SET state=%s, result=%s, error_code=%s, request_metadata=request_metadata || %s::jsonb, lease_until=NULL,
                available_at=NOW()+INTERVAL '15 seconds'
            WHERE vault_id=%s AND topic_id=%s AND source_revision=%s AND state='running'
              AND lease_token=%s AND lease_until>=NOW() RETURNING topic_id''',
            (state, Jsonb(result) if result is not None else None, error, Jsonb([metadata] if metadata else []),
             job['vault_id'], job['topic_id'], job['source_revision'], job['lease_token'])) is not None

    def expire_exhausted(self, *, context):
        self._one('''UPDATE owner_truth.biography_topic_jobs SET state='fallback', error_code='attemptBudgetExhausted'
            WHERE vault_id=%s AND owner_subject_id=%s AND state='running' AND lease_until<NOW() AND attempts>=%s''',
            (context.vault_id,context.owner_subject_id,MAX_ATTEMPTS))

    def retire_other_revisions(self, *, context, blocks):
        # Cancel obsolete queued/in-flight revisions even when every fact was deleted.
        keep = [[b['topicId'], b['sourceRevision']] for b in blocks]
        self._one('''UPDATE owner_truth.biography_topic_jobs j SET state='obsolete', lease_until=NULL
            WHERE vault_id=%s AND owner_subject_id=%s AND state IN ('pending','running')
              AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(%s::jsonb) k
                 WHERE k->>0=j.topic_id AND k->>1=j.source_revision)''',
            (context.vault_id,context.owner_subject_id,Jsonb(keep)))

    def completed_many(self, *, context, blocks):
        keys = [[b['topicId'], b['sourceRevision']] for b in blocks]
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""SELECT topic_id,source_revision,result FROM owner_truth.biography_topic_jobs j
                WHERE vault_id=%s AND owner_subject_id=%s AND state='ready'
                AND EXISTS (SELECT 1 FROM jsonb_array_elements(%s::jsonb) k
                  WHERE k->>0=j.topic_id AND k->>1=j.source_revision)""",
                (context.vault_id,context.owner_subject_id,Jsonb(keys)))
            return {(row['topic_id'],row['source_revision']):row['result'] for row in cursor.fetchall()}

def read_composed_model(repository, *, context, model):
    result = deepcopy(model)
    biography = result['biographyProjection']
    artifacts = repository.completed_many(context=context, blocks=[b for s in biography['sections'] for b in s['blocks']])
    for section in biography['sections']:
        for i, block in enumerate(section['blocks']):
            response = artifacts.get((block['topicId'], block['sourceRevision']))
            if response is not None:
                try:
                    section['blocks'][i] = apply_composition(block, response)
                except (ValueError, TypeError, KeyError):
                    pass  # Corrupt artifact cannot leak into the authoritative read.
    biography['documentVersion'] = digest({k:v for k,v in biography.items() if k != 'documentVersion'})
    result['modelVersion'] = digest({k:v for k,v in result.items() if k != 'modelVersion'})
    return result

class BiographyWorker:
    def __init__(self, store, compose):
        self.store, self.compose = store, compose

    def _uow(self):
        return self.store.request_unit_of_work(correlation_id='biography-derivation', command_id='biography-derivation')

    def _snapshot(self, context):
        from app.services.owner_truth_person_memory_profile import OwnerTruthPersonMemoryProfileService
        # Avoid reading artifacts during job discovery; the stable faithful input
        # must not be dependent on an earlier model's sentence order.
        service = OwnerTruthPersonMemoryProfileService(
            self.store, biography_topics=True, include_composition=False,
            composition_config=getattr(self.compose, "configuration_id", MODEL_CONFIG_VERSION),
        )
        profile = service.read(context=context)
        return [b for s in profile.memory_model['biographyProjection']['sections'] for b in s['blocks']]

    def run_once(self, *, context):
        # The revision/confirmed facts already survive a post-confirm crash.
        # Discovery is repeatable and idempotent; no best-effort callback needed.
        with self._uow():
            blocks = self._snapshot(context)
            repo = self.store.owner_truth_biography_repository()
            repo.expire_exhausted(context=context)
            repo.retire_other_revisions(context=context, blocks=blocks)
            for block in blocks:
                repo.ensure(context=context, block=block)
            job = repo.claim(context=context)
        if job is None:
            return {'status':'idle'}
        block = job['payload']
        result, error, state = None, None, 'ready'
        used_provider = False
        try:
            if not input_within_budget(block):
                raise ValueError('compositionInputBudgetExceeded')
            # No transaction or connection is held across this provider call.
            if len(block['sentences']) == 1:
                result = {'sentenceIds': [block['sentences'][0]['sentenceId']]}
            else:
                used_provider = True
                result = self.compose(deepcopy(block))
            apply_composition(block, result)
        except (TimeoutError, ConnectionError):
            state = 'pending' if job['attempts'] < MAX_ATTEMPTS else 'fallback'
            error = 'providerUnavailable'
        except (ValueError, TypeError, KeyError) as exc:
            state = 'fallback'
            error = 'compositionNotIntegrated' if str(exc) == 'compositionNotIntegrated' else 'invalidComposition'
        with self._uow():
            current = {b['topicId']:b for b in self._snapshot(context)}
            if current.get(block['topicId'],{}).get('sourceRevision') != job['source_revision']:
                state, result, error = 'obsolete', None, 'sourceChanged'
            ok = self.store.owner_truth_biography_repository().settle(job, state=state, result=result if state=='ready' else None, error=error, metadata=(provider_metadata(self.compose) if used_provider else None))
        return {'status':state if ok else 'leaseLost', 'attempt':job['attempts']}


def provider_metadata(compose):
    """Persist bounded diagnostics only, never request text, credentials or URLs."""
    raw = getattr(compose, 'last_metadata', {})
    if not isinstance(raw, dict):
        return None
    requests = []
    for item in raw.get('requests', [])[:2]:
        if not isinstance(item, dict):
            continue
        clean = {k: item[k] for k in ('stage','requestedModel','reportedModel','httpStatus','elapsedSeconds')
                 if k in item and isinstance(item[k], (str,int,float))}
        clean = {k: (v[:120] if isinstance(v,str) else v) for k,v in clean.items()}
        usage = item.get('usage', {})
        clean['usage'] = {k:usage[k] for k in ('prompt_tokens','completion_tokens','total_tokens')
                          if isinstance(usage,dict) and type(usage.get(k)) is int}
        requests.append(clean)
    return {'requests': requests} if requests else None
