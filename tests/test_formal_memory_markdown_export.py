from __future__ import annotations

from hashlib import sha256
import unittest

from app.services.formal_memory_markdown_export import (
    FORMAL_MEMORY_MARKDOWN_MIME_TYPE,
    build_formal_memory_markdown_artifact,
    formal_memory_markdown_download,
    render_formal_memory_markdown,
)
from app.services.owner_truth_formal_memory import (
    OwnerTruthFormalMemory,
    OwnerTruthFormalMemoryVersion,
)


def _version(number: int, summary: str, *, status: str = "current") -> OwnerTruthFormalMemoryVersion:
    return OwnerTruthFormalMemoryVersion(
        version_id=f"00000000-0000-0000-0001-{number:012d}",
        version_number=number,
        status=status,
        decision="accepted",
        content_schema_version="owner-truth-v2",
        content_hash=sha256(summary.encode("utf-8")).hexdigest(),
        content={
            "summary": summary,
            "facets": {
                "people": [
                    {
                        "value": "外祖父",
                        "evidenceMode": "ownerStated",
                        "confidence": 1.0,
                        "providerDebug": "must-not-export",
                    }
                ],
                "time": [],
                "places": [{"value": "老院子", "evidenceMode": "inferred", "confidence": 0.8}],
                "relationships": [],
                "emotions": [],
                "values": [],
                "personality": [],
                "confidence": 0.9,
            },
            "sourceText": "must-not-export-source",
            "candidateId": "must-not-export-candidate",
            "internalAudit": "must-not-export-audit",
        },
        source_count=1,
        created_at=f"2026-08-{(number % 28) + 1:02d}T10:00:00+00:00",
    )


def _memory(index: int, summary: str) -> OwnerTruthFormalMemory:
    current = _version(index + 1, summary)
    history = _version(index, f"历史正文-{index}", status="superseded")
    return OwnerTruthFormalMemory(
        memory_id=f"00000000-0000-0000-0002-{index:012d}",
        memory_kind="experience",
        perspective_type="firstPerson",
        epistemic_status="recalled",
        sensitivity="standard",
        current_version=current,
        versions=(current, history),
    )


class FormalMemoryMarkdownExportTests(unittest.TestCase):
    def test_renderer_exports_only_current_readable_owner_content(self) -> None:
        markdown = render_formal_memory_markdown(
            (_memory(1, "在 #老院子 里听外祖父讲 <故事>。"),),
            generated_at="2026-08-19T08:09:00+00:00",
        )

        self.assertIn("# 我的正式记忆", markdown)
        self.assertIn(r"在 \#老院子 里听外祖父讲 \<故事\>。", markdown)
        self.assertIn("人物：外祖父", markdown)
        self.assertIn("地点：老院子", markdown)
        self.assertNotIn("历史正文", markdown)
        self.assertNotIn("must-not-export-source", markdown)
        self.assertNotIn("must-not-export-candidate", markdown)
        self.assertNotIn("must-not-export-audit", markdown)
        self.assertNotIn("providerDebug", markdown)
        self.assertNotIn("evidenceMode", markdown)
        self.assertNotIn("confidence", markdown)

    def test_empty_and_thousand_record_exports_are_stable(self) -> None:
        empty = render_formal_memory_markdown(
            (),
            generated_at="2026-08-19T08:09:00+00:00",
        )
        self.assertIn("正式记忆数量：0", empty)
        self.assertIn("当前没有可导出的正式记忆", empty)

        memories = tuple(_memory(index, f"第 {index} 条") for index in range(1_000))
        first = render_formal_memory_markdown(
            memories,
            generated_at="2026-08-19T08:09:00+00:00",
        )
        second = render_formal_memory_markdown(
            memories,
            generated_at="2026-08-19T08:09:00+00:00",
        )
        self.assertEqual(first, second)
        self.assertIn("正式记忆数量：1000", first)
        self.assertEqual(first.count("\n## "), 1_000)

    def test_artifact_download_revalidates_hash_and_mime(self) -> None:
        class Store:
            pass

        from unittest.mock import patch

        memory = _memory(1, "一段正式记忆")
        with patch(
            "app.services.formal_memory_markdown_export.collect_current_formal_memories",
            return_value=(memory,),
        ):
            artifact, manifest, content_hash = build_formal_memory_markdown_artifact(
                Store(),
                context=type(
                    "Context",
                    (),
                    {"vault_id": "vault-owner", "owner_subject_id": "owner"},
                )(),
                job_id="dej_00000000000000000000000000000000",
                generated_at="2026-08-19T08:09:00+00:00",
                expires_at="2026-08-19T08:24:00+00:00",
            )

        data, filename, downloaded_hash = formal_memory_markdown_download(
            {"artifact": artifact}
        )
        self.assertEqual(downloaded_hash, content_hash)
        self.assertEqual(sha256(data).hexdigest(), content_hash)
        self.assertEqual(manifest["mimeType"], FORMAL_MEMORY_MARKDOWN_MIME_TYPE)
        self.assertEqual(filename, "寻梦环游-正式记忆-20260819-0809.md")
        self.assertEqual(manifest["memoryCount"], 1)


if __name__ == "__main__":
    unittest.main()
