"""Reviewable Live themes compose immutable V5 facts; they are not new facts.

Model grouping and semantic review are separate inputs. Identity, ownership,
evidence membership, omissions, CAS and terminal protections are mechanical.
The model never chooses a formal-memory write or bypasses its V5 confirmation.
"""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Any, Sequence
from uuid import UUID, uuid5

SCHEMA = 'owner-truth-live-theme-v1'
_NS = UUID('68d9df39-1baf-4a1e-a0e7-154ef6cf5043')

class LiveThemeConflict(ValueError):
    pass

def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def _ids(values, *, maximum=64):
    if not isinstance(values,list) or not 1 <= len(values) <= maximum:
        raise LiveThemeConflict('invalidThemeMembers')
    if any(not isinstance(v,str) or not v for v in values) or len(set(values))!=len(values):
        raise LiveThemeConflict('invalidThemeMembers')
    return tuple(values)

def supported_atom_catalog(*, atoms, turns):
    """Resolve every private atom range against immutable owner message text.

    A support hash alone is not evidence. Recalculate the original range ID,
    text hash and full message hash, then bind that range to message identity.
    Neither a model nor a test fixture can invent a valid evidence reference.
    """
    by_index={}
    for turn in turns:
        index=turn.get('index')
        if type(index) is not int or index<1 or index in by_index:
            raise LiveThemeConflict('invalidSourceTurnIdentity')
        text=turn.get('text');message_id=turn.get('messageId')
        if (not isinstance(text,str) or not isinstance(message_id,str)
            or sha256(text.encode()).hexdigest()!=turn.get('contentHash')):
            raise LiveThemeConflict('sourceMessageHashMismatch')
        try:UUID(message_id)
        except (TypeError,ValueError):raise LiveThemeConflict('invalidSourceMessageIdentity') from None
        by_index[index]=turn
    result=[];seen=set()
    for atom in atoms:
        aid=str(atom.get('id') or atom.get('atomId') or '')
        if not aid or aid in seen:raise LiveThemeConflict('invalidAtomIdentity')
        seen.add(aid)
        memory=atom.get('memory')
        if not isinstance(memory,dict):raise LiveThemeConflict('invalidAtomMemory')
        proof=memory.get('_supportProofHash')
        if not isinstance(proof,str) or len(proof)!=64 or any(c not in '0123456789abcdef' for c in proof):
            raise LiveThemeConflict('atomSupportRequired')
        ranges=memory.get('_sourceEvidenceRanges')
        if not isinstance(ranges,list) or not ranges:raise LiveThemeConflict('atomEvidenceRequired')
        evidence=[];indices=set()
        for ref in ranges:
            if not isinstance(ref,dict):raise LiveThemeConflict('atomEvidenceInvalid')
            index=ref.get('turnIndex');start=ref.get('start');end=ref.get('end')
            if type(index) is not int or type(start) is not int or type(end) is not int:
                raise LiveThemeConflict('atomEvidenceInvalid')
            turn=by_index.get(index)
            if turn is None or turn.get('role')!='user' or not 0<=start<end<=len(turn['text']):
                raise LiveThemeConflict('atomEvidenceOutsideOwnerSource')
            text_hash=sha256(turn['text'][start:end].encode()).hexdigest()
            eid=sha256(f'{index}:{start}:{end}:{text_hash}'.encode()).hexdigest()
            if ref.get('textHash')!=text_hash or ref.get('evidenceId')!=eid:
                raise LiveThemeConflict('atomEvidenceHashMismatch')
            # Source position and identical text from different utterances stay distinct.
            evidence.append(dict(evidenceId=digest([turn['messageId'],eid]),messageId=turn['messageId'],
                turnIndex=index,start=start,end=end,textHash=text_hash,text=turn['text'][start:end]))
            indices.add(index)
        expected=memory.get('sourceTurnIndices')
        if not isinstance(expected,list) or any(type(i) is not int for i in expected) or set(expected)!=indices:
            raise LiveThemeConflict('atomEvidenceTurnMismatch')
        memory_public={k:v for k,v in memory.items() if not k.startswith('_')}
        state=atom.get('state','active')
        result.append(dict(atomId=aid,content=memory_public,contentHash=digest(memory_public),
            dimensions=memory.get('dimensions') or [memory.get('memoryKind')],
            supportState='supported' if state=='active' else 'superseded' if state in {'merged','superseded','retracted'} else 'conflicted',
            evidenceIds=list(dict.fromkeys(e['evidenceId'] for e in evidence)),evidence=evidence))
    return result

@dataclass(frozen=True)
class SupportedLiveTheme:
    key: str
    title: str
    summary: str
    atom_ids: tuple[str,...]
    evidence_ids: tuple[str,...]
    dimensions: tuple[str,...]
    support_hash: str

    def payload(self):
        return dict(schemaVersion=SCHEMA,key=self.key,title=self.title,summary=self.summary,
            atomIds=list(self.atom_ids),evidenceIds=list(self.evidence_ids),
            dimensions=list(self.dimensions),supportHash=self.support_hash)

@dataclass(frozen=True)
class ThemeReviewResult:
    themes: tuple[SupportedLiveTheme,...]
    omitted_atom_ids: tuple[str,...]
    blocked: tuple[dict,...]
    input_hash: str

def validate_theme_review(*, atoms: Sequence[Mapping[str,Any]], proposal: Mapping[str,Any],
                          review: Mapping[str,Any]) -> ThemeReviewResult:
    """Accept independently supported groups; preserve failures per group.

    Input atoms must come from the server's supported, evidence-bound atom
    ledger. Clients and models cannot supply or replace this authoritative set.
    Missing facts are explicit omissions; uncertain/corrected content cannot
    enter a different group just to obtain a successful result.
    """
    if not 1 <= len(atoms) <= 64:raise LiveThemeConflict('themePageCapacity')
    by_id={}
    for atom in atoms:
        key=atom.get('atomId')
        if not isinstance(key,str) or not key or key in by_id:
            raise LiveThemeConflict('invalidAtomIdentity')
        if atom.get('supportState') not in {'supported','conflicted','superseded'}:
            raise LiveThemeConflict('atomSupportRequired')
        _ids(atom.get('evidenceIds'))
        if not isinstance(atom.get('contentHash'),str) or (len(atom['contentHash'])!=64 or any(c not in '0123456789abcdef' for c in atom['contentHash'])):
            raise LiveThemeConflict('invalidAtomContentHash')
        by_id[key]=atom
    input_hash=digest(list(atoms))
    if proposal.get('schemaVersion')!=SCHEMA or proposal.get('inputHash')!=input_hash:
        raise LiveThemeConflict('themeInputMismatch')
    groups=proposal.get('themes')
    if not isinstance(groups,list) or len(groups)>64:raise LiveThemeConflict('invalidThemes')
    proposal_hash=digest(proposal)
    if (review.get('schemaVersion')!='owner-truth-live-theme-support-v1'
        or review.get('inputHash')!=input_hash or review.get('proposalHash')!=proposal_hash):
        raise LiveThemeConflict('themeReviewBindingMismatch')
    raw_reviews=review.get('themes')
    if not isinstance(raw_reviews,list):raise LiveThemeConflict('themeReviewIncomplete')
    reviews={}
    for item in raw_reviews:
        if not isinstance(item,dict) or not isinstance(item.get('key'),str) or item['key'] in reviews:
            raise LiveThemeConflict('themeReviewIncomplete')
        reviews[item['key']]=item
    seen=set();group_keys=set();accepted=[];blocked=[]
    for group in groups:
        if not isinstance(group,dict):raise LiveThemeConflict('invalidTheme')
        key=group.get('key')
        if not isinstance(key,str) or not 1 <= len(key)<=128 or key in group_keys:
            raise LiveThemeConflict('invalidThemeKey')
        group_keys.add(key)
        members=_ids(group.get('atomIds'))
        if not set(members)<=set(by_id) or seen.intersection(members):
            raise LiveThemeConflict('wrongOrRepeatedAtom')
        seen.update(members)
        evidence=_ids(group.get('evidenceIds'),maximum=256)
        expected={e for aid in members for e in by_id[aid]['evidenceIds']}
        if set(evidence)!=expected:raise LiveThemeConflict('themeEvidenceMismatch')
        title=group.get('title');summary=group.get('summary');dimensions=group.get('dimensions')
        if (not isinstance(title,str) or not 1<=len(title.strip())<=120
            or not isinstance(summary,str) or not 1<=len(summary.strip())<=4000):
            raise LiveThemeConflict('invalidThemeText')
        dims=_ids(dimensions,maximum=16)
        allowed_dims={d for aid in members for d in by_id[aid].get('dimensions',[])}
        if not set(dims)<=allowed_dims:raise LiveThemeConflict('inventedThemeDimension')
        assessment=reviews.get(key)
        if assessment is None:raise LiveThemeConflict('themeReviewIncomplete')
        if (assessment.get('atomIds')!=list(members) or assessment.get('evidenceIds')!=list(evidence)):
            raise LiveThemeConflict('themeReviewEvidenceMismatch')
        if assessment.get('verdict') not in {'supported','unsupported','uncertain'}:
            raise LiveThemeConflict('invalidThemeVerdict')
        reliable=(assessment['verdict']=='supported' and assessment.get('sameSubjectEvent') is True
            and assessment.get('compatibleTime') is True and assessment.get('correctionsResolved') is True
            and all(by_id[aid]['supportState']=='supported' for aid in members))
        if not reliable:
            blocked.append(dict(key=key,atomIds=list(members),reason='themeSupportUnresolved'))
            continue
        accepted.append(SupportedLiveTheme(key,title.strip(),summary.strip(),members,evidence,dims,digest(assessment)))
    if set(reviews)!=group_keys:raise LiveThemeConflict('themeReviewIncomplete')
    declared=proposal.get('omittedAtomIds')
    if (not isinstance(declared,list) or any(not isinstance(v,str) for v in declared)
        or len(declared)!=len(set(declared)) or set(declared)!=set(by_id)-seen):
        raise LiveThemeConflict('themeOmissionManifestMismatch')
    omitted=set(declared)|{a for b in blocked for a in b['atomIds']}
    return ThemeReviewResult(tuple(accepted),tuple(sorted(omitted)),tuple(blocked),input_hash)

def next_theme_revision(*, previous: Mapping[str,Any] | None, theme: SupportedLiveTheme,
                        owner: str, vault: str, epoch: int, stable_key: str,
                        source_id: str, snapshot_id: str, expected_version: int,
                        member_bindings: Mapping[str,Mapping[str,Any]], change_reason: str='new', relation_proof=None) -> dict:
    """Immutable proposal revision; existing confirmed facts are never updated."""
    if not owner or not vault or type(epoch) is not int or epoch<0 or not stable_key:
        raise LiveThemeConflict('invalidThemeOwner')
    UUID(source_id);UUID(snapshot_id)
    if type(expected_version) is not int or expected_version<0:raise LiveThemeConflict('invalidThemeVersion')
    if set(member_bindings)!=set(theme.atom_ids):raise LiveThemeConflict('themeCandidateMembershipMismatch')
    for item in member_bindings.values():
        UUID(item['candidateId'])
        UUID(item['sourceId'])
        if len(str(item.get('proposalHash','')))!=64:
            raise LiveThemeConflict('themeCandidateBindingMismatch')
    # Fingerprints represent validated fact meaning, not Source/turn positions.
    fingerprints=sorted(set(str(v['factHash']) for v in member_bindings.values()))
    if any(len(v)!=64 or any(c not in '0123456789abcdef' for c in v) for v in fingerprints):raise LiveThemeConflict('invalidFactFingerprint')
    scope=dict(ownerId=owner,vaultId=vault,authorityEpoch=epoch)
    topic_id=str(uuid5(_NS,digest([owner,vault,epoch,stable_key])))
    linked=None;version=1
    if previous is not None:
        if any(previous.get(k)!=v for k,v in scope.items()):raise LiveThemeConflict('themeOwnerMismatch')
        if previous.get('version')!=expected_version:raise LiveThemeConflict('themeVersionChanged')
        if previous.get('state') not in {'pending','accepted','rejected'}:raise LiveThemeConflict('invalidThemeState')
        if previous.get('snapshotId')==snapshot_id:
            if previous.get('theme')!=theme.payload() or previous.get('members')!=dict(member_bindings):
                raise LiveThemeConflict('immutableThemeRevision')
            return dict(previous)
        topic_id=previous['topicId']
        if previous['state']=='pending':
            version=previous['version']+1
        else:
            if set(fingerprints)<=set(previous.get('factHashes',[])):
                return {**scope,'status':'noChange','topicId':topic_id,'reason':'terminalThemeUnchanged'}
            if change_reason not in {'supplement','correction'}:
                raise LiveThemeConflict('terminalThemeRequiresRelatedProposal')
            linked=topic_id
            topic_id=str(uuid5(_NS,digest([topic_id,snapshot_id,change_reason])))
    elif expected_version!=0:raise LiveThemeConflict('themeVersionChanged')
    result={**scope,'schemaVersion':SCHEMA,'topicId':topic_id,'version':version,'state':'pending',
        'sourceId':source_id,'snapshotId':snapshot_id,'linkedTopicId':linked,'changeReason':change_reason,
        'theme':theme.payload(),'members':dict(member_bindings),'factHashes':fingerprints}
    if relation_proof is not None:result['relationProof']=relation_proof
    result['proposalHash']=digest(result)
    return result
