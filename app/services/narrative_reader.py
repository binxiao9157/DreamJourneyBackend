"""Fail-closed reader and ephemeral export over current narrative artifacts."""

from __future__ import annotations

import json
from typing import Any, Mapping

from app.domain.narrative.contracts import (
    NarrativeArtifactRecord,
    NarrativeArtifactState,
    NarrativeArtifactType,
    NarrativeScope,
)
from app.services.narrative_project import NarrativeRepository


class NarrativeReaderError(ValueError):
    pass


class NarrativeReaderService:
    def __init__(self, repository: NarrativeRepository) -> None:
        self.repository = repository

    def manifest(self, *, scope: NarrativeScope, project_id: str) -> Mapping[str, Any]:
        project = self.repository.get_project(scope=scope, project_id=project_id)
        chapters = self._current_chapters(project_id)
        return {
            "schemaVersion": "narrative-reader-manifest-v1",
            "projectId": project.project_id,
            "vaultId": project.scope.vault_id,
            "title": project.title,
            "projectType": project.project_type.value,
            "chapters": [self._chapter_summary(item) for item in chapters],
        }

    def chapter(
        self, *, scope: NarrativeScope, project_id: str, chapter_key: str
    ) -> Mapping[str, Any]:
        self.repository.get_project(scope=scope, project_id=project_id)
        chapter = next(
            (item for item in self._current_chapters(project_id) if item.artifact_key == chapter_key),
            None,
        )
        if chapter is None:
            raise NarrativeReaderError("readable chapter was not found")
        paragraphs = chapter.payload.get("paragraphs")
        if not isinstance(paragraphs, list) or not paragraphs:
            paragraphs = [
                {
                    "paragraphId": f"{chapter.artifact_key}-body",
                    "text": chapter.content_text or "",
                }
            ]
        return {
            "schemaVersion": "narrative-reader-chapter-v1",
            "projectId": project_id,
            "chapterKey": chapter.artifact_key,
            "chapterVersionId": chapter.artifact_version_id,
            "title": str(chapter.payload.get("title") or chapter.artifact_key),
            "paragraphs": [
                {
                    "paragraphId": str(item.get("paragraphId") or f"p-{index + 1}"),
                    "text": str(item.get("text") or ""),
                }
                for index, item in enumerate(paragraphs)
                if isinstance(item, Mapping) and str(item.get("text") or "").strip()
            ],
            "contentHash": chapter.content_hash,
        }

    def export(
        self, *, scope: NarrativeScope, project_id: str, format: str
    ) -> tuple[str, str]:
        manifest = self.manifest(scope=scope, project_id=project_id)
        chapters = [
            self.chapter(scope=scope, project_id=project_id, chapter_key=item["chapterKey"])
            for item in manifest["chapters"]
        ]
        if format == "json":
            value = {"schemaVersion": "narrative-export-v1", "manifest": manifest, "chapters": chapters}
            return json.dumps(value, ensure_ascii=False, indent=2), "application/json"
        if format != "txt":
            raise NarrativeReaderError("export format must be txt or json")
        lines = [str(manifest["title"]), ""]
        for chapter in chapters:
            lines.extend([str(chapter["title"]), ""])
            lines.extend(str(item["text"]) for item in chapter["paragraphs"])
            lines.append("")
        return "\n\n".join(lines).strip() + "\n", "text/plain; charset=utf-8"

    def _current_chapters(self, project_id: str) -> list[NarrativeArtifactRecord]:
        candidates = self.repository.list_artifacts(
            project_id=project_id, artifact_type=NarrativeArtifactType.CHAPTER
        )
        current: dict[str, NarrativeArtifactRecord] = {}
        for item in candidates:
            if item.state not in {
                NarrativeArtifactState.CONFIRMED,
                NarrativeArtifactState.FINAL,
            }:
                continue
            prior = current.get(item.artifact_key)
            if prior is None or item.version_number > prior.version_number:
                current[item.artifact_key] = item
        return sorted(
            current.values(),
            key=lambda item: (
                int(item.payload.get("order", 2**31 - 1)),
                item.artifact_key,
            ),
        )

    @staticmethod
    def _chapter_summary(chapter: NarrativeArtifactRecord) -> Mapping[str, Any]:
        return {
            "chapterKey": chapter.artifact_key,
            "chapterVersionId": chapter.artifact_version_id,
            "title": str(chapter.payload.get("title") or chapter.artifact_key),
            "order": int(chapter.payload.get("order", 0)),
            "contentHash": chapter.content_hash,
        }


__all__ = ["NarrativeReaderError", "NarrativeReaderService"]
