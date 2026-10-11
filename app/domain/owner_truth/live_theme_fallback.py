"""Evidence-bound reuse of private themes; old drafts remain hints only."""
import re
from app.domain.owner_truth.live_topics import digest, SupportedLiveTheme

SCHEMA = 'private-theme-reuse-v2'
BINDING_FIELDS = ('atomId', 'content', 'contentHash', 'supportState', 'dimensions', 'evidenceIds', 'evidence')


def reuse_scope(*, run_id, vault_id, owner_subject_id, authority_epoch, capture_generation):
    return dict(runId=str(run_id), vaultId=str(vault_id), ownerSubjectId=str(owner_subject_id),
                authorityEpoch=int(authority_epoch), captureGeneration=int(capture_generation))


def atom_binding(atom):
    return digest({key: atom[key] for key in BINDING_FIELDS})


def reuse_receipt(*, scope, catalog):
    return dict(schemaVersion=SCHEMA, scope=dict(scope),
                atoms={a['atomId']: atom_binding(a) for a in catalog})


def eligible_private_themes(*, coverage, scope, catalog):
    """Return only whole, unchanged themes from a caller-verified durable draft.

    A stale/incompatible receipt cannot authorize reuse. Coverage digest and
    completed-unit ownership are checked by the repository caller first.
    """
    receipt = coverage.get('verifiedReuse')
    if not isinstance(receipt, dict) or receipt.get('schemaVersion') != SCHEMA or receipt.get('scope') != scope:
        return ()
    bindings = receipt.get('atoms')
    if not isinstance(bindings, dict):
        return ()
    current = {a['atomId']: a for a in catalog}
    result = []
    for value in coverage.get('themes', []):
        try:
            ids = value['atomIds']; evidence = value['evidenceIds']; dimensions = value['dimensions']
            if (value.get('schemaVersion') != 'owner-truth-live-theme-v1'
                    or not isinstance(ids, list) or not ids or len(ids) != len(set(ids))
                    or not set(ids) <= current.keys()):
                continue
            members = [current[aid] for aid in ids]
            if any(a['supportState'] != 'supported' or a['contentHash'] != digest(a['content'])
                   or bindings.get(a['atomId']) != atom_binding(a) for a in members):
                continue
            if (not isinstance(evidence, list) or len(evidence) != len(set(evidence))
                    or set(evidence) != {e for a in members for e in a['evidenceIds']}
                    or not isinstance(dimensions, list) or not dimensions
                    or len(dimensions) != len(set(dimensions))
                    or not set(dimensions) <= {d for a in members for d in a['dimensions']}):
                continue
            if any(not isinstance(value[k], str) or not value[k].strip() for k in ('key', 'title', 'summary', 'supportHash')):
                continue
            if not re.fullmatch('[0-9a-f]{64}', value['supportHash']):
                continue
            result.append(SupportedLiveTheme(value['key'], value['title'], value['summary'],
                         tuple(ids), tuple(evidence), tuple(dimensions), value['supportHash']))
        except (KeyError, TypeError, ValueError):
            # Schema-incompatible drafts stay ineligible, never partly reused.
            continue
    return tuple(result)


def private_draft_coverage(*, parent_unit_id, scope, catalog, themes, omitted, blocked):
    return dict(scope='privateBatchTheme', parentUnitId=parent_unit_id,
                notAuthoritativeUntilFinalRelations=True, themes=[t.payload() for t in themes],
                omittedAtomIds=list(omitted), blockedThemes=list(blocked),
                verifiedReuse=reuse_receipt(scope=scope, catalog=catalog))
