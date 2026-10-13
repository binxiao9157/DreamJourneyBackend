"""Literal cross-entry object links for biography presentation only.

No ledger writes, fuzzy matching, model calls or transitive group merging.
Unsupported/ambiguous evidence leaves the original groups intact.
"""
from copy import deepcopy
import re

RULE_VERSION = 'literal-object-reference-v1'
MAX_TARGETS_PER_PLACE = 32
_TOKEN = r'[^，,。.!！？?；;：:\s]'
_PLACEMENT = re.compile(r'^我把(' + _TOKEN + r'{2,30})放(?:在|进|入)(' + _TOKEN + r'{2,40})[。.!]?$')
_ATTRIBUTE = re.compile(r'^这个(' + _TOKEN + r'{2,30})是(' + _TOKEN + r'{1,16}?)(?:的)?[。.!]?$')
_REFERENCE = re.compile(r'^(?:补充|关于)(' + _TOKEN + r'{2,40}?)(?:的)?(?:这个|那个)(' + _TOKEN + r'{2,40})[：:，,]')
_UNSAFE_NAME = re.compile(r'另|新买|其他|两个|两只|一对|和|与|及|、|它|这个|那个')


def _place(value):
    # Structure particle only. Above/below, inside/outside remain distinct.
    return re.sub(r'\s|的', '', value)


def _name(value):
    value = re.sub(r'^一(?:个|只|台|把|本|件)', '', value)
    if not 2 <= len(value) <= 30 or _UNSAFE_NAME.search(value):
        return None
    return value


def _object(record):
    value = record.get('object')
    return value if isinstance(value, dict) else {}


def _root(group, subject_identity):
    placements, attributes, entities, categories = [], [], set(), set()
    subjects = {subject_identity(r) for r in group['records']}
    if len(subjects) != 1:
        return None
    for record in group['records']:
        text = record['narrative'].strip()
        location, attribute = _PLACEMENT.fullmatch(text), _ATTRIBUTE.fullmatch(text)
        if location:
            name = _name(location[1])
            if not name:
                return None
            placements.append((name, _place(location[2])))
        elif attribute:
            attributes.append((attribute[1], attribute[2]))
        else:
            # A confirmed theme may contain multiple objects; its title is not proof.
            return None
    if not placements or len(set(placements)) != 1:
        return None
    name, place = placements[0]
    if any(n != name for n, _ in attributes):
        return None
    modifiers = {value for _, value in attributes}
    if len(modifiers) > 1:
        return None
    labels = {name} | {a + name for a in modifiers}
    for record in group['records']:
        obj = _object(record)
        label = str(obj.get('label') or '').strip()
        # Location-valued object fields are not identities of the stored object.
        if label and label not in labels and _place(label) != place:
            return None
        if label in labels:
            if obj.get('entityId'):
                entities.add(obj['entityId'])
            if obj.get('category'):
                categories.add(obj['category'])
    if len(entities) > 1 or len(categories) > 1:
        return None
    return dict(subject=next(iter(subjects)), place=place, labels=labels,
                entities=entities, categories=categories)


def _reference(record, subject_identity):
    match = _REFERENCE.match(record['narrative'].strip())
    if not match or _UNSAFE_NAME.search(match[2]):
        return None
    obj = _object(record)
    place = (record.get('qualifiers') or {}).get('place')
    if isinstance(place, dict) and place.get('label') and _place(place['label']) != _place(match[1]):
        return None
    label = str(obj.get('label') or '').strip()
    if label and label != match[2]:
        return None
    return dict(subject=subject_identity(record), place=_place(match[1]), label=match[2],
                entity=obj.get('entityId'), category=obj.get('category'))


def resolve_groups(groups, subject_identity):
    """Join explicitly referring groups into one unique original object group.

    Root index is computed once from the unmodified groups. Overfull buckets
    conservatively decline matching, bounding work with large account histories.
    Full rebuild and incremental rebuild use exactly the same current facts.
    """
    roots, index = {}, {}
    for topic, group in groups.items():
        root = _root(group, subject_identity)
        if root is not None:
            roots[topic] = root
            index.setdefault((root['subject'], root['place']), []).append(topic)
    moves = {}
    for topic, group in sorted(groups.items()):
        if topic in roots:
            continue
        target = None
        for record in group['records']:
            ref = _reference(record, subject_identity)
            if ref is None:
                target = None
                break
            bucket = index.get((ref['subject'], ref['place']), [])
            if len(bucket) > MAX_TARGETS_PER_PLACE:
                target = None
                break
            matches = [t for t in bucket if ref['label'] in roots[t]['labels']
                       and not (ref['entity'] and roots[t]['entities'] and ref['entity'] not in roots[t]['entities'])
                       and not (ref['category'] and roots[t]['categories'] and ref['category'] not in roots[t]['categories'])]
            if len(matches) != 1 or (target is not None and target != matches[0]):
                target = None
                break
            target = matches[0]
        if target is not None:
            moves[topic] = target
    # Evaluate additions together, without repeated all-pairs scans.
    by_target = {}
    for origin, target in sorted(moves.items()):
        by_target.setdefault(target, []).append(origin)
    accepted = {}
    for target, origins in by_target.items():
        entities, categories = set(roots[target]['entities']), set(roots[target]['categories'])
        for origin in origins:
            for record in groups[origin]['records']:
                obj = _object(record)
                if obj.get('entityId'):
                    entities.add(obj['entityId'])
                if obj.get('category'):
                    categories.add(obj['category'])
        if len(entities) <= 1 and len(categories) <= 1:
            accepted[target] = origins
    if not accepted:
        return groups
    result = deepcopy(groups)
    for target, origins in sorted(accepted.items()):
        for origin in origins:
            source = result.pop(origin)
            result[target]['records'].extend(source['records'])
            result[target]['bindings'].update(source['bindings'])
        # Include the complete current proof once, not once per input/partition.
        # Removing an anchor must invalidate partitions holding only additions.
        result[target]['objectLinks'] = dict(
            rule=RULE_VERSION, origins=origins, target=target,
            records=[{k: r.get(k) for k in ('memoryId', 'memoryVersionId', 'contentHash', 'citation')}
                     for r in sorted(result[target]['records'], key=lambda r: r['memoryId'])],
            bindings=sorted(result[target]['bindings'].values(), key=lambda b: b['memoryId']))
    return result
