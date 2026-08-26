from __future__ import annotations

from copy import deepcopy
import unittest

from app.domain.owner_truth.person_memory_model import build_person_memory_model


def _entry(
    *,
    suffix: int,
    kind: str,
    content: dict[str, object],
    schema_version: str = "owner-truth-v2",
) -> dict[str, object]:
    return {
        "memoryId": f"00000000-0000-0000-0000-{suffix:012d}",
        "memoryVersionId": f"10000000-0000-0000-0000-{suffix:012d}",
        "memoryVersion": 1,
        "memoryKind": kind,
        "epistemicStatus": "recalled",
        "sensitivity": "standard",
        "contentSchemaVersion": schema_version,
        "contentHash": f"{suffix:064x}"[-64:],
        "content": content,
        "sourceId": f"20000000-0000-0000-0000-{suffix:012d}",
        "sourceVersion": 1,
        "evidenceRefs": [
            {
                "sourceId": f"20000000-0000-0000-0000-{suffix:012d}",
                "sourceVersion": 1,
            }
        ],
    }


def _facets(**values: list[str]) -> dict[str, object]:
    names = (
        "people",
        "time",
        "places",
        "relationships",
        "emotions",
        "values",
        "personality",
        "habits",
        "goals",
        "identity",
        "reflections",
    )
    return {
        **{
            name: [
                {"value": value, "evidenceMode": "ownerStated", "confidence": 1.0}
                for value in values.get(name, [])
            ]
            for name in names
        },
        "confidence": 1.0,
    }


class OwnerTruthPersonMemoryModelTests(unittest.TestCase):
    def test_legacy_formal_memories_become_multi_facet_versioned_projections(self) -> None:
        entries = [
            _entry(
                suffix=1,
                kind="experience",
                content={
                    "summary": "小时候我常和外祖父在老院子里听雨",
                    "facets": _facets(
                        people=["外祖父"],
                        places=["老院子"],
                        relationships=["祖孙"],
                        emotions=["安心"],
                        values=["重视家人"],
                    ),
                },
            ),
            _entry(
                suffix=2,
                kind="knowledge",
                content={
                    "claim": "遇到复杂事情时，我会先拆分再逐项核对",
                    "facets": _facets(values=["重视家人"], habits=["逐项核对"]),
                },
            ),
        ]

        model = build_person_memory_model(entries)

        self.assertEqual(model["state"], "ready")
        self.assertEqual(model["memoryCount"], 2)
        formal = model["formalMemories"]
        self.assertEqual(formal[0]["primaryKind"], "lifeEvent")
        self.assertEqual(
            set(formal[0]["facets"]),
            {"lifeEvent", "emotion", "relationship", "value"},
        )
        self.assertEqual(formal[1]["primaryKind"], "knowledge")
        self.assertIn("habit", formal[1]["facets"])
        self.assertEqual(len(model["relationshipProjection"]["relations"]), 2)
        self.assertEqual(model["biographyProjection"]["supportingMemoryCount"], 2)
        evidence = [
            citation
            for section in model["biographyProjection"]["sections"]
            for citation in section["evidence"]
        ]
        self.assertEqual(
            {item["memoryVersionId"] for item in evidence},
            {entry["memoryVersionId"] for entry in entries},
        )
        mental_models = model["cognitiveProjection"]["mentalModels"]
        self.assertEqual(len(mental_models), 1)
        self.assertEqual(mental_models[0]["epistemicStatus"], "inferred")
        self.assertEqual(len(mental_models[0]["evidence"]), 2)

    def test_replacing_current_version_refreshes_the_whole_document(self) -> None:
        original = _entry(
            suffix=3,
            kind="experience",
            content={"summary": "我第一次独自去上海工作", "facets": _facets()},
        )
        first = build_person_memory_model([original])
        corrected = deepcopy(original)
        corrected["memoryVersionId"] = "10000000-0000-0000-0000-000000000103"
        corrected["memoryVersion"] = 2
        corrected["contentHash"] = "f" * 64
        corrected["content"] = {
            "summary": "我第一次独自去杭州工作",
            "facets": _facets(places=["杭州"]),
        }

        second = build_person_memory_model([corrected])

        self.assertNotEqual(first["sourceFingerprint"], second["sourceFingerprint"])
        self.assertNotEqual(first["modelVersion"], second["modelVersion"])
        self.assertNotEqual(
            first["biographyProjection"]["documentVersion"],
            second["biographyProjection"]["documentVersion"],
        )
        document_text = " ".join(
            block["text"]
            for section in second["biographyProjection"]["sections"]
            for block in section["blocks"]
        )
        self.assertIn("杭州", document_text)
        self.assertNotIn("上海", document_text)
        self.assertEqual(
            second["formalMemories"][0]["memoryVersionId"],
            corrected["memoryVersionId"],
        )

    def test_empty_model_is_stable_and_value_free(self) -> None:
        first = build_person_memory_model([])
        second = build_person_memory_model([])

        self.assertEqual(first, second)
        self.assertEqual(first["state"], "empty")
        self.assertEqual(first["memoryCount"], 0)
        self.assertEqual(first["biographyProjection"]["sections"], [])
        self.assertEqual(first["relationshipProjection"]["relations"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
