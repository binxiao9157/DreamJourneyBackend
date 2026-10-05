"""New-protocol snapshot lane. Old Source workers cannot claim this job type.

A short transaction loads authoritative atoms; prepared model calls happen
outside transactions. Each exact call is reserved in the existing run budget
and its accepted response is durable, so lease recovery does not repeat it.
"""
from dataclasses import dataclass
from hashlib import sha256
from app.domain.owner_truth.live_topics import supported_atom_catalog,digest,LiveThemeConflict,SupportedLiveTheme
from app.services.owner_truth_live_theme_provider import DeepSeekLiveThemeProvider
from app.services.owner_truth_live_long_memory import LiveLongMemoryUnitPlan,LiveLongMemoryBudgetExhausted
from app.services.owner_truth_live_memory_contract_errors import contract_failure,LiveMemoryContractFailure

class RecoveryThemePageFailed(ValueError):
    """A durably exhausted model unit; no authority or code error is downgraded."""
    def __init__(self,code):
        self.code=code
        super().__init__(code)

def optional_relation_failure(error):
    # Malformed semantic output may decline a merge, but a failed immutable
    # input/target/member binding remains visible even after retry exhaustion.
    reason=str(error).rsplit('.',1)[-1]
    return reason not in {
        'themeRelationBindingMismatch','themeRelationTargetMismatch',
        'themeRelationMemberMismatch','themeRelationReviewBindingMismatch',
        'themeRequestChanged','themeResponseHashMismatch',
    }

@dataclass(frozen=True)
class RecoveryThemeAssembly:
    command: object
    themes: tuple
    atom_order: tuple
    manifest: dict
    catalog: tuple
    relations: dict

class RecoveryThemeAssembler:
    def __init__(self,host):
        self.host=host
        self.provider=DeepSeekLiveThemeProvider(host._settings)

    def _uow(self,label,lease):
        return self.host._unit_of_work(correlation_id='live-theme-'+label,
            command_id='liveTheme:'+lease.job_id+':'+label)

    def _admit(self,lease):
        store=self.host._store
        intent=store.async_effect_lease_repository().load_intent(lease)
        admission=store.owner_truth_source_target_admission_repository().admit_owner_truth_source(intent)
        if not admission.allowed:raise contract_failure('themeValidate','sourceAuthorityChanged',category='domain')
        return intent

    def _call(self,*,lease,run_id,revision,source_id,atoms,proposal=None,page_key="root",prepared=None,validator=None):
        stage,request=prepared or self.provider.prepare(atoms=atoms,proposal=proposal)
        validate=validator or (lambda payload:self.provider.validate_payload(atoms=atoms,proposal=proposal,payload=payload))
        request_hash=sha256(request.body).hexdigest()
        durable_stage=getattr(self,'stage_prefix','')+stage
        plan=LiveLongMemoryUnitPlan(run_id=run_id,ordinal=revision,kind=durable_stage,generation=1,
            ownership=({'sourceId':source_id,'inputHash':digest(atoms),'requestHash':request_hash,'pageKey':page_key},))
        with self._uow(stage+'Reserve',lease):
            self._admit(lease)
            repo=self.host._store.owner_truth_live_long_memory_repository()
            unit=repo.record_unit(plan)
            if unit['state']=='failed':raise RecoveryThemePageFailed(unit.get('failureCode') or 'themeUnitFailed')
            if unit['state']=='completed':
                stored=unit.get('coverage') or {}
                if stored.get('requestHash')!=request_hash:raise LiveThemeConflict('themeRequestChanged')
                payload=stored.get('response')
                if digest(payload)!=unit.get('outputHash'):raise LiveThemeConflict('themeResponseHashMismatch')
                validate(payload)
                return payload
            snap=repo.snapshot(run_id)
            attempts=[a for a in snap.get('attempts',[]) if a.get('unitId')==plan.unit_id]
            reservation=repo.reserve_provider_attempt(run_id=run_id,unit_id=plan.unit_id,stage=durable_stage,
                request_hash=request_hash,reserved_input_tokens=len(request.body),
                reserved_output_tokens=self.provider.maximum_output_tokens,recovery=bool(attempts))
        response=None
        try:
            response=self.provider.request_prepared(stage=stage,request=request)
            validate(response.payload)
        except Exception as error:
            # Only classified provider/contract failures can isolate a page.
            # Lost authority, DB faults and programming errors still stop the job.
            model_failure=isinstance(error,LiveMemoryContractFailure) and error.category in {'http','contract','transport'}
            can_retry=model_failure and (error.transport_retryable or error.contract_retry_eligible)
            terminal=model_failure and (len(attempts)>=1 or not can_retry
                or (stage=='themeRelation' and error.reason=='themeCorrectionKindMismatch')
                or (stage=='themeRelationScreen' and error.category=='contract'))
            with self._uow(stage+'Failure',lease):
                self._admit(lease)
                repo=self.host._store.owner_truth_live_long_memory_repository()
                repo.complete_provider_attempt(reservation,
                    exposure_state=('rejected' if getattr(error,'category',None) in {'http','contract'} else 'outcomeUnknown'),model=self.provider.model,
                    finish_reason=response.observation.get('_providerFinishReason') if response else None,
                    usage={'_diagnostic':getattr(error,'provider_observation',None) or {
                        'stage':getattr(error,'stage',stage),'reason':getattr(error,'reason','unclassified'),
                        'httpStatus':getattr(error,'provider_status',None),'requestHash':request_hash,
                        'validationVersion':'live-theme-contract-v2'}},
                    response_hash=digest(response.payload) if response else None)
                if model_failure:
                    repo.record_unit_failure(plan=plan,failure_code=error.code,terminal=terminal,
                        retry_seconds=error.retry_after_seconds or 0)
            if terminal:raise RecoveryThemePageFailed(error.code) from error
            raise
        with self._uow(stage+'Result',lease):
            self._admit(lease)
            repo=self.host._store.owner_truth_live_long_memory_repository()
            repo.complete_provider_attempt(reservation,exposure_state='responseAccepted',model=self.provider.model,
                finish_reason=response.observation.get('_providerFinishReason'),usage=response.observation.get('_providerUsage'),response_hash=digest(response.payload))
            repo.record_unit_result(plan=plan,atoms=[],output_hash=digest(response.payload),
                coverage={'requestHash':request_hash,'response':response.payload})
        return response.payload

    def _organize_page(self, *, lease,run_id,revision,source_id,page,key):
        proposal=self._call(lease=lease,run_id=run_id,revision=revision,source_id=source_id,
            atoms=page,page_key=key)
        review=self._call(lease=lease,run_id=run_id,revision=revision,source_id=source_id,
            atoms=page,proposal=proposal,page_key=key)
        return self.provider.validate(atoms=page,proposal=proposal,review=review)

    def _regroup_pages(self, *, repair, blocked):
        # At most two input partitions; preserve order and normal page limits.
        # Splitting only rejected groups keeps already verified themes intact
        # and does not turn every fact into its own user-visible card.
        second=set()
        for entry in blocked:
            if entry.get('groupingRejected') is not True:
                continue
            members=[a['atomId'] for a in repair if a['atomId'] in entry['atomIds']]
            if len(members)>1:
                second.update(members[(len(members)+1)//2:])
        partitions=([a for a in repair if a['atomId'] not in second],
                    [a for a in repair if a['atomId'] in second])
        return [page for part in partitions if part for page in self.provider.pages(part)]

    def organize_pages(self, *, lease,run_id,revision,source_id,catalog):
        if not catalog:return (),(),()
        leaves={a['atomId']:a for a in catalog}
        expansions={a['atomId']:(a['atomId'],) for a in catalog}
        units=list(catalog);omitted=set();blocked=[];final=[];parents={}
        context=dict(lease=lease,run_id=run_id,revision=revision,source_id=source_id)
        for level in range(8):
            pages=self.provider.pages(units);next_units=[];next_expansions={};next_parents={};final=[]
            kept_children=False
            for index,page in enumerate(pages):
                key=f'{level}:{index}';page_themes=[];page_omitted=set();page_blocked=[]
                try:
                    result=self._organize_page(**context,page=page,key=key)
                    page_themes.extend(result.themes);page_omitted.update(result.omitted_atom_ids)
                    page_blocked.extend(result.blocked)
                    if level==0 and page_omitted:
                        # One bounded regroup of rejected supported facts. This is
                        # a new exact request, not a retry of the same bad merge.
                        # Independent support/correction checks remain unchanged.
                        repair=[dict(a,groupingRepair='split-uncertain-events-v1') for a in page
                            if a['atomId'] in page_omitted and a['supportState']=='supported']
                        recovered=set()
                        repair_strategy=('regroup-split-v2' if any(b.get('groupingRejected') is True for b in page_blocked) else 'regroup')
                        for ri,repair_page in enumerate(self._regroup_pages(repair=repair,blocked=page_blocked)):
                            try:
                                fixed=self._organize_page(**context,page=repair_page,key=f'{key}:{repair_strategy}:{ri}')
                            except (RecoveryThemePageFailed,LiveLongMemoryBudgetExhausted):
                                continue
                            page_themes.extend(fixed.themes)
                            recovered.update(a for t in fixed.themes for a in t.atom_ids)
                        page_omitted-=recovered
                        page_blocked=[dict(b,atomIds=[a for a in b['atomIds'] if a not in recovered])
                            for b in page_blocked if set(b['atomIds'])-recovered]
                except LiveLongMemoryBudgetExhausted:
                    if level==0:raise
                    page_omitted={a['atomId'] for a in page}
                except RecoveryThemePageFailed as error:
                    page_omitted={a['atomId'] for a in page}
                    page_blocked=[dict(key=key,atomIds=sorted(page_omitted),
                        reason='modelUnitExhausted',failureCode=error.code)]
                if level>0 and page_omitted:
                    # A failed larger summary cannot invalidate already reviewed
                    # child themes. No old/history facts are imported here.
                    final.extend(parents[uid] for uid in sorted(page_omitted))
                    kept_children=True
                    page_omitted.clear();page_blocked=[]
                for atom_id in page_omitted:omitted.update(expansions[atom_id])
                for entry in page_blocked:
                    blocked.append({**entry,'atomIds':sorted({a for i in entry['atomIds'] for a in expansions[i]}),'page':key})
                for theme in page_themes:
                    ids=tuple(sorted({aid for i in theme.atom_ids for aid in expansions[i]}))
                    evidence=tuple(sorted({e for aid in ids for e in leaves[aid]['evidenceIds']}))
                    proof=digest([theme.support_hash,[u for u in page if u['atomId'] in theme.atom_ids]])
                    expanded=SupportedLiveTheme(digest([key,theme.key,ids]),theme.title,theme.summary,
                        ids,evidence,theme.dimensions,proof)
                    final.append(expanded)
                    uid='verified-theme:'+digest(expanded.payload())
                    content=dict(title=theme.title,summary=theme.summary)
                    next_units.append(dict(atomId=uid,content=content,contentHash=digest(content),
                        dimensions=list(theme.dimensions),supportState='supported',evidenceIds=[proof],
                        evidence=[dict(evidenceId=proof,kind='independentlyVerifiedSummary',text=theme.summary)]))
                    next_expansions[uid]=ids;next_parents[uid]=expanded
            if kept_children or len(pages)==1 or len(next_units)>=len(units):break
            units=next_units;expansions=next_expansions;parents=next_parents
            if not units:break
        if not final:raise contract_failure('themeValidate','noReliableThemes',category='domain')
        return tuple(final),tuple(sorted(omitted)),tuple(blocked)

    def defer_relation(self, *, theme, atoms, targets, reason):
        """Keep an independently verified current theme; do not assert 'none'.

        Optional association uncertainty does not invalidate current facts.
        The durable manifest distinguishes deferred from a verified no-relation
        decision. No old member, correction target, or new model text is used.
        """
        if (set(theme.atom_ids)!={a['atomId'] for a in atoms}
            or set(theme.evidence_ids)!={e for a in atoms for e in a['evidenceIds']}
            or any(a['supportState']!='supported' or a['contentHash']!=digest(a['content']) for a in atoms)):
            raise LiveThemeConflict('deferredRelationEvidenceMismatch')
        if not hasattr(self,'deferred_relations'):self.deferred_relations=[]
        entry=dict(key=theme.key,atomIds=list(theme.atom_ids),reason=reason,
            action='publishCurrentThemeWithoutHistoricalMutation',supportHash=theme.support_hash,
            targets=[dict(topicId=t['topicId'],version=t['version'],proposalHash=t['proposalHash']) for t in targets])
        entry['hash']=digest(entry);self.deferred_relations.append(entry)
        return (theme,),{},(),(),{}

    def guard_partial_themes(self, *,lease,run_id,revision,source_id,themes,turns):
        # Split long individual utterances without dropping text. Range offsets
        # remain diagnostic context, never newly invented atom evidence.
        context=[]
        for turn in turns:
            if turn['role']!='user':continue
            text=turn['text'];start=0
            while start<len(text):
                end=min(len(text),start+2000)
                context.append(dict(messageId=turn['messageId'],index=turn['index'],
                    start=start,end=end,text=text[start:end]))
                start=end
        # A page contains adjacent raw owner turns. Do not issue one model
        # request per utterance or give an isolated pronoun no surrounding text.
        # Each full chunk appears once, with one preceding chunk as overlap.
        import json
        pages=[];page=[]
        for item in context:
            if page and len(json.dumps(page+[item],ensure_ascii=False).encode())>12000:
                pages.append(page);page=[page[-1]]
            page.append(item)
        if page:pages.append(page)
        blocked=[];safe=[]
        for theme in themes:
            denied=False
            for index,items in enumerate(pages):
                stage,request,ih=self.provider.prepare_safety(themes=[theme],context=items)
                try:
                    payload=self._call(lease=lease,run_id=run_id,revision=revision,source_id=source_id,
                        atoms=[theme.payload(),items],page_key=f'safety:{theme.key}:{index}',prepared=(stage,request),
                        validator=lambda payload:self.provider.validate_safety(themes=[theme],input_hash=ih,payload=payload))
                    verdict=self.provider.validate_safety(themes=[theme],input_hash=ih,payload=payload)[theme.key]
                except RecoveryThemePageFailed as error:
                    blocked.append(dict(key=theme.key,atomIds=list(theme.atom_ids),reason='safetyUnitExhausted',failureCode=error.code))
                    denied=True;break
                if verdict!='safe':
                    blocked.append(dict(key=theme.key,atomIds=list(theme.atom_ids),reason='unresolvedCorrectionContext',
                        contextRanges=[dict(messageId=item['messageId'],start=item['start'],end=item['end']) for item in items]))
                    denied=True;break
            if not denied:safe.append(theme)
        return tuple(safe),tuple(blocked)

    def screen_relation_targets(self, *, lease, source, run_id, theme, atoms, targets):
        from app.async_effects.owner_truth_live_relation_paging import identity_context
        from app.services.owner_truth_live_topics import fact_fingerprint
        # Keep stable catalog order, and retain exact matches regardless of a
        # model's screening decision. The subsequent evidence checks still apply.
        material=dict(version='live-relation-screen-v1',current=identity_context(theme.payload()),
            targets=[dict(topicId=t['topicId'],version=t['version'],proposalHash=t['proposalHash'],
                state=t['state'],identity=identity_context(t['theme'])) for t in targets])
        try:
            payload=self._call(lease=lease,run_id=run_id,revision=source.source_metadata['snapshotRevision'],
                source_id=source.source_id,atoms=[material],page_key='relation-screen:'+digest(material),
                prepared=self.provider.prepare_relation_screen(material=material),
                validator=lambda p:self.provider.validate_relation_screen(material=material,payload=p))
        except RecoveryThemePageFailed as error:
            if not optional_relation_failure(error):raise
            self.defer_relation(theme=theme,atoms=atoms,targets=targets,reason=error.code)
            return []
        except LiveLongMemoryBudgetExhausted:
            self.defer_relation(theme=theme,atoms=atoms,targets=targets,reason='optionalRelationBudgetExhausted')
            return []
        except LiveMemoryContractFailure as error:
            if error.category=='input' and error.reason=='inputOverCapacity':
                reason='relationScreenOverCapacity'
            elif error.category=='contract' and error.reason.startswith('relationScreen'):
                # Reject the entire malformed retrieval answer, not the already
                # verified current facts. No selected target can escape this path.
                reason=error.code
            else:raise
            self.defer_relation(theme=theme,atoms=atoms,targets=targets,reason=reason)
            return []
        verdicts={t['topicId']:t['verdict'] for t in payload['targets']}
        ids={a['atomId'] for a in atoms};hashes={fact_fingerprint(a['content']) for a in atoms}
        exact={t['topicId'] for t in targets if any(a['atomId'] in ids or
            fact_fingerprint(a['content']) in hashes for a in t['atoms'])}
        selected=[t for t in targets if t['topicId'] in exact or verdicts[t['topicId']]!='unlikely']
        self.relation_screens.append(dict(themeKey=theme.key,inputHash=digest(material),responseHash=digest(payload),
            targets=payload['targets'],exactMatchTargetIds=sorted(exact),
            selectedTargetIds=[t['topicId'] for t in selected],
            excludedTargetIds=[t['topicId'] for t in targets if t not in selected],
            semantics='retrievalOnlyNotVerifiedNoRelation'))
        return selected

    def relate_themes(self,*,lease,intent,source,run_id,themes,catalog):
        from dataclasses import replace
        from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
        from app.services.owner_truth_live_topics import fact_fingerprint
        context=OwnerTruthCommandContext(vault_id=intent.target.vault_id,owner_subject_id=intent.target.owner_subject_id,
            actor_subject_id=intent.target.owner_subject_id)
        by_id={a['atomId']:a for a in catalog};accepted=[];relations={};blocked=[];unchanged=[];corrections={}
        self.deferred_relations=[]
        self.relation_screens=[]
        for theme in themes:
            atoms=[by_id[a] for a in theme.atom_ids]
            with self._uow('RelatedRead',lease):
                self._admit(lease)
                targets=self.host._store.owner_truth_live_topic_repository().relation_catalog(context=context,
                    atom_ids=theme.atom_ids,fact_hashes=[fact_fingerprint(a['content']) for a in atoms])
            if not targets:accepted.append(theme);continue
            if len(targets)>1:
                targets=self.screen_relation_targets(lease=lease,source=source,run_id=run_id,
                    theme=theme,atoms=atoms,targets=targets)
                if not targets:accepted.append(theme);continue
            from app.async_effects.owner_truth_live_relation_paging import needs_relation_paging, relate_paged_theme
            if needs_relation_paging(self.provider, theme, atoms, targets):
                result=relate_paged_theme(self,lease=lease,intent=intent,source=source,run_id=run_id,
                    theme=theme,atoms=atoms,targets=targets)
                accepted.extend(result[0]);relations.update(result[1]);blocked.extend(result[2])
                unchanged.extend(result[3]);corrections.update(result[4])
                continue
            # Split actual request size, then independently review each shortlist page.
            pages=[];page=[]
            for target in targets:
                item={k:v for k,v in target.items() if k!='revision'}
                material=dict(theme=theme.payload(),atoms=atoms,targets=page+[item])
                try:self.provider.prepare_relation(material=material)
                except LiveMemoryContractFailure as error:
                    if error.reason!='inputOverCapacity':raise
                    if page:pages.append(page)
                    page=[item]
                    self.provider.prepare_relation(material=dict(theme=theme.payload(),atoms=atoms,targets=page))
                else:page.append(item)
            if page:pages.append(page)
            matches=[];uncertain=False;uncertain_reason=None
            for index,items in enumerate(pages):
                material=dict(theme=theme.payload(),atoms=atoms,targets=items)
                try:
                    proposal=self._call(lease=lease,run_id=run_id,revision=source.source_metadata['snapshotRevision'],
                        source_id=source.source_id,atoms=[material],page_key=f'relation:{theme.key}:{index}',
                        prepared=self.provider.prepare_relation(material=material),
                        validator=lambda p:self.provider.validate_relation_payload(material=material,payload=p))
                    if proposal['relation']=='uncertain':
                        uncertain=True;break
                    # The verified current theme owns display text for a terminal
                    # target. Preserve raw relation output in its unit, then bind
                    # the exact proposal that the independent reviewer will see.
                    from app.domain.owner_truth.live_theme_relations import bind_terminal_relation_display
                    proposal=bind_terminal_relation_display(material=material,proposal=proposal)
                    review=self._call(lease=lease,run_id=run_id,revision=source.source_metadata['snapshotRevision'],
                        source_id=source.source_id,atoms=[material],page_key=f'relation:{theme.key}:{index}',
                        prepared=self.provider.prepare_relation(material=material,proposal=proposal),
                        validator=lambda p:self.provider.validate_relation_payload(material=material,proposal=proposal,payload=p))
                    validated=self.provider.validate_relation_payload(material=material,proposal=proposal,payload=review)
                except RecoveryThemePageFailed as error:
                    if not optional_relation_failure(error):raise
                    uncertain_reason=error.code;uncertain=True;break
                except LiveLongMemoryBudgetExhausted:
                    uncertain_reason='optionalRelationBudgetExhausted';uncertain=True;break
                except LiveMemoryContractFailure as error:
                    if error.category!='contract' or error.reason!='themeCorrectionKindMismatch':raise
                    uncertain_reason=error.code;uncertain=True;break
                if validated is None or validated['relation']=='uncertain':
                    uncertain=True;break
                elif validated['relation']!='none':matches.append(validated)
            if uncertain or len(matches)>1:
                self.defer_relation(theme=theme,atoms=atoms,targets=targets,
                    reason=uncertain_reason or ('themeRelationUnresolved' if uncertain else 'relationMultipleTargets'))
                accepted.append(theme);continue
            if not matches:accepted.append(theme);continue
            relation=matches[0];previous=next(t['revision'] for t in targets if t['topicId']==relation['targetTopicId'])
            prior_atoms={a['atomId']:a for t in targets if t['topicId']==previous['topicId'] for a in t['atoms']}
            new_ids=[a for a in theme.atom_ids if a not in relation['duplicateAtomIds']]
            if not new_ids:
                unchanged.append(dict(topicId=previous['topicId'],version=previous['version'],reason='sameValidatedFacts',atomIds=list(theme.atom_ids)))
                continue
            retained={}
            if previous['state']=='pending':
                retained={a:m for a,m in previous['members'].items() if a not in relation['replaces'].values() and a not in new_ids}
            elif previous['state']=='accepted' and relation['replaces']:
                with self._uow('FormalTargetRead',lease):
                    self._admit(lease)
                    targets_by_atom=self.host._store.owner_truth_live_topic_repository().formal_targets(context=context,
                        revision=previous,atom_ids=set(relation['replaces'].values()))
                corrections.update({new:targets_by_atom[old] for new,old in relation['replaces'].items()})
            combined={**{a:prior_atoms[a] for a in retained},**{a:by_id[a] for a in new_ids}}
            if any(a['supportState']!='supported' for a in combined.values()):
                blocked.append(dict(key=theme.key,atomIds=list(theme.atom_ids),reason='relatedThemeEvidenceChanged'));continue
            updated=replace(theme,title=relation['title'],summary=relation['summary'],atom_ids=tuple(combined),
                evidence_ids=tuple(sorted({e for a in combined.values() for e in a['evidenceIds']})),
                dimensions=tuple(sorted({d for a in combined.values() for d in a['dimensions']})),
                support_hash=digest([theme.support_hash,relation,review]))
            relations[theme.key]=dict(previous=previous,retained=retained,changeReason=relation['relation'],proof=digest([relation,review]))
            accepted.append(updated)
        return tuple(accepted),relations,tuple(blocked),tuple(unchanged),corrections

    def resolve_snapshot_atoms(self,*,lease,intent,source,run_id,atoms):
        """Reuse the production cross-batch fact resolver before theme grouping.

        Raw extraction atoms remain immutable. A changed fact gets a separately
        supported derived atom; its lineage retains every contributing raw ID.
        Later snapshots read raw extraction units, never recursively re-resolve
        their own previous derived results.
        """
        from app.services.owner_truth_live_long_memory import LiveLongMemoryAtomRecord
        from app.services.owner_truth_live_topics import fact_fingerprint
        extractor=self.host._live_preorganizer
        identity=extractor._run_identity(intent=intent,source=source)
        if identity.run_id!=run_id:raise LiveThemeConflict('recoveryRunIdentityMismatch')
        by_id={str(a['id']):a for a in atoms}
        memories=[{**a['memory'],'_atomIds':[str(a['id'])]} for a in sorted(atoms,
            key=lambda a:(min(a['memory']['sourceTurnIndices']),str(a['id'])))]
        memories,exact=extractor._merge_exact_memories(memories)
        memories,retracted,changed=extractor._resolve_cross_batch_relations(
            turns=source.source_metadata['conversationTurns'],memories=memories,
            run_identity=identity,retry_context=None)
        if exact or changed:
            memories=extractor._revalidate_resolved_memories(turns=source.source_metadata['conversationTurns'],
                memories=memories,run_identity=identity,retry_context=None,stage_reporter=None)
        resolved=[];lineage={}
        for memory in memories:
            ids=tuple(sorted(memory.get('_atomIds') or ()))
            if not ids or not set(ids)<=set(by_id):raise LiveThemeConflict('resolvedAtomLineageMismatch')
            original=by_id[ids[0]]
            if (len(ids)==1 and fact_fingerprint(memory)==fact_fingerprint(original['memory'])
                and memory['sourceTurnIndices']==original['memory']['sourceTurnIndices']
                and memory.get('_sourceEvidenceRanges')==original['memory'].get('_sourceEvidenceRanges')):
                resolved.append(original);lineage[str(original['id'])]=list(ids);continue
            plan=LiveLongMemoryUnitPlan(run_id=run_id,ordinal=min(memory['sourceTurnIndices']),
                kind='recoveryResolvedAtom',generation=1,ownership=({'rawAtomIds':list(ids),'memoryHash':digest(memory)},))
            atom=LiveLongMemoryAtomRecord.make(run_id=run_id,unit_id=plan.unit_id,memory=memory)
            with self._uow('ResolvedAtom',lease):
                self._admit(lease)
                repo=self.host._store.owner_truth_live_long_memory_repository()
                repo.record_unit(plan)
                repo.record_unit_result(plan=plan,atoms=[atom],output_hash=digest(memory),coverage={'rawAtomIds':list(ids)})
            resolved.append(dict(id=atom.atom_id,memory=dict(atom.memory),state='active'))
            lineage[atom.atom_id]=list(ids)
        if set(a for ids in lineage.values() for a in ids)|set(retracted)!=set(by_id):
            raise LiveThemeConflict('resolvedAtomCoverageMismatch')
        return resolved,dict(lineage=lineage,retractedAtomIds=sorted(retracted))

    def assemble(self,*,lease,intent,source):
        metadata=source.source_metadata or {}
        if metadata.get('origin')!='liveRecoverySnapshot':
            raise contract_failure('pipelineInput','recoverySourceRequired',category='domain')
        with self._uow('ReadAtoms',lease):
            self._admit(lease)
            repo=self.host._store.owner_truth_live_topic_repository()
            with repo._cursor() as cur:
                cur.execute("""SELECT run.id FROM owner_truth.live_memory_runs run
                    WHERE run.vault_id=%s AND run.owner_subject_id=%s AND run.authority_epoch=%s
                    AND run.product_session_id=%s AND run.capture_generation=%s""",
                    (intent.target.vault_id,intent.target.owner_subject_id,intent.target.authority_epoch,
                    metadata['productSessionId'],metadata['productCaptureGeneration']))
                row=cur.fetchone()
                if row is None:raise contract_failure('pipelineInput','draftRunUnavailable',category='domain')
                run_id=str(row['id'])
                cur.execute("""SELECT id,state,ownership,output_coverage,failure_code FROM owner_truth.live_memory_work_units
                    WHERE run_id=%s AND kind='atomExtraction' ORDER BY ordinal""",(run_id,))
                units=cur.fetchall()
                # No unsupported partial publication: failed-unit/correction isolation
                # will extend this fence, never turn an exception into noChange.
                if any(u['state'] in {'planned','running'} for u in units):
                    raise contract_failure('pipelineInput','draftUnitsUnresolved',category='domain')
                covered={int(i['index']) for u in units for i in u['ownership']}
                failed={int(i['index']) for u in units if u['state']=='failed' for i in u['ownership']}
                expected={int(t['index']) for t in metadata['conversationTurns'] if t['role']=='user'}
                if not expected<=covered:raise contract_failure('pipelineInput','draftCoverageMissing',category='domain')
                cur.execute("SELECT a.id,a.memory,a.state FROM owner_truth.live_memory_atoms a JOIN owner_truth.live_memory_work_units u ON u.id=a.unit_id WHERE a.run_id=%s AND u.kind='atomExtraction' ORDER BY a.id",(run_id,))
                atoms=[a for a in cur.fetchall() if set(a['memory'].get('sourceTurnIndices',[]))<=expected
                    and not set(a['memory'].get('sourceTurnIndices',[])) & failed]
        if not atoms and failed:raise contract_failure('pipelineInput','noReliableExtractedFacts',category='domain')
        relation_failure=[];resolution=dict(lineage={},retractedAtomIds=[])
        try:
            atoms,resolution=self.resolve_snapshot_atoms(lease=lease,intent=intent,source=source,run_id=run_id,atoms=atoms)
        except LiveMemoryContractFailure as error:
            if error.category not in {'http','transport','contract'}:raise
            # A failed semantic relation review cannot bless conflicting old
            # facts. The independent full-context safety pass below is required.
            relation_failure=[dict(reason='relationResolutionFailed',failureCode=error.code,
                atomIds=[str(a['id']) for a in atoms])]
        catalog=supported_atom_catalog(atoms=atoms,turns=metadata['conversationTurns'])
        # Private grouping is a bounded hint, never evidence. Reuse only drafts
        # whose entire membership survived cross-batch resolution unchanged.
        # Corrected/retracted/merged atoms cannot import an obsolete draft title.
        draft_refs=[];current_ids={a['atomId'] for a in catalog}
        with self._uow('ReadPrivateThemes',lease):
            self._admit(lease)
            with self.host._store.owner_truth_live_topic_repository()._cursor() as cur:
                cur.execute("SELECT id,output_hash,output_coverage FROM owner_truth.live_memory_work_units WHERE run_id=%s AND kind='privateThemeDraft' AND state='completed' ORDER BY ordinal,id",(run_id,))
                drafts=cur.fetchall()
        hints={}
        for draft in drafts:
            coverage=draft['output_coverage']
            if digest(coverage)!=draft['output_hash']:raise LiveThemeConflict('privateThemeDraftHashMismatch')
            draft_refs.append(dict(unitId=str(draft['id']),outputHash=draft['output_hash']))
            for theme in coverage.get('themes',[]):
                if set(theme['atomIds'])<=current_ids:
                    hint=dict(key=digest([str(draft['id']),theme['key']]),title=theme['title'])
                    for aid in theme['atomIds']:hints.setdefault(aid,[]).append(hint)
        for atom in catalog:
            if atom['atomId'] in hints:atom['privateDraftGroups']=hints[atom['atomId']]
        themes,omitted,blocked=self.organize_pages(lease=lease,run_id=run_id,
            revision=metadata['snapshotRevision'],source_id=source.source_id,catalog=catalog)
        # Relationship review quality is not a count of unpublished facts.
        # It still requires the same independent full-context safety gate.
        if (failed or omitted or blocked or relation_failure) and themes:
            # Include all received owner text, not just the failed page; this
            # preserves enough adjacent context for pronouns/corrections.
            themes,guard_blocked=self.guard_partial_themes(lease=lease,run_id=run_id,
                revision=metadata['snapshotRevision'],source_id=source.source_id,
                themes=themes,turns=metadata['conversationTurns'])
            omitted=tuple(sorted(set(omitted)|{a for b in guard_blocked for a in b['atomIds']}))
            blocked=tuple(blocked)+tuple(guard_blocked)
            for diagnostic in relation_failure:
                diagnostic['safetyReview']='blocked' if guard_blocked else 'passed'
            if not themes:raise contract_failure('themeValidate','noReliableThemes',category='domain')
        themes,relations,relation_blocked,unchanged,corrections=self.relate_themes(lease=lease,intent=intent,
            source=source,run_id=run_id,themes=themes,catalog=catalog)
        blocked=tuple(blocked)+relation_blocked
        omitted=tuple(sorted(set(omitted)|{a for b in relation_blocked for a in b['atomIds']}))
        if not themes and relation_blocked and not unchanged:raise contract_failure('themeValidate','noReliableThemes',category='domain')
        accepted={aid for t in themes for aid in t.atom_ids}
        retained_ids={a for relation in relations.values() for a in relation['retained']}
        selected=[a for a in atoms if str(a['id']) in accepted and str(a['id']) not in retained_ids]
        command=self.host._live_preorganizer.build_verified_proposals(intent=intent,source=source,
            turns=metadata['conversationTurns'],memories=[a['memory'] for a in selected])
        if corrections:
            from dataclasses import replace
            command=replace(command,proposals=tuple(replace(p,correction_of_memory_version_id=corrections.get(str(a['id'])))
                for a,p in zip(selected,command.proposals)))
        manifest=dict(relationDiagnostics=relation_failure,relationScreens=list(getattr(self,'relation_screens',[])),deferredRelations=list(getattr(self,'deferred_relations',[])),privateDraftRefs=draft_refs,factResolution=resolution,unchangedRelations=list(unchanged),schemaVersion='live-recovery-publication-v1',snapshotId=metadata['snapshotId'],
            snapshotHash=metadata['snapshotHash'],sourceId=source.source_id,sourceHash=source.source_content_hash,
            receivedRanges=metadata['receivedRanges'],missingRanges=metadata['missingRanges'],
            endPositionKnown=metadata['endPositionKnown'],omittedAtomIds=list(omitted),blockedThemes=list(blocked),
            atomIds=sorted(accepted),themeCount=len(themes),failedTurnIndices=sorted(failed & expected),
            completeness=('partial' if failed or omitted or blocked else metadata['completeness']))
        manifest['hash']=digest(manifest)
        return RecoveryThemeAssembly(command,themes,tuple(str(a['id']) for a in selected),manifest,tuple(catalog),relations)
