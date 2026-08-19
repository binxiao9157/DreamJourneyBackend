"""Vault-scoped export of current formal MemoryVersions as readable Markdown."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any, Mapping

from app.domain.owner_truth.ontology import OWNER_TRUTH_FACET_NAMES
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.data_export_jobs import (
    FORMAL_MEMORY_MARKDOWN_EXPORT_TYPE,
    DataExportJobStateError,
)
from app.services.owner_truth_formal_memory import (
    OwnerTruthFormalMemory,
    OwnerTruthFormalMemoryQuery,
    OwnerTruthFormalMemoryService,
)


FORMAL_MEMORY_MARKDOWN_EXPORT_SCHEMA_VERSION = 1
FORMAL_MEMORY_MARKDOWN_MIME_TYPE = "text/markdown; charset=utf-8"
FORMAL_MEMORY_MARKDOWN_MAX_RECORDS = 10_000
_BODY_FIELDS = ("title", "summary", "claim", "label", "body", "description")
_KIND_LABELS = {"experience": "经历", "knowledge": "知识", "emotion": "情感"}
_FACET_LABELS = {
    "people": "人物",
    "time": "时间",
    "places": "地点",
    "relationships": "关系",
    "emotions": "情绪",
    "values": "价值观",
    "personality": "性格",
}
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+.!|<>-])")


class FormalMemoryMarkdownExportError(ValueError):
    pass


def collect_current_formal_memories(
    store: Any,
    *,
    context: OwnerTruthCommandContext,
) -> tuple[OwnerTruthFormalMemory, ...]:
    service = OwnerTruthFormalMemoryService(store)
    cursor = None
    collected: list[OwnerTruthFormalMemory] = []
    while True:
        page = service.list(
            context=context,
            query=OwnerTruthFormalMemoryQuery(cursor=cursor, limit=100),
        )
        collected.extend(page.items)
        if len(collected) > FORMAL_MEMORY_MARKDOWN_MAX_RECORDS:
            raise FormalMemoryMarkdownExportError("formal memory export exceeds record limit")
        if page.next_cursor is None:
            break
        from app.services.owner_truth_formal_memory import OwnerTruthFormalMemoryCursor

        cursor = OwnerTruthFormalMemoryCursor.decode(page.next_cursor)
    return tuple(collected)


def render_formal_memory_markdown(
    memories: tuple[OwnerTruthFormalMemory, ...],
    *,
    generated_at: str,
) -> str:
    generated = _timestamp(generated_at)
    lines = [
        "# 我的正式记忆",
        "",
        f"> 导出时间：{_escape(generated)}",
        f"> 正式记忆数量：{len(memories)}",
        "",
    ]
    if not memories:
        lines.extend(["当前没有可导出的正式记忆。", ""])
        return "\n".join(lines)

    for index, memory in enumerate(memories, start=1):
        version = memory.current_version
        if version.status != "current":
            raise FormalMemoryMarkdownExportError("export contains a non-current MemoryVersion")
        content = version.content
        kind_label = _KIND_LABELS.get(memory.memory_kind, memory.memory_kind)
        title = _first_text(content.get("title")) or f"{kind_label}记忆 {index}"
        lines.extend(
            [
                f"## {index}. {_escape(title)}",
                "",
                f"- 类型：{_escape(kind_label)}",
                f"- 版本：{version.version_number}",
                f"- 记忆时间：{_escape(version.created_at)}",
                "",
                "### 正文",
                "",
            ]
        )
        body_values = []
        for field in _BODY_FIELDS:
            if field == "title":
                continue
            value = _first_text(content.get(field))
            if value and value not in body_values:
                body_values.append(value)
        if body_values:
            for value in body_values:
                lines.extend([_escape_multiline(value), ""])
        else:
            lines.extend(["（无正文）", ""])

        facets = content.get("facets")
        facet_lines = _render_facets(facets)
        if facet_lines:
            lines.extend(["### 已确认线索", "", *facet_lines, ""])
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def build_formal_memory_markdown_artifact(
    store: Any,
    *,
    context: OwnerTruthCommandContext,
    job_id: str,
    generated_at: str,
    expires_at: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    memories = collect_current_formal_memories(store, context=context)
    markdown = render_formal_memory_markdown(memories, generated_at=generated_at)
    data = markdown.encode("utf-8")
    content_hash = sha256(data).hexdigest()
    filename = _filename(generated_at)
    manifest = {
        "schemaVersion": FORMAL_MEMORY_MARKDOWN_EXPORT_SCHEMA_VERSION,
        "jobId": str(job_id),
        "exportType": FORMAL_MEMORY_MARKDOWN_EXPORT_TYPE,
        "scopeId": context.vault_id,
        "packageStatus": "ready",
        "generatedAt": _timestamp(generated_at),
        "expiresAt": _timestamp(expires_at),
        "fileName": filename,
        "mimeType": FORMAL_MEMORY_MARKDOWN_MIME_TYPE,
        "contentHash": content_hash,
        "byteCount": len(data),
        "memoryCount": len(memories),
    }
    artifact = {
        "schemaVersion": FORMAL_MEMORY_MARKDOWN_EXPORT_SCHEMA_VERSION,
        "exportType": FORMAL_MEMORY_MARKDOWN_EXPORT_TYPE,
        "scopeId": context.vault_id,
        "fileName": filename,
        "mimeType": FORMAL_MEMORY_MARKDOWN_MIME_TYPE,
        "contentHash": content_hash,
        "markdown": markdown,
    }
    return artifact, manifest, content_hash


def materialize_formal_memory_markdown_export_job(
    store: Any,
    *,
    job_id: str,
    context: OwnerTruthCommandContext,
    now: Any = None,
) -> dict[str, Any]:
    timestamp = _timestamp(now)
    claimed = store.claim_data_export_job(
        job_id,
        owner_user_id=context.owner_subject_id,
        updated_at=timestamp,
    )
    job = claimed.get("job")
    if not isinstance(job, Mapping):
        raise DataExportJobStateError("formal memory export job does not exist")
    _assert_job_scope(job, context=context)
    if claimed.get("outcome") != "claimed":
        return dict(job)
    try:
        artifact, manifest, content_hash = build_formal_memory_markdown_artifact(
            store,
            context=context,
            job_id=job_id,
            generated_at=timestamp,
            expires_at=str(job["expiresAt"]),
        )
        result = store.complete_data_export_job(
            job_id,
            owner_user_id=context.owner_subject_id,
            status="ready",
            artifact_hash=content_hash,
            artifact=artifact,
            manifest=manifest,
            ready_at=timestamp,
        )
        return dict(result["job"])
    except Exception:
        store.fail_data_export_job(
            job_id,
            owner_user_id=context.owner_subject_id,
            failure_code="formalMemoryMarkdownExportFailed",
            updated_at=timestamp,
        )
        raise


def formal_memory_markdown_download(job: Mapping[str, Any]) -> tuple[bytes, str, str]:
    artifact = job.get("artifact")
    if not isinstance(artifact, Mapping):
        raise FormalMemoryMarkdownExportError("formal memory export artifact is unavailable")
    markdown = artifact.get("markdown")
    filename = artifact.get("fileName")
    content_hash = artifact.get("contentHash")
    if not isinstance(markdown, str) or not isinstance(filename, str) or not isinstance(content_hash, str):
        raise FormalMemoryMarkdownExportError("formal memory export artifact is malformed")
    data = markdown.encode("utf-8")
    if sha256(data).hexdigest() != content_hash:
        raise FormalMemoryMarkdownExportError("formal memory export hash is invalid")
    return data, filename, content_hash


def _assert_job_scope(job: Mapping[str, Any], *, context: OwnerTruthCommandContext) -> None:
    if (
        job.get("exportType") != FORMAL_MEMORY_MARKDOWN_EXPORT_TYPE
        or job.get("scopeId") != context.vault_id
        or job.get("ownerUserId") != context.owner_subject_id
    ):
        raise DataExportJobStateError("formal memory export job scope does not match")


def _render_facets(value: Any) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    lines = []
    for name in OWNER_TRUTH_FACET_NAMES:
        entries = value.get(name)
        if not isinstance(entries, list):
            continue
        values = sorted(
            {
                str(entry.get("value") or "").strip()
                for entry in entries
                if isinstance(entry, Mapping) and str(entry.get("value") or "").strip()
            }
        )
        if values:
            rendered = "、".join(_escape(item) for item in values)
            lines.append(f"- {_FACET_LABELS[name]}：{rendered}")
    return lines


def _first_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _escape(value: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", str(value).replace("\x00", ""))


def _escape_multiline(value: str) -> str:
    return "  \n".join(_escape(line) for line in value.splitlines())


def _timestamp(value: Any) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise FormalMemoryMarkdownExportError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FormalMemoryMarkdownExportError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _filename(generated_at: str) -> str:
    parsed = datetime.fromisoformat(_timestamp(generated_at))
    return f"寻梦环游-正式记忆-{parsed.strftime('%Y%m%d-%H%M')}.md"


__all__ = [
    "FORMAL_MEMORY_MARKDOWN_EXPORT_SCHEMA_VERSION",
    "FORMAL_MEMORY_MARKDOWN_MIME_TYPE",
    "FormalMemoryMarkdownExportError",
    "build_formal_memory_markdown_artifact",
    "collect_current_formal_memories",
    "formal_memory_markdown_download",
    "materialize_formal_memory_markdown_export_job",
    "render_formal_memory_markdown",
]
