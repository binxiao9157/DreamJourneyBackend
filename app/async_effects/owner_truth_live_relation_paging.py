"""Bounded relation pages for large themes; pages never become user cards.

Only supported, complete page evidence can be reduced into a relation. Unknown
pages defer the optional association while preserving the verified current theme. Authority/DB/budget faults are
not downgraded. All model calls use the existing persisted Worker budget.
"""
from dataclasses import replace
from app.domain.owner_truth.live_topics import SupportedLiveTheme, LiveThemeConflict, digest
from app.domain.owner_truth.live_theme_relations import bind_terminal_relation_display, validate_relation
from app.services.owner_truth_live_memory_contract_errors import LiveMemoryContractFailure
from app.services.owner_truth_live_long_memory import LiveLongMemoryBudgetExhausted

PAGE_VERSION = 'live-theme-relation-pages-v2'
PAGE_ATOMS = 32


class RelationPageUnresolved(ValueError):
    pass


def needs_relation_paging(provider, theme, atoms, targets):
    if len(atoms) > 64:
        return True
    for target in targets:
        material = dict(theme=theme.payload(), atoms=atoms,
                        targets=[{k: v for k, v in target.items() if k != 'revision'}])
        try:
            _, request = provider.prepare_relation(material=material)
        except LiveMemoryContractFailure as error:
            if error.reason != 'inputOverCapacity':
                raise
            return True
        # Reserve room for the independently reviewed proposal and summaryScope.
        if len(request.body) > provider.maximum_input_bytes // 2:
            return True
    return False


def atom_statement(atom):
    content = atom['content']
    primary = {'experience': 'summary', 'knowledge': 'claim', 'emotion': 'label'}.get(content.get('memoryKind'))
    text = content.get('statement') or content.get(primary or '')
    if not isinstance(text, str) or not text.strip():
        raise LiveThemeConflict('themeMemberTextUnavailable')
    return text


def scoped_theme(parent, atoms):
    """A page contains supported statements, never an unsourced whole-theme summary."""
    ids = [a['atomId'] for a in atoms]
    text = '\n'.join(atom_statement(a) for a in atoms)
    if len(text) > 4000:
        raise RelationPageUnresolved('relationPageSummaryOverCapacity')
    return dict(key=digest([PAGE_VERSION, parent['key'], ids]),
                title=parent['title'], summary=text, atomIds=ids,
                evidenceIds=sorted({e for a in atoms for e in a['evidenceIds']}),
                dimensions=sorted({d for a in atoms for d in a['dimensions']}),
                supportHash=digest([parent.get('supportHash'), atoms]))


def identity_context(parent):
    # Only an already supported theme can supply read-only global context.
    # It cannot expand page-local correction/duplicate membership.
    if not all(parent.get(k) for k in ('title', 'summary', 'supportHash', 'atomIds')):
        raise LiveThemeConflict('relationIdentityContextMissing')
    return dict(title=parent['title'], summary=parent['summary'],
                supportHash=parent['supportHash'], membershipHash=digest(parent['atomIds']))


def relation_material(theme, atoms, target, prior):
    old = {k: v for k, v in target.items() if k != 'revision'}
    parent = target.get('theme') or dict(key=target['topicId'], title='既有主题', supportHash=target['proposalHash'])
    old.update(atoms=prior, theme=scoped_theme(parent, prior))
    return dict(theme=scoped_theme(theme.payload(), atoms), atoms=atoms, targets=[old],
                relationContext=dict(current=identity_context(theme.payload()), prior=identity_context(target['theme'])),
                relationPage=dict(version=PAGE_VERSION, themeKey=theme.key,
                    themeMembershipHash=digest(list(theme.atom_ids)),
                    targetMembershipHash=digest([a['atomId'] for a in target['atoms']]),
                    newAtomIds=[a['atomId'] for a in atoms], oldAtomIds=[a['atomId'] for a in prior]))


def relation_pages(provider, theme, atoms, targets):
    """Exact new x old coverage, split by count and encoded size, never truncate."""
    pages = []
    def add(new, target, old):
        try:
            material = relation_material(theme, new, target, old)
            _, request = provider.prepare_relation(material=material)
            fits = len(request.body) <= provider.maximum_input_bytes // 2
        except RelationPageUnresolved:
            fits = False
        except LiveMemoryContractFailure as error:
            if error.reason != 'inputOverCapacity':
                raise
            fits = False
        if fits:
            pages.append(material)
            return
        # Both dimensions may be oversized. Keep splitting immutable atom groups.
        if len(new) > 1 and (len(new) >= len(old) or len(old) == 1):
            mid = len(new) // 2
            add(new[:mid], target, old); add(new[mid:], target, old)
        elif len(old) > 1:
            mid = len(old) // 2
            add(new, target, old[:mid]); add(new, target, old[mid:])
        else:
            raise RelationPageUnresolved('relationSingleEvidenceOverCapacity')
    for target in targets:
        if not target['atoms']:
            raise LiveThemeConflict('relationTargetEvidenceMissing')
        for i in range(0, len(atoms), PAGE_ATOMS):
            for j in range(0, len(target['atoms']), PAGE_ATOMS):
                add(atoms[i:i + PAGE_ATOMS], target, target['atoms'][j:j + PAGE_ATOMS])
    return tuple(pages)


def _complete_summary(assembler, call_context, theme, combined):
    themes, omitted, blocked = assembler.organize_pages(catalog=list(combined.values()), **call_context)
    if (omitted or blocked or len(themes) != 1
        or set(themes[0].atom_ids) != set(combined)
        or set(themes[0].evidence_ids) != {e for a in combined.values() for e in a['evidenceIds']}):
        raise RelationPageUnresolved('relationMergedSummaryIncomplete')
    verified = themes[0]
    return replace(verified, key=theme.key)


def _reduce(assembler, *, lease, intent, source, run_id, theme, atoms, targets, pages, checked):
    positives = [(m, p, r) for m, p, r in checked if p['relation'] != 'none']
    if not positives:
        # Every page was checked; no page may represent evidence outside itself.
        return (theme,), {}, (), (), {}
    target_ids = {p['targetTopicId'] for _, p, _ in positives}
    if len(target_ids) != 1:
        raise RelationPageUnresolved('relationMultipleTargets')
    target_id = next(iter(target_ids))
    target = next(t for t in targets if t['topicId'] == target_id)
    all_ids = {a['atomId'] for a in atoms}
    related_ids = {a['atomId'] for m, _, _ in positives for a in m['atoms']}
    if related_ids != all_ids:
        raise RelationPageUnresolved('relationMixedIndependentPages')
    duplicates, replacements, intents = set(), {}, {}
    for _, proposal, _ in positives:
        duplicates.update(proposal['duplicateAtomIds'])
        for new, old in proposal['replaces'].items():
            if new in replacements and replacements[new] != old:
                raise RelationPageUnresolved('relationConflictingReplacement')
            replacements[new] = old
            proof = proposal['correctionEvidence'][new]
            # Identical mapping may have a different valid quoted subrange.
            intents.setdefault(new, proof)
    if len(set(replacements.values())) != len(replacements) or duplicates & set(replacements):
        raise RelationPageUnresolved('relationConflictingReplacement')
    # A duplicate of a removed assertion would silently reintroduce stale content.
    from app.services.owner_truth_live_topics import fact_fingerprint
    by_id = {a['atomId']: a for a in atoms}
    prior = {a['atomId']: a for a in target['atoms']}
    removed_hashes = {fact_fingerprint(prior[i]['content']) for i in replacements.values()}
    if any(fact_fingerprint(by_id[i]['content']) in removed_hashes for i in duplicates):
        raise RelationPageUnresolved('relationDuplicateOfReplacedFact')
    new_ids = [i for i in theme.atom_ids if i not in duplicates]
    previous = target['revision']
    proof = digest(dict(version=PAGE_VERSION, pages=pages, checked=checked))
    if not new_ids:
        return (), {}, (), (dict(topicId=target_id, version=previous['version'],
            reason='sameValidatedFacts', atomIds=list(theme.atom_ids), relationProof=proof),), {}
    retained = {}
    if previous['state'] == 'pending':
        retained = {i: m for i, m in previous['members'].items()
                    if i not in replacements.values() and i not in new_ids}
    combined = {**{i: prior[i] for i in retained}, **{i: by_id[i] for i in new_ids}}
    if any(a['supportState'] != 'supported' for a in combined.values()):
        raise RelationPageUnresolved('relatedThemeEvidenceChanged')
    context = dict(lease=lease, run_id=run_id, revision=source.source_metadata['snapshotRevision'], source_id=source.source_id)
    # No page-local summary can describe the final union, especially corrections.
    if retained or duplicates:
        updated = _complete_summary(assembler, context, theme, combined)
    else:
        updated = replace(theme, atom_ids=tuple(combined),
            evidence_ids=tuple(sorted({e for a in combined.values() for e in a['evidenceIds']})))
    kind = 'correction' if replacements else 'supplement'
    material = dict(theme=theme.payload(), atoms=atoms,
                    targets=[{k: v for k, v in target.items() if k != 'revision'}])
    aggregate = dict(schemaVersion='owner-truth-live-theme-relation-v1', inputHash=digest(material),
        targetTopicId=target_id, targetVersion=target['version'], targetHash=target['proposalHash'],
        relation=kind, duplicateAtomIds=sorted(duplicates), replaces=replacements, correctionEvidence=intents,
        title=updated.title, summary=updated.summary)
    # Pure domain validation has no provider page cap and never substitutes for
    # independent per-page semantic review or final supported summary generation.
    validate_relation(material=material, proposal=aggregate)
    corrections = {}
    if previous['state'] == 'accepted' and replacements:
        from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
        context = OwnerTruthCommandContext(vault_id=intent.target.vault_id,
            owner_subject_id=intent.target.owner_subject_id, actor_subject_id=intent.target.owner_subject_id)
        with assembler._uow('FormalTargetRead', lease):
            assembler._admit(lease)
            binding = assembler.host._store.owner_truth_live_topic_repository().formal_targets(
                context=context, revision=previous, atom_ids=set(replacements.values()))
        corrections = {new: binding[old] for new, old in replacements.items()}
    updated = replace(updated, support_hash=digest([updated.support_hash, proof, aggregate]))
    relation = dict(previous=previous, retained=retained, changeReason=kind,
                    proof=digest([proof, aggregate, updated.support_hash]))
    return (updated,), {theme.key: relation}, (), (), corrections


def relate_paged_theme(assembler, *, lease, intent, source, run_id, theme, atoms, targets):
    from app.async_effects.owner_truth_live_recovery_worker import RecoveryThemePageFailed, optional_relation_failure
    try:
        pages = relation_pages(assembler.provider, theme, atoms, targets)
        checked = []
        for material in pages:
            key = 'relation-pages:' + PAGE_VERSION + ':' + digest(material)
            context = dict(lease=lease, run_id=run_id, revision=source.source_metadata['snapshotRevision'],
                           source_id=source.source_id, atoms=[material], page_key=key)
            proposal = assembler._call(**context, prepared=assembler.provider.prepare_relation(material=material),
                validator=lambda p: assembler.provider.validate_relation_payload(material=material, payload=p))
            if proposal['relation']=='uncertain':
                raise RelationPageUnresolved('themeRelationUnresolved')
            proposal = bind_terminal_relation_display(material=material, proposal=proposal)
            review = assembler._call(**context, prepared=assembler.provider.prepare_relation(material=material, proposal=proposal),
                validator=lambda p: assembler.provider.validate_relation_payload(material=material, proposal=proposal, payload=p))
            validated = assembler.provider.validate_relation_payload(material=material, proposal=proposal, payload=review)
            if validated is None or validated['relation'] == 'uncertain':
                # No more optional model pages once automatic association is
                # undecidable. The complete current theme was already verified.
                raise RelationPageUnresolved('themeRelationUnresolved')
            checked.append((material, validated, review))
        return _reduce(assembler, lease=lease, intent=intent, source=source, run_id=run_id,
                       theme=theme, atoms=atoms, targets=targets, pages=pages, checked=checked)
    except RecoveryThemePageFailed as error:
        if not optional_relation_failure(error):raise
        code = str(error)
    except LiveLongMemoryBudgetExhausted:
        code = 'optionalRelationBudgetExhausted'
    except RelationPageUnresolved as error:
        code = str(error)
    except LiveMemoryContractFailure as error:
        if not ((error.category == 'input' and error.reason == 'inputOverCapacity')
                or (error.category == 'domain' and error.reason == 'noReliableThemes')
                or (error.category == 'contract' and error.reason == 'themeCorrectionKindMismatch')):
            raise
        code = error.code
    return assembler.defer_relation(theme=theme, atoms=atoms, targets=targets, reason=code)
