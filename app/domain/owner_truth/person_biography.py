"""Topic-local biography derived exclusively from current confirmed facts.

Faithful source sentences remain the fallback. Natural prose requires complete
source identity coverage, independent semantic review and mechanical guards.
No generated prose changes the authoritative formal memory ledger.
"""
from copy import deepcopy
from collections import Counter
from hashlib import sha256
import json
import re

ALGORITHM = 'topic-biography-v5-confirmed-groups'
MAX_SENTENCES = 20
MAX_INPUT_CHARACTERS = 12000
PROMPT_VERSION = 'evidence-polish-v10-paragraph-first'
MODEL_CONFIG_VERSION = 'reviewed-polish-v2'
PHOTO_GROUPING_VERSION = 'photo-confirmed-scope-v1'
TOPICS = (
    ('coffee', '咖啡习惯', ('咖啡',), 'daily-habits', '饮食与日常习惯'),
    ('birth', '出生', ('出生', '生日'), 'roots', '个人经历'),
    ('photos', '照片与摄影', ('照片', '摄影', '相册'), 'interests', '兴趣与生活'),
    ('plants', '植物观察', ('薄荷', '植物', '园艺'), 'interests', '兴趣与生活'),
    ('family-contact', '与家人相处', ('父母', '妻子', '女儿', '儿子'), 'family', '家人与相处'),
)
RELATIVE_TIME = re.compile(r'最近|近[一二三四五六七八九十\d]+年|如今|现在|目前|今年|去年|明年|上个月|这两年')

def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def literal_topic_anchor(text):
    """Bounded literal objects, never a shared container/place or inferred hobby.

    Unsupported and coordinated objects stay separate. This is presentation
    grouping only: each complete confirmed sentence and citation is retained.
    """
    anchors = set(re.findall(r'^我[^，,。.!！？?；;]{0,24}?(?:练习|学习|研究)([^，,。.!！？?；;\s]{2,12})[。.!！？?]?$', text.strip()))
    anchors.update(re.findall(r'^我把([^，,。.!！？?；;\s]{2,12})放(?:在|进|入)[^，,。.!！？?；;]+[。.!！？?]?$', text.strip()))
    if len(anchors) != 1:
        return None
    label = next(iter(anchors))
    if label in {'自己','事情','东西','活动','生活','这些','那些','这个','那个','它们'} or re.search(r'和|与|及|、|或者|以及|然后|的|了|着|过|它|这|那', label):
        return None
    return label

def storage_destination_matches(text, label):
    """A location-valued object is redundant only when the literal syntax proves it.

    Do not interpret arbitrary object conflicts, names, or semantic synonyms.
    """
    match = re.fullmatch(r'我把[^，,。.!！？?；;\s]{2,12}放(?:在|进|入)([^，,。.!！？?；;]+)[。.!！？?]?', text.strip())
    if not match:
        return False
    destination = match.group(1).strip()
    # Only literal locative suffixes, not arbitrary substring containment.
    forms = {destination, re.sub(r'(?:里面|里边|内侧|外侧|上面|下面|里|内|上|下)$', '', destination)}
    return bool(label) and label in forms


def subject_identity(record):
    subject = record.get('claimSubjectId') or record.get('memorySubjectId')
    if subject:
        return subject
    # Mentioning someone is not a change of speaker/claim subject.
    third = re.match(r'^(?:我的|我)?(室友|同事|朋友|父亲|母亲|爸爸|妈妈|妻子|丈夫|女儿|儿子|姐姐|哥哥|弟弟|妹妹)', record['narrative'].strip())
    return 'legacy-person:' + third.group(1) if third else 'self'


def verified_binding(record, value):
    if not isinstance(value, dict):
        return None
    for key in ('memoryId', 'memoryVersionId', 'contentHash'):
        if not record.get(key) or value.get(key) != record[key]:
            return None
    if value.get('sourceId') != record['citation'].get('sourceId'):
        return None
    if (not isinstance(value.get('topicId'), str) or not value['topicId']
            or type(value.get('topicVersion')) is not int or value['topicVersion'] < 1
            or not isinstance(value.get('proposalHash'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', value['proposalHash'])
            or not isinstance(value.get('title'), str) or not value['title'].strip()
            or len(value['title']) > 80):
        return None
    return deepcopy(value)


def identity(record, binding=None):
    text = record['narrative']
    subject = subject_identity(record)
    matches = [t for t in TOPICS if any(word in text for word in t[2])]
    if len(matches) == 1 and matches[0][0] == 'photos':
        # Photography is a category, not evidence that two objects are the same.
        # Keep anaphora with their verified confirmed topic. Without that proof,
        # preserve an independent fact instead of mixing unrelated photo sets.
        if binding is not None:
            key, title, section, section_title = 'confirmed:' + binding['topicId'], binding['title'], 'interests', '兴趣与生活'
        else:
            key, title, section, section_title = 'fact:' + record['memoryId'], '记忆片段', 'life-fragments', '人生片段'
    elif len(matches) == 1:
        key, title, _, section, section_title = matches[0]
    elif binding is not None:
        key, title, section, section_title = 'confirmed:' + binding['topicId'], binding['title'], 'interests', '兴趣与生活'
    elif not matches:
        obj = record.get('object') or {}
        label = str(obj.get('label') or '').strip() if isinstance(obj, dict) else ''
        anchor = literal_topic_anchor(text)
        if not label:
            label = anchor or ''
        elif anchor and not obj.get('entityId') and not obj.get('category') and storage_destination_matches(text, label):
            # Keep the complete record/citation; resolve presentation role only.
            label = anchor
        if 1 < len(label) <= 40 and label in text and label not in {'自己','事情','东西','活动','生活'} and (not anchor or anchor == label):
            key, title, section, section_title = 'object:' + label, label, 'interests', '兴趣与生活'
        else:
            key, title, section, section_title = 'fact:' + record['memoryId'], '记忆片段', 'life-fragments', '人生片段'
    else:
        # Multiple unrelated subjects in a single confirmed fact are not split
        # into invented statements. Keep a faithful independent paragraph.
        key, title, section, section_title = 'fact:' + record['memoryId'], '记忆片段', 'life-fragments', '人生片段'
    return 'topic-' + digest([subject, key])[:24], title, section, section_title

def sentence(record):
    text = record['narrative'].strip()
    if RELATIVE_TIME.search(text):
        # A confirmation timestamp is NOT the expression date. Keep the original
        # statement explicitly historical when no source reference date exists.
        text = '原记录中提到：“' + text.rstrip('。') + '”。'
    return {'sentenceId': 'sentence-' + digest(record['memoryId'])[:24], 'text': text,
            'evidence': [deepcopy(record['citation'])],
            'time': deepcopy(record.get('eventTime') or {}),
            'qualifiers': deepcopy(record.get('qualifiers') or {})}

def ordering(record):
    time = record.get('eventTime') or {}
    start = time.get('start')
    expression = str(time.get('expression') or '')
    if not start and re.fullmatch(r'[12][0-9]{3}年', expression) and expression in record['narrative']:
        return (0, expression[:4], record['narrative'])
    if time.get('precision') in {'year', 'month', 'day', 'exact'} and isinstance(start, str) and re.match(r'^\d{4}', start):
        return (0, start, record['narrative'])
    # Text order is a neutral deterministic presentation, not a claimed timeline.
    return (1, '', record['narrative'])

def input_within_budget(block):
    return (len(block['sentences']) <= MAX_SENTENCES
            and len(json.dumps(block, ensure_ascii=False)) <= MAX_INPUT_CHARACTERS)


def build_biography(records, source_fingerprint, previous=None, *, model_config=MODEL_CONFIG_VERSION, bindings=None):
    groups = {}
    old = {b['topicId']: b for s in (previous or {}).get('sections', []) for b in s.get('blocks', []) if 'topicId' in b}
    bindings = bindings or {}
    for record in records:
        binding = verified_binding(record, bindings.get(record['memoryId']))
        topic, title, section, section_title = identity(record, binding)
        group = groups.setdefault(topic, {'title': title, 'section': section, 'sectionTitle': section_title, 'records': [], 'bindings': {}})
        group['records'].append(record)
        if binding is not None:
            group['bindings'][record['memoryId']] = binding

    from app.domain.owner_truth.biography_object_links import resolve_groups
    groups = resolve_groups(groups, subject_identity)
    for group in groups.values():
        if group.get('objectLinks'):
            group['objectLinkFingerprint'] = digest(group['objectLinks'])

    def make_block(topic, group, material, index, count):
        block_topic = topic if count == 1 else topic + '-part-' + str(index)
        proof = [group['bindings'][r['memoryId']] for r in material if r['memoryId'] in group['bindings']]
        grouping = digest(sorted(proof, key=lambda b: b['memoryId']))
        if group.get('objectLinkFingerprint'):
            grouping = digest({'bindings': grouping, 'objectLinks': group['objectLinkFingerprint']})
        fingerprint = digest({'versions': sorted((r['memoryId'], r['memoryVersionId'], r['contentHash']) for r in material),
                              'group': topic, 'title': group['title'], 'grouping': grouping, 'part': [index, count],
                              'algorithm': ALGORITHM, 'prompt': PROMPT_VERSION, 'model': model_config})
        sentences = [sentence(r) for r in material]
        return {'topicId': block_topic, 'groupId': topic, 'partIndex': index, 'partCount': count,
                'groupingFingerprint': grouping, 'blockId': 'paragraph-' + block_topic[6:], 'blockType': 'narrative',
                'title': group['title'], 'text': ''.join(s['text'] for s in sentences),
                'sentences': sentences, 'evidence': [deepcopy(r['citation']) for r in material],
                'facets': sorted({f for r in material for f in r['facets']}),
                'sourceRevision': fingerprint, 'algorithmVersion': ALGORITHM,
                'promptVersion': PROMPT_VERSION, 'modelConfigVersion': model_config,
                'qualityState': 'faithful', 'uncoveredFacts': []}

    sections = {}
    for topic, group in sorted(groups.items()):
        material = sorted(group['records'], key=lambda r: (ordering(r), r['memoryId']))
        parts, current = [], []
        for record in material:
            proposed = current + [record]
            # Upper bound for partCount gives conservative serialized-size packing.
            probe = make_block(topic, group, proposed, len(parts), len(material))
            if current and not input_within_budget(probe):
                parts.append(current)
                current = [record]
            else:
                current = proposed
        if current:
            parts.append(current)
        section = sections.setdefault(group['section'], {'sectionId': group['section'], 'title': group['sectionTitle'], 'blocks': [], 'evidence': []})
        for index, part in enumerate(parts):
            block = make_block(topic, group, part, index, len(parts))
            if old.get(block['topicId'], {}).get('sourceRevision') == block['sourceRevision']:
                block = deepcopy(old[block['topicId']])
            section['blocks'].append(block)
            section['evidence'].extend(deepcopy(block['evidence']))
    for section in sections.values():
        section['blocks'].sort(key=lambda b: (b['title'], b.get('groupId', b['topicId']), b.get('partIndex', 0)))
    document = {'schemaVersion': 'owner-truth-biography-projection-v1', 'format': 'structuredText',
                'state': 'ready' if records else 'empty', 'sourceFingerprint': source_fingerprint,
                'algorithmVersion': ALGORITHM, 'title': '我的人生记录', 'overview': None,
                'sections': [sections[key] for key in sorted(sections)], 'sectionCount': len(sections),
                'supportingMemoryCount': len(records)}
    document['documentVersion'] = digest(document)
    return document

def apply_composition(block, response):
    """Validate exact coverage and identity; accept only whole sentence order.

    Arbitrary model prose, omissions, duplicates and invented references are
    rejected, including seemingly benign removal of plans or negations.
    """
    if isinstance(response, dict) and 'sentences' in response:
        return apply_polish(block, response)
    if not isinstance(response, dict) or set(response) != {'sentenceIds'}:
        raise ValueError('invalid composition shape')
    ids = response['sentenceIds']
    expected = {s['sentenceId']: s for s in block['sentences']}
    if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids) or len(ids) != len(expected) or set(ids) != set(expected):
        raise ValueError('composition does not cover exact sentence identities')
    # Proven chronology cannot be reversed by a stylistic ordering suggestion.
    dated = [s['sentenceId'] for s in block['sentences'] if s['time'].get('start') and s['time'].get('precision') in {'year','month','day','exact'}]
    if [x for x in ids if x in dated] != dated:
        raise ValueError('composition reverses supported chronology')
    result = deepcopy(block)
    result['sentences'] = [deepcopy(expected[x]) for x in ids]
    result['text'] = ''.join(s['text'] for s in result['sentences'])
    result['qualityState'] = 'composed'
    return result

def _protected_terms(text):
    """Mechanical guardrail in addition to, never instead of semantic review."""
    terms = set(re.findall(r'\d+(?:\.\d+)?', text))
    terms.update(re.findall(r'(?:每周[一二三四五六日天]|每天|每月|每年|最近[一二三四五六七八九十两\d]+年|近[一二三四五六七八九十两\d]+年|上午|下午|晚上)', text))
    return terms


def apply_polish(block, response):
    if not isinstance(response, dict) or set(response) != {'sentences', 'audit'}:
        raise ValueError('invalid polish shape')
    generated, audit = response['sentences'], response['audit']
    inputs = {s['sentenceId']: s for s in block['sentences']}
    if not isinstance(generated, list) or not 1 <= len(generated) <= len(inputs) * 2:
        raise ValueError('invalid polished sentence count')
    if not isinstance(audit, dict) or set(audit) != {'verdict', 'facts', 'sentences', 'issues'} or audit['verdict'] != 'supported' or audit['issues'] != []:
        raise ValueError('semantic audit uncertain' if isinstance(audit, dict) and audit.get('verdict') == 'uncertain' else 'semantic audit did not approve')
    facts = audit['facts']
    if not isinstance(facts, list) or len(facts) != len(inputs) or any(
        not isinstance(f, dict) or set(f) != {'sentenceId', 'covered', 'qualifiersPreserved'}
        or not isinstance(f['sentenceId'], str) or f['covered'] is not True or f['qualifiersPreserved'] is not True for f in facts
    ) or {f['sentenceId'] for f in facts} != set(inputs):
        raise ValueError('semantic audit missing input coverage')
    judgments = audit['sentences']
    if not isinstance(judgments, list) or len(judgments) != len(generated) or any(
        not isinstance(j, dict) or set(j) != {'index', 'supported'} or type(j['index']) is not int or j['supported'] is not True for j in judgments
    ) or {j['index'] for j in judgments} != set(range(len(generated))):
        raise ValueError('semantic audit missing output coverage')
    output, seen = [], set()
    for i, sentence in enumerate(generated):
        if not isinstance(sentence, dict) or set(sentence) != {'text', 'sourceSentenceIds'}:
            raise ValueError('invalid polished sentence')
        text, refs = sentence['text'], sentence['sourceSentenceIds']
        if not isinstance(text, str) or not text.strip() or len(text) > 1600 or not isinstance(refs, list) or not refs:
            raise ValueError('invalid polished text/references')
        if any(not isinstance(x, str) or x not in inputs for x in refs) or len(refs) != len(set(refs)):
            raise ValueError('unknown/duplicate polished reference')
        source_text = ''.join(inputs[x]['text'] for x in refs)
        # A reviewer saying yes cannot authorize invented numbers/dates.
        if not set(re.findall(r'\d+(?:\.\d+)?', text)) <= set(re.findall(r'\d+(?:\.\d+)?', source_text)):
            raise ValueError('polish invented a numeric fact')
        # Relative dates must retain their reference as a past expression, not
        # silently become the reader's current time or an invented calendar date.
        if RELATIVE_TIME.search(source_text) and RELATIVE_TIME.search(text) and not any(x in text for x in ('那时','当时','谈起','提到','提起','曾说')):
            raise ValueError('relative time lost its expression anchor')
        evidence = {r['memoryVersionId']: deepcopy(r) for x in refs for r in inputs[x]['evidence']}
        output.append({'sentenceId': 'sentence-' + digest([block['topicId'],refs,text])[:24], 'text':text.strip(),
                       'evidence':list(evidence.values()), 'sourceSentenceIds':refs,
                       'timeBySource':{x:deepcopy(inputs[x]['time']) for x in refs},
                       'qualifiersBySource':{x:deepcopy(inputs[x]['qualifiers']) for x in refs}})
        seen.update(refs)
    if seen != set(inputs):
        raise ValueError('polish omitted a confirmed fact')
    # Check all sentences citing each input together: legitimate splitting need
    # not repeat every qualifier in every output sentence. Unrelated facts cannot
    # supply a missing qualifier because their references are excluded.
    for source_id, source in inputs.items():
        bound_text = ''.join(s['text'] for s in output if source_id in s['sourceSentenceIds'])
        for source_words, alternatives in (
            (('偶尔',), ('偶尔', '有时', '不时')),
            (('打算', '计划'), ('打算', '计划', '准备')),
            (('还没有', '尚未'), ('还没有', '尚未', '还没')),
        ):
            if any(w in source['text'] for w in source_words) and not any(w in bound_text for w in alternatives):
                raise ValueError('polish dropped a key qualifier')
        if re.search(r'不|没|未|从未', source['text']) and not re.search(r'不|没|未|从未', bound_text):
            raise ValueError('polish dropped negation')
        if not _protected_terms(source['text']) <= _protected_terms(bound_text):
            raise ValueError('polish moved source timing/frequency')
    # Cross-sentence coverage permits splitting, but no literal timing/frequency
    # may disappear from the complete paragraph. Semantic equivalents fall back
    # conservatively rather than silently relaxing the content contract.
    source_text=''.join(s['text'] for s in inputs.values()); text=''.join(s['text'] for s in output)
    if not _protected_terms(source_text) <= _protected_terms(text):
        raise ValueError('polish dropped time/frequency/numeric evidence')
    if len(inputs) > 1:
        compact = lambda value: re.sub(r'[\s，,。.!！？?；;：:、“”「」‘’\"\x27]', '', value)
        clauses = lambda value: Counter(compact(s) for s in re.split(r'[，,。.!！？?；;：:]', value) if compact(s))
        if compact(source_text) == compact(text) or clauses(source_text) == clauses(text):
            raise ValueError('compositionNotIntegrated')
    result=deepcopy(block);result['sentences']=output;result['text']=text;result['qualityState']='polished'
    return result


def retain_valid_composition(current, old, response):
    """Reuse old prose only when ALL its supporting inputs are still identical.

    Never surgically drop a sentence: a later sentence can depend on its subject
    or chronology. Any correction/revocation discards the whole old block.
    """
    if not isinstance(old, dict) or any(old.get(k) != current.get(k) for k in ('topicId', 'groupId', 'title')):
        raise ValueError('retainedTopicMismatch')
    inputs = {s['sentenceId']: s for s in current['sentences']}
    originals = old.get('sentences', [])
    if not originals or len({s['sentenceId'] for s in originals}) != len(originals):
        raise ValueError('retainedIdentityMismatch')
    if any(inputs.get(s['sentenceId']) != s for s in originals):
        raise ValueError('retainedEvidenceChanged')
    composed = apply_composition(old, response)
    if composed['qualityState'] != 'polished':
        raise ValueError('retainedNotPolished')
    covered = {s['sentenceId'] for s in originals}
    added = [deepcopy(s) for s in current['sentences'] if s['sentenceId'] not in covered]
    result = deepcopy(current)
    result['sentences'] = deepcopy(composed['sentences']) + added
    result['text'] = composed['text'] + ''.join(s['text'] for s in added)
    result['qualityState'] = 'retainedWithAdditions' if added else 'retained'
    return result


def composition_error_code(error):
    """Closed diagnostic vocabulary: never persist arbitrary exception text."""
    message = str(error)
    exact = {
        'compositionNotIntegrated': 'compositionNotIntegrated',
        'responseTruncated': 'responseTruncated',
        'permanentProviderFailure': 'providerRejected',
        'providerNotConfigured': 'providerNotConfigured',
        'semantic audit uncertain': 'auditUncertain',
        'semantic audit did not approve': 'auditRejected',
        'compositionInputBudgetExceeded': 'compositionInputBudgetExceeded',
    }
    if message in exact:
        return exact[message]
    if message in {'invalidWriterShape', 'invalidWriterCount', 'invalidWriterSentence'}:
        return 'writerShape'
    if message in {'invalidWriterEvidence', 'incompleteWriterCoverage', 'invalidAuditIdentity'}:
        return 'writerReferences' if message != 'invalidAuditIdentity' else 'auditShape'
    if message.startswith('semantic audit missing') or message == 'invalid polish shape':
        return 'auditShape'
    if message in {'polish dropped a key qualifier', 'polish dropped negation', 'polish moved source timing/frequency',
                   'polish dropped time/frequency/numeric evidence', 'relative time lost its expression anchor',
                   'polish invented a numeric fact'}:
        return 'qualifierMismatch'
    if message in {'polish omitted a confirmed fact', 'unknown/duplicate polished reference', 'composition does not cover exact sentence identities'}:
        return 'factCoverage'
    return 'invalidComposition'
