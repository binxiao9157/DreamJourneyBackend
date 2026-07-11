from typing import Final


CANONICAL_SOURCE_KINDS: Final = frozenset(
    {
        "conversationTurn",
        "conversationPhoto",
        "memoryArchiveItem",
        "timeMailboxLetter",
        "kbLiteEntity",
        "memoir",
        "importRecord",
        "userAuthorization",
    }
)
LEGACY_SOURCE_KINDS: Final = frozenset(
    {
        "conversationSession",
        "archiveImageAnalysis",
    }
)

SOURCE_TITLE_BY_KIND: Final = {
    "conversationTurn": "对话来源",
    "conversationPhoto": "对话照片",
    "memoryArchiveItem": "档案素材",
    "conversationSession": "旧版对话会话来源",
    "archiveImageAnalysis": "旧版档案图像分析",
    "timeMailboxLetter": "时空信件",
    "kbLiteEntity": "知识条目",
    "memoir": "回忆录",
    "importRecord": "导入记录",
    "userAuthorization": "授权记录",
}


def source_ref_title(kind: str) -> str:
    return SOURCE_TITLE_BY_KIND.get(kind, "来源记录")


def source_ref_classification(kind: str) -> str:
    if kind in CANONICAL_SOURCE_KINDS:
        return "canonical"
    if kind in LEGACY_SOURCE_KINDS:
        return "legacy"
    return "unknown"
