"""Derived reference boundaries for authorized formal facts; no reads or writes.

A scope identifies an evidence slice, not an entity. Never infer an antecedent
from proximity, a shared Source alone, a topic title or an unconfirmed turn.
"""
from hashlib import sha256
import json
import re
from typing import Any, Mapping

REFERENCE_RULE = (
    "记忆的referenceContext仅是指代边界，不是新事实。scopes相同只表示同一证据片段，不证明同一物件。"
    "不能把孤立代词（它、这个等）套到当前问题的物件上，也不能因动作相似或记录相邻就合并归属。"
    "仅可依据本条明确表述，或相同非空scope内已提供且唯一明确的先行词理解指代；多物件、人物或时期不明时不猜。"
    "objectLabel保留原字段，可能本身是代词或地点，不等于已解析的物件身份。"
    "缺少先行词的片段不要用于推断具体物件；其他明确事实仍正常回答。"
)
_REFERENCE = re.compile(r'它|他们|她们|他|她|这(?:个|些|本|把|只|件|种)|那(?:个|些|本|把|只|件|种)|该(?:物|人|物件)')
_MAX_REFS = 4


def reference_context(entry: Mapping[str, Any]) -> dict[str, Any]:
    content = entry.get('content')
    content = content if isinstance(content, Mapping) else {}
    obj = content.get('object')
    label = obj.get('label') if isinstance(obj, Mapping) else None
    label = label if isinstance(label, str) and len(label) <= 256 else None
    semantic = content.get('semantic')
    text = next((v for v in (content.get('statement'), content.get('event'), content.get('summary'),
        content.get('claim'), content.get('expression'),
        semantic.get('narrative') if isinstance(semantic, Mapping) else None) if isinstance(v, str) and v), '')
    subjects = [content.get('memorySubjectId'), content.get('claimSubjectId')]
    subjects = [s if isinstance(s, str) and len(s) <= 256 else None for s in subjects]
    refs = entry.get('evidenceRefs')
    scopes = set()
    if isinstance(refs, list) and 0 < len(refs) <= _MAX_REFS:
        for ref in refs:
            if not isinstance(ref, Mapping):continue
            source, version = ref.get('sourceId'), ref.get('sourceVersion')
            if not isinstance(source, str) or not source or len(source) > 160 or type(version) is not int or version < 1:continue
            span, turn = ref.get('span'), ref.get('turnId')
            locator = None
            if isinstance(span, Mapping) and type(span.get('start')) is int and type(span.get('end')) is int and 0 <= span['start'] < span['end']:
                locator = ['span',span['start'],span['end']]
            elif isinstance(turn, str) and 0 < len(turn) <= 160:
                locator = ['turn',turn]
            if locator is not None:
                # Subject is part of the scope so equal offsets never bridge people.
                value = json.dumps([source,version,locator,subjects],ensure_ascii=False,separators=(',',':'))
                scopes.add(sha256(value.encode()).hexdigest())
    referential = bool(_REFERENCE.search(text) or _REFERENCE.search(label or ''))
    if not scopes and label is None and not referential:
        return {}
    return dict(scopes=sorted(scopes),objectLabel=label,referential=referential)


def render_reference_context(entry: Mapping[str, Any]) -> str:
    return 'referenceContext=' + json.dumps(reference_context(entry),ensure_ascii=False,separators=(',',':'))
