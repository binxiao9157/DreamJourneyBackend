"""PostgreSQL storage of reviewable theme versions, under the active Owner fence.

The caller persists V5 children, theme revision and publication manifest in
one UoW. No source text or model response is trusted as a Candidate binding.
"""
from app.domain.owner_truth.live_topics import LiveThemeConflict, digest, next_theme_revision, supported_atom_catalog

def fact_fingerprint(content):
    # Evidence location may move to a new immutable Source; typed fact meaning
    # must not change merely because its support was copied to a new revision.
    def semantic(value):
        if isinstance(value,dict):
            return {k:semantic(v) for k,v in value.items()
                if k not in {'sourceTurnIndices','evidenceRefs','sourceRefs'} and not k.startswith('_')}
        if isinstance(value,list):return [semantic(v) for v in value]
        return value
    return digest(semantic(content))

class PostgresLiveTopicRepository:
    def __init__(self,connection):self.connection=connection

    def _cursor(self):
        from psycopg.rows import dict_row
        return self.connection.cursor(row_factory=dict_row)

    def _authority(self,cur,context,epoch=None,lock=True):
        if context.actor_subject_id!=context.owner_subject_id:raise LiveThemeConflict('themeOwnerMismatch')
        cur.execute("SELECT authority_epoch FROM owner_truth.vaults WHERE vault_id=%s AND owner_subject_id=%s AND status='active'"+
            (' FOR UPDATE' if lock else ' FOR SHARE'),(context.vault_id,context.owner_subject_id))
        row=cur.fetchone()
        if row is None or (epoch is not None and int(row['authority_epoch'])!=epoch):
            raise LiveThemeConflict('themeOwnerMismatch')
        return int(row['authority_epoch'])

    def _read(self,cur,topic_id,context,epoch):
        from uuid import UUID
        try:topic_id=str(UUID(str(topic_id)))
        except (TypeError,ValueError):raise LiveThemeConflict('invalidThemeIdentity') from None
        cur.execute("""SELECT t.state,r.payload FROM owner_truth.live_memory_topics t
            JOIN owner_truth.live_memory_topic_revisions r ON r.topic_id=t.id AND r.version=t.current_version
            JOIN owner_truth.sources s ON s.id=r.source_id
            WHERE t.id=%s AND t.vault_id=%s AND t.owner_subject_id=%s AND t.authority_epoch=%s
              AND s.state='active' AND s.authority_epoch=t.authority_epoch AND s.owner_subject_id=t.owner_subject_id
            FOR UPDATE OF t""",(topic_id,context.vault_id,context.owner_subject_id,epoch))
        row=cur.fetchone()
        if row is None:raise LiveThemeConflict('themeUnavailable')
        return {**row['payload'],'state':row['state']}

    def list_pending(self,*,context,limit=50,after_id=None):
        if type(limit) is not int or not 1<=limit<=100:raise LiveThemeConflict('invalidThemePage')
        if after_id is not None:
            from uuid import UUID
            try:after_id=str(UUID(after_id))
            except (ValueError,TypeError):raise LiveThemeConflict('invalidThemePage') from None
        with self._cursor() as cur:
            epoch=self._authority(cur,context,lock=False)
            cur.execute("""SELECT r.payload FROM owner_truth.live_memory_topics t
                JOIN owner_truth.live_memory_topic_revisions r ON r.topic_id=t.id AND r.version=t.current_version
                JOIN owner_truth.sources s ON s.id=r.source_id
                WHERE t.vault_id=%s AND t.owner_subject_id=%s AND t.authority_epoch=%s AND t.state='pending'
                  AND s.state='active' AND s.authority_epoch=t.authority_epoch AND s.owner_subject_id=t.owner_subject_id
                  AND (%s::uuid IS NULL OR t.id>%s::uuid)
                ORDER BY t.id LIMIT %s""",(context.vault_id,context.owner_subject_id,epoch,after_id,after_id,limit+1))
            rows=cur.fetchall();page=[r['payload'] for r in rows[:limit]]
            for payload in page:self._member_details(cur,payload)
            return {'themes':page,'nextCursor':page[-1]['topicId'] if len(rows)>limit else None}

    def _member_details(self,cur,payload):
        ids=[v['candidateId'] for v in payload['members'].values()]
        cur.execute("SELECT id,payload FROM owner_truth.memory_candidates WHERE id=ANY(%s::uuid[])",(ids,))
        candidates={str(row['id']):row['payload'] for row in cur.fetchall()}
        details={}
        for atom,binding in payload['members'].items():
            candidate=candidates.get(binding['candidateId'])
            if candidate is None or candidate.get('proposalHash')!=binding['proposalHash']:
                raise LiveThemeConflict('themeCandidateBindingMismatch')
            content=candidate['content'];kind=candidate['candidateKind']
            primary={'experience':'summary','knowledge':'claim','emotion':'label'}.get(kind)
            text=content.get('statement') or content.get(primary or '')
            if not isinstance(text,str) or not text.strip():raise LiveThemeConflict('themeMemberTextUnavailable')
            details[atom]=dict(candidateId=binding['candidateId'],statement=text,kind=kind)
        payload['memberDetails']=details

    def related_by_evidence(self, *, context, atom_ids):
        """A shared immutable atom is an exact relation, never a keyword guess."""
        with self._cursor() as cur:
            epoch=self._authority(cur,context)
            cur.execute("""SELECT DISTINCT t.id FROM owner_truth.live_memory_topics t
                JOIN owner_truth.live_memory_topic_members m ON m.topic_id=t.id AND m.version=t.current_version
                WHERE t.vault_id=%s AND t.owner_subject_id=%s AND t.authority_epoch=%s
                    AND m.atom_id=ANY(%s) ORDER BY t.id""",
                (context.vault_id,context.owner_subject_id,epoch,list(atom_ids)))
            ids=[str(row['id']) for row in cur.fetchall()]
            if len(ids)>1:raise LiveThemeConflict('themeMergeRequiresExplicitRelation')
            return self._read(cur,ids[0],context,epoch) if ids else None

    def relation_catalog(self,*,context,atom_ids,fact_hashes,limit=8):
        """Owner-scoped bounded retrieval. Exact overlap first, then recent topics.

        Retrieval is a shortlist, never itself a semantic merge decision.
        """
        with self._cursor() as cur:
            epoch=self._authority(cur,context)
            cur.execute("""SELECT t.id FROM owner_truth.live_memory_topics t
                JOIN owner_truth.live_memory_topic_revisions r ON r.topic_id=t.id AND r.version=t.current_version
                JOIN owner_truth.sources s ON s.id=r.source_id
                WHERE t.vault_id=%s AND t.owner_subject_id=%s AND t.authority_epoch=%s
                  AND s.state='active' AND s.authority_epoch=t.authority_epoch AND s.owner_subject_id=t.owner_subject_id
                ORDER BY ((r.payload->'factHashes') ?| %s) DESC,
                  EXISTS(SELECT 1 FROM owner_truth.live_memory_topic_members m WHERE m.topic_id=t.id
                    AND m.version=t.current_version AND m.atom_id=ANY(%s)) DESC,t.created_at DESC,t.id LIMIT %s""",
                (context.vault_id,context.owner_subject_id,epoch,list(fact_hashes),list(atom_ids),limit))
            result=[]
            for topic_id in [str(r['id']) for r in cur.fetchall()]:
                payload=self._read(cur,topic_id,context,epoch)
                catalog=self._catalog_for_revision(cur,payload,context,epoch)
                result.append(dict(topicId=topic_id,version=payload['version'],proposalHash=payload['proposalHash'],
                    state=payload['state'],theme=payload['theme'],atoms=catalog,revision=payload))
            return result

    def _catalog_for_revision(self,cur,payload,context,epoch):
        catalogs=[]
        for aid,binding in payload['members'].items():
            cur.execute("""SELECT a.id,a.memory,a.source_turn_indices,a.state,s.metadata
                FROM owner_truth.live_memory_atoms a JOIN owner_truth.live_memory_runs run ON run.id=a.run_id
                JOIN owner_truth.sources s ON s.id=%s
                JOIN owner_truth.memory_candidates c ON c.id=%s AND c.source_id=s.id
                WHERE a.id=%s AND run.vault_id=%s AND run.owner_subject_id=%s AND run.authority_epoch=%s
                  AND s.vault_id=run.vault_id AND s.owner_subject_id=run.owner_subject_id AND s.authority_epoch=run.authority_epoch
                  AND s.state='active' AND c.payload->>'proposalHash'=%s""",
                (binding['sourceId'],binding['candidateId'],aid,context.vault_id,context.owner_subject_id,epoch,binding['proposalHash']))
            row=cur.fetchone()
            if row is None:raise LiveThemeConflict('relatedThemeEvidenceUnavailable')
            catalogs.extend(supported_atom_catalog(atoms=[row],turns=row['metadata'].get('conversationTurns',[])))
        return catalogs

    def formal_targets(self,*,context,revision,atom_ids):
        with self._cursor() as cur:
            epoch=self._authority(cur,context)
            current=self._read(cur,revision['topicId'],context,epoch)
            if current['version']!=revision['version'] or current['proposalHash']!=revision['proposalHash'] or current['state']!='accepted':
                raise LiveThemeConflict('themeVersionChanged')
            candidates={revision['members'][a]['candidateId']:a for a in atom_ids}
            cur.execute("""SELECT result_payload FROM owner_truth.memory_changeset_group_receipts WHERE vault_id=%s AND owner_subject_id=%s
                AND EXISTS(SELECT 1 FROM jsonb_array_elements(result_payload->'members') m WHERE m->>'candidateId'=ANY(%s))""",
                (context.vault_id,context.owner_subject_id,list(candidates)))
            found={}
            for row in cur.fetchall():
                for member in row['result_payload'].get('members',[]):
                    aid=candidates.get(member.get('candidateId'))
                    if aid and member.get('memoryVersionId'):found[aid]=member['memoryVersionId']
            if set(found)!=set(atom_ids):raise LiveThemeConflict('themeFormalTargetUnavailable')
            for version in found.values():
                cur.execute("""SELECT v.id FROM owner_truth.memory_versions v JOIN owner_truth.memories m ON m.id=v.memory_id
                    WHERE v.id=%s AND v.is_current AND m.vault_id=%s AND m.owner_subject_id=%s AND m.authority_epoch=%s""",
                    (version,context.vault_id,context.owner_subject_id,epoch))
                if cur.fetchone() is None:raise LiveThemeConflict('themeFormalTargetChanged')
            return found

    def publish(self,*,context,authority_epoch,theme,stable_key,snapshot_id,source_id,
                atom_candidate_ids,expected_version=0,previous_topic_id=None,change_reason='new',retained_members=None,expected_previous_hash=None,relation_proof=None):
        from psycopg.types.json import Jsonb
        from uuid import UUID
        with self._cursor() as cur:
            self._authority(cur,context,authority_epoch)
            cur.execute("""SELECT s.metadata FROM owner_truth.live_recovery_snapshots p
                JOIN owner_truth.live_recovery_sessions r ON r.session_id=p.session_id
                JOIN owner_truth.sources s ON s.id=p.source_id
                WHERE p.id=%s AND p.source_id=%s AND r.vault_id=%s AND r.owner_subject_id=%s
                  AND r.authority_epoch=%s AND r.publication_authorization IS NOT NULL
                  AND s.state='active' AND s.authority_epoch=r.authority_epoch AND s.owner_subject_id=r.owner_subject_id
                FOR SHARE OF p""",(snapshot_id,source_id,context.vault_id,context.owner_subject_id,authority_epoch))
            source_row=cur.fetchone()
            if source_row is None:raise LiveThemeConflict('themeSourceMismatch')
            retained_members=retained_members or {}
            previous=self._read(cur,previous_topic_id,context,authority_epoch) if previous_topic_id else None
            if previous is not None and expected_previous_hash is not None and previous['proposalHash']!=expected_previous_hash:
                raise LiveThemeConflict('themeVersionChanged')
            if retained_members and (previous is None or previous['state']!='pending'
                or any(previous['members'].get(a)!=m for a,m in retained_members.items())):
                raise LiveThemeConflict('invalidRetainedThemeMembers')
            if set(atom_candidate_ids)&set(retained_members) or set(atom_candidate_ids)|set(retained_members)!=set(theme.atom_ids):
                raise LiveThemeConflict('themeCandidateMembershipMismatch')
            ids=[str(UUID(i)) for i in atom_candidate_ids.values()]
            if len(ids)!=len(set(ids)):raise LiveThemeConflict('themeCandidateMembershipMismatch')
            cur.execute("""SELECT id,source_id,payload FROM owner_truth.memory_candidates
                WHERE id=ANY(%s::uuid[]) AND source_id=%s AND vault_id=%s AND owner_subject_id=%s
                  AND authority_epoch=%s AND decision_status='pending' FOR SHARE""",
                (ids,source_id,context.vault_id,context.owner_subject_id,authority_epoch))
            candidates={str(row['id']):row for row in cur.fetchall()}
            if set(candidates)!=set(ids):raise LiveThemeConflict('themeCandidateBindingMismatch')
            cur.execute("""SELECT a.id,a.memory,a.source_turn_indices,a.state FROM owner_truth.live_memory_atoms a
                JOIN owner_truth.live_memory_runs run ON run.id=a.run_id
                JOIN owner_truth.interview_sessions i ON i.product_session_id=run.product_session_id
                    AND i.owner_subject_id=run.owner_subject_id AND i.vault_id=run.vault_id
                JOIN owner_truth.live_recovery_snapshots p ON p.session_id=i.id
                JOIN owner_truth.live_recovery_sessions r ON r.session_id=i.id
                WHERE a.id=ANY(%s::uuid[]) AND p.id=%s AND run.owner_subject_id=%s AND run.vault_id=%s
                    AND run.authority_epoch=%s AND run.capture_generation=(r.coordinates->>'generation')::integer
                FOR SHARE OF a""",(list(atom_candidate_ids),snapshot_id,context.owner_subject_id,context.vault_id,authority_epoch))
            atoms={str(row['id']):row for row in cur.fetchall()}
            if set(atoms)!=set(atom_candidate_ids):raise LiveThemeConflict('themeAtomIdentityMismatch')
            catalog=supported_atom_catalog(atoms=list(atoms.values()),turns=source_row['metadata'].get('conversationTurns',[]))
            retained_catalog=[]
            if retained_members:
                retained_catalog=[a for a in self._catalog_for_revision(cur,previous,context,authority_epoch) if a['atomId'] in retained_members]
                if any(a['supportState']!='supported' for a in retained_catalog):raise LiveThemeConflict('relatedThemeEvidenceChanged')
            expected_evidence={e for atom in catalog+retained_catalog for e in atom['evidenceIds']}
            if set(theme.evidence_ids)!=expected_evidence:
                raise LiveThemeConflict('themeEvidenceMismatch')
            members=dict(retained_members)
            for atom_id,candidate_id in atom_candidate_ids.items():
                candidate=candidates[candidate_id];payload=candidate['payload']
                atom=atoms[atom_id];memory=atom['memory'];content=payload['content']
                primary={'experience':'summary','knowledge':'claim','emotion':'label'}.get(memory.get('memoryKind'))
                if (atom['state']!='active' or primary is None or content.get(primary)!=memory.get(primary)
                    or content.get('sourceTurnIndices')!=atom['source_turn_indices']):
                    raise LiveThemeConflict('themeAtomCandidateMismatch')
                members[atom_id]=dict(candidateId=candidate_id,sourceId=source_id,
                    proposalHash=payload['proposalHash'],factHash=fact_fingerprint(payload['content']))
            result=next_theme_revision(previous=previous,theme=theme,owner=context.owner_subject_id,vault=context.vault_id,
                epoch=authority_epoch,stable_key=stable_key,source_id=source_id,snapshot_id=snapshot_id,
                expected_version=expected_version,member_bindings=members,change_reason=change_reason,relation_proof=relation_proof)
            if result.get('status')=='noChange':return result
            # Replay is resolved by exact immutable hash, never by replacing data.
            cur.execute('SELECT proposal_hash,payload FROM owner_truth.live_memory_topic_revisions WHERE topic_id=%s AND version=%s',
                (result['topicId'],result['version']))
            old=cur.fetchone()
            if old is not None:
                if old['proposal_hash']!=result['proposalHash']:raise LiveThemeConflict('immutableThemeRevision')
                return old['payload']
            cur.execute("""INSERT INTO owner_truth.live_memory_topics(id,vault_id,owner_subject_id,authority_epoch,
                current_version,state,linked_topic_id) VALUES(%s,%s,%s,%s,%s,'pending',%s)
                ON CONFLICT(id) DO NOTHING""",(result['topicId'],context.vault_id,context.owner_subject_id,
                authority_epoch,result['version'],result['linkedTopicId']))
            cur.execute("""INSERT INTO owner_truth.live_memory_topic_revisions(topic_id,version,snapshot_id,source_id,proposal_hash,payload)
                VALUES(%s,%s,%s,%s,%s,%s)""",(result['topicId'],result['version'],snapshot_id,source_id,result['proposalHash'],Jsonb(result)))
            for atom_id,binding in members.items():
                cur.execute("""INSERT INTO owner_truth.live_memory_topic_members(topic_id,version,atom_id,candidate_id,candidate_proposal_hash)
                    VALUES(%s,%s,%s,%s,%s)""",(result['topicId'],result['version'],atom_id,binding['candidateId'],binding['proposalHash']))
            cur.execute("""UPDATE owner_truth.live_memory_topics SET current_version=%s
                WHERE id=%s AND state='pending' AND current_version IN (%s,%s)""",
                (result['version'],result['topicId'],max(1,result['version']-1),result['version']))
            if cur.rowcount!=1:raise LiveThemeConflict('themeVersionChanged')
            return result

    def lock_visible_revision(self,*,context,topic_id,expected_version,expected_hash,allow_terminal=False):
        with self._cursor() as cur:
            epoch=self._authority(cur,context)
            current=self._read(cur,topic_id,context,epoch)
            if ((not allow_terminal and current['state']!='pending') or type(expected_version) is not int or current['version']!=expected_version
                or current['proposalHash']!=expected_hash):raise LiveThemeConflict('themeVersionChanged')
            # Revalidate every constituent source and candidate under the same
            # transaction as group preview/confirmation, including retained history.
            catalog=self._catalog_for_revision(cur,current,context,epoch)
            if not allow_terminal and any(atom['supportState']!='supported' for atom in catalog):
                raise LiveThemeConflict('themeEvidenceNoLongerSupported')
            for item in current['members'].values():
                cur.execute("SELECT decision_status,payload FROM owner_truth.memory_candidates WHERE id=%s",(item['candidateId'],))
                candidate=cur.fetchone()
                if not allow_terminal and candidate['decision_status']!='pending':raise LiveThemeConflict('themeMemberChanged')
                target=candidate['payload'].get('correctionOfMemoryVersionId')
                if target and not allow_terminal:
                    cur.execute('''SELECT v.id FROM owner_truth.memory_versions v JOIN owner_truth.memories m ON m.id=v.memory_id
                        WHERE v.id=%s AND v.is_current AND m.vault_id=%s AND m.owner_subject_id=%s AND m.authority_epoch=%s''',
                        (target,context.vault_id,context.owner_subject_id,epoch))
                    if cur.fetchone() is None:raise LiveThemeConflict('themeFormalTargetChanged')
            return current

    def prepare_primary_edits(self, *, revision, edits):
        """Read bound Candidate kinds inside the caller's visible-revision UoW.

        Do not require pending here: confirmation receipt replay must keep the
        same command after members become terminal. Review still owns CAS.
        """
        from app.domain.owner_truth.contracts import MemoryKind
        from app.domain.owner_truth.ontology import primary_memory_edit_payload
        if not edits:
            return {}
        if not set(edits) <= set(revision['members']):
            raise LiveThemeConflict('invalidThemeEdits')
        ids = [revision['members'][atom]['candidateId'] for atom in edits]
        with self._cursor() as cur:
            cur.execute("SELECT id,payload FROM owner_truth.memory_candidates WHERE id=ANY(%s::uuid[])", (ids,))
            candidates = {str(row['id']): row['payload'] for row in cur.fetchall()}
        result = {}
        for atom, text in edits.items():
            binding = revision['members'][atom]
            candidate = candidates.get(binding['candidateId'])
            if candidate is None or candidate.get('proposalHash') != binding['proposalHash']:
                raise LiveThemeConflict('themeCandidateBindingMismatch')
            try:
                kind = MemoryKind(candidate['candidateKind'])
            except (KeyError, TypeError, ValueError):
                raise LiveThemeConflict('themeCandidateKindInvalid') from None
            result[atom] = primary_memory_edit_payload(
                kind=kind, source_payload=candidate['content'], text=text,
            )
        return result

    def record_decision(self,*,context,topic_id,expected_version,expected_hash,state):
        if state not in {'accepted','rejected'}:raise LiveThemeConflict('invalidThemeDecision')
        current=self.lock_visible_revision(context=context,topic_id=topic_id,
            expected_version=expected_version,expected_hash=expected_hash,allow_terminal=True)
        if current['state']!='pending':raise LiveThemeConflict('themeVersionChanged')
        with self._cursor() as cur:
            # A theme cannot claim confirmation until all its V5 children have
            # actually committed that decision in this same transaction.
            ids=[v['candidateId'] for v in current['members'].values()]
            cur.execute('SELECT decision_status FROM owner_truth.memory_candidates WHERE id=ANY(%s::uuid[])',(ids,))
            statuses=[r['decision_status'] for r in cur.fetchall()]
            if len(statuses)!=len(ids) or any(status not in ({'accepted','corrected'} if state=='accepted' else {'rejected'}) for status in statuses):
                raise LiveThemeConflict('themeMembersNotDecided')
            cur.execute('UPDATE owner_truth.live_memory_topics SET state=%s WHERE id=%s',(state,topic_id))
