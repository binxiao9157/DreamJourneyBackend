"""Bounded cross-scene relation proposals, independently reviewed before use.

Identity/version/source remain database authority. A model can suggest a
relation but cannot invent a member, silently overwrite a decision or choose
an unchecked formal-memory target.
"""
from app.domain.owner_truth.live_topics import digest,LiveThemeConflict

SCHEMA='owner-truth-live-theme-relation-v1'


def _correction_intent_supported(material, proposal, review):
    """Bind intent to new owner evidence; semantic approval remains independent.

    Missing old-version proof is unresolved, never implicit permission to target
    formal memory. A quote is necessary but is not by itself a semantic verdict.
    """
    if review.get('correctionIntentSupported') is not True:
        return False
    proofs = proposal.get('correctionEvidence')
    replaces = proposal['replaces']
    if not isinstance(proofs, dict) or set(proofs) != set(replaces):
        return False
    atoms = {a['atomId']: a for a in material['atoms']}
    for atom_id, proof in proofs.items():
        if not isinstance(proof, dict):
            return False
        eid, quote = proof.get('evidenceId'), proof.get('quote')
        if not isinstance(eid, str) or not isinstance(quote, str) or not quote.strip():
            return False
        atom = atoms[atom_id]
        if eid not in atom.get('evidenceIds', []):
            return False
        evidence = [e for e in atom.get('evidence', []) if e.get('evidenceId') == eid]
        if len(evidence) != 1 or quote not in evidence[0].get('text', ''):
            return False
    return True

def validate_relation(*,material,proposal,review=None):
    if proposal.get('schemaVersion')!=SCHEMA or proposal.get('inputHash')!=digest(material):
        raise LiveThemeConflict('themeRelationBindingMismatch')
    relation=proposal.get('relation')
    if relation not in {'none','uncertain','supplement','correction','duplicate'}:
        raise LiveThemeConflict('invalidThemeRelation')
    target=proposal.get('targetTopicId')
    targets={x['topicId']:x for x in material['targets']}
    if relation in {'none','uncertain'}:
        if target is not None or any(proposal.get(k) is not None for k in ('targetVersion','targetHash')):
            raise LiveThemeConflict('unexpectedRelationTarget')
        # An independent/uncertain proposal cannot smuggle merge operations.
        if (('duplicateAtomIds' in proposal and proposal['duplicateAtomIds'] != [])
            or ('replaces' in proposal and proposal['replaces'] != {})):
            raise LiveThemeConflict('unexpectedRelationMembers')
    else:
        old=targets.get(target)
        if old is None or proposal.get('targetVersion')!=old['version'] or proposal.get('targetHash')!=old['proposalHash']:
            raise LiveThemeConflict('themeRelationTargetMismatch')
        new={a['atomId']:a for a in material['atoms']};prior={a['atomId']:a for a in old['atoms']}
        duplicates=proposal.get('duplicateAtomIds');replaces=proposal.get('replaces')
        if (not isinstance(duplicates,list) or len(set(duplicates))!=len(duplicates) or not set(duplicates)<=set(new)
            or not isinstance(replaces,dict) or not set(replaces)<=set(new)
            or not set(replaces.values())<=set(prior) or len(set(replaces.values()))!=len(replaces)
            or set(replaces)&set(duplicates)):
            raise LiveThemeConflict('themeRelationMemberMismatch')
        # Only exact validated fact meaning may be silently suppressed. Paraphrases
        # require ordinary V5 review rather than trusting model deduplication.
        from app.services.owner_truth_live_topics import fact_fingerprint
        old_hashes={fact_fingerprint(a['content']) for a in prior.values()}
        if any(fact_fingerprint(new[i]['content']) not in old_hashes for i in duplicates):
            raise LiveThemeConflict('unprovenThemeDuplicate')
        if (relation=='duplicate' and (set(duplicates)!=set(new) or replaces)
            or relation=='correction' and not replaces or relation!='correction' and replaces):
            raise LiveThemeConflict('themeRelationKindMismatch')
        for new_id,old_id in replaces.items():
            if new[new_id]['content'].get('memoryKind')!=prior[old_id]['content'].get('memoryKind'):
                raise LiveThemeConflict('themeCorrectionKindMismatch')
        for key,limit in [('title',120),('summary',4000)]:
            if not isinstance(proposal.get(key),str) or not 1<=len(proposal[key].strip())<=limit:
                raise LiveThemeConflict('invalidRelationSummary')
    if review is not None:
        if (review.get('schemaVersion')!='owner-truth-live-theme-relation-support-v1'
            or review.get('inputHash')!=digest(material) or review.get('proposalHash')!=digest(proposal)
            or review.get('verdict') not in {'supported','unsupported','uncertain'}):
            raise LiveThemeConflict('themeRelationReviewBindingMismatch')
        predicates = ('sameSubjectEvent','compatibleTime','correctionsResolved','summarySupported')
        if any(type(review.get(k)) is not bool for k in predicates):
            raise LiveThemeConflict('themeRelationReviewPredicateInvalid')
        if review['verdict'] != 'supported' or relation == 'uncertain':
            return None
        if relation == 'none':
            # These are predicates about a proposed merge. Independent events
            # need not have compatible dates or any correction to resolve.
            # Still require affirmative independent review and supported text;
            # a same-event claim is contradictory, not a reason to bypass it.
            if review['sameSubjectEvent'] or not review['summarySupported']:
                return None
        else:
            # A structurally validated duplicate/supplement has no correction
            # mapping. Its not-applicable correction predicate is not a denial.
            # Reviewed terminal themes are immutable. The current theme already
            # has a supported title/summary grounded only in this scene; relation
            # classification cannot rewrite that text using old-only facts.
            if targets[target].get('state') in {'accepted', 'rejected'}:
                current = material.get('theme', {})
                if any(not isinstance(current.get(k), str) or not current[k].strip()
                       or proposal.get(k) != current[k] for k in ('title', 'summary')):
                    return None
            required = ('sameSubjectEvent', 'compatibleTime', 'summarySupported')
            if not all(review[k] for k in required):
                return None
            if relation == 'correction' and (not review['correctionsResolved']
                or not _correction_intent_supported(material, proposal, review)):
                return None
    return proposal


def bind_terminal_relation_display(*, material, proposal):
    """Terminal-target display belongs to the supported new theme, not relation AI.

    Validate model-owned relation/target/mapping first. Keep raw proposal evidence
    in its work unit; only the independently reviewed proposal uses this binding.
    Pending themes still need a reviewed combined summary.
    """
    validate_relation(material=material, proposal=proposal)
    target = next((t for t in material['targets'] if t['topicId'] == proposal.get('targetTopicId')), None)
    if target is None or target.get('state') not in {'accepted', 'rejected'}:
        return proposal
    theme = material.get('theme', {})
    if any(not isinstance(theme.get(k), str) or not theme[k].strip() for k in ('title', 'summary')):
        raise LiveThemeConflict('currentThemeDisplayUnavailable')
    return dict(proposal, title=theme['title'], summary=theme['summary'])
