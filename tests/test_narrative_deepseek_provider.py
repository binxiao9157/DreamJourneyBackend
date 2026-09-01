import json
import unittest
from hashlib import sha256
from uuid import uuid4

from app.core.config import Settings
from app.domain.narrative.contracts import (
    NarrativeJobState,
    NarrativeSelectionManifestRecord,
)
from app.services.narrative_deepseek import (
    DeepSeekNarrativeProvider,
    make_narrative_provider,
    narrative_provider_ready,
)
from app.services.narrative_generation import (
    NarrativeGenerationError,
    NarrativeGenerationProcessor,
)
from tests import test_narrative_generation as scenarios


def _settings(**overrides):
    values = {
        "store_backend": "postgres",
        "async_effect_v1_enabled": True,
        "async_effect_worker_enabled": True,
        "narrative_generation_worker_enabled": True,
        "narrative_generation_provider": "deepseek",
        "narrative_generation_model": "deepseek-chat",
        "narrative_generation_prompt_version": "narrative-test-prompt-v2",
        "narrative_generation_pipeline_version": "narrative-test-pipeline-v3",
        "deepseek_api_key": "fixture-key",
    }
    values.update(overrides)
    return Settings(**values)


class DeepSeekNarrativeProviderTests(unittest.TestCase):
    def test_request_contains_only_minimized_authorized_writing_context(self):
        _, _, project, ref, _ = scenarios._fixture()
        provider = DeepSeekNarrativeProvider(_settings(), transport=lambda *_: {})
        request = provider.build_request(
            stage="factualDraft",
            job_type="auditions",
            project=project,
            context={
                "writingContext": {
                    "primaryReader": "self",
                    "accessToken": "must-not-leave",
                },
                "memoryFacts": [{
                    "memoryVersionId": ref.memory_version_id,
                    "contentHash": ref.content_hash,
                    "memoryKind": ref.memory_kind,
                    "epistemicStatus": ref.epistemic_status,
                    "content": {
                        "event": "我在北方的一所大学学习计算机。",
                        "sourceId": "private-source-id",
                        "audioUrl": "private-audio-url",
                    },
                }],
                "inputPayload": {
                    "styleFeedback": "语气克制",
                    "candidateId": "private-candidate-id",
                },
                "supportingArtifacts": [{
                    "artifactVersionId": "artifact-1",
                    "artifactType": "goldenSample",
                    "artifactKey": "goldenSample",
                    "versionNumber": 1,
                    "state": "confirmed",
                    "contentText": "已经确认的书稿。",
                    "payload": {"secret": "must-not-leave"},
                }],
            },
            previous_output={"plan": {"objective": "写作"}},
        )

        body = request["json"]
        payload = json.loads(body["messages"][1]["content"])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(body["model"], "deepseek-chat")
        self.assertEqual(
            payload["formalMemories"][0]["memoryVersionId"],
            ref.memory_version_id,
        )
        self.assertIn("我在北方的一所大学学习计算机", serialized)
        self.assertIn("已经确认的书稿", serialized)
        self.assertIn("语气克制", serialized)
        for forbidden in (
            "must-not-leave",
            "private-source-id",
            "private-audio-url",
            "private-candidate-id",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("fixture-key", serialized)

    def test_nonretryable_transport_rejection_is_not_reclassified(self):
        _, _, project, _, _ = scenarios._fixture()

        def rejected(*_):
            raise NarrativeGenerationError("provider rejected fixture request")

        provider = DeepSeekNarrativeProvider(_settings(), transport=rejected)
        with self.assertRaisesRegex(
            NarrativeGenerationError,
            "provider rejected fixture request",
        ):
            provider.generate_stage(
                stage="storyPlan",
                job_type="auditions",
                project=project,
                context={},
                previous_output={},
            )

    def test_single_audition_repair_requests_only_the_failed_style(self):
        _, _, project, ref, _ = scenarios._fixture()
        calls = []

        def response_for(text):
            return {"choices": [{"message": {"content": json.dumps({
                "artifacts": [{
                    "key": "documentary",
                    "text": text,
                    "payload": {"paragraphs": [{
                        "paragraphId": "documentary-p1",
                        "text": text,
                        "memoryVersionIds": [ref.memory_version_id],
                    }]},
                }]
            }, ensure_ascii=False)}}]}

        def transport(_url, _headers, body, _timeout):
            calls.append(body)
            return response_for("我在北方求学。" * 30)

        provider = DeepSeekNarrativeProvider(_settings(), transport=transport)
        output = provider.repair_audition_artifact(
            project=project,
            context={"memoryFacts": []},
            expected_key="documentary",
            artifact={"key": "documentary", "text": "太短"},
            violation="audition_contract_invalid:textLength=2",
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(output["key"], "documentary")
        prompt = calls[0]["messages"][0]["content"]
        self.assertIn("本次只修复一篇", prompt)
        self.assertIn("documentary", prompt)
        self.assertIn("不得新增、删减", prompt)

    def test_final_auditions_normalize_provider_key_order_without_rewriting(self):
        _, _, project, ref, _ = scenarios._fixture()
        paragraph = "我在北方求学。" * 30

        def transport(_url, _headers, _body, _timeout):
            content = {
                "artifacts": [{
                    "key": key,
                    "text": paragraph,
                    "payload": {"paragraphs": [{
                        "paragraphId": f"{key}-p1",
                        "text": paragraph,
                        "memoryVersionIds": [ref.memory_version_id],
                    }]},
                } for key in ("thoughtfulMemoir", "documentary", "warmReflection")]
            }
            return {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}

        provider = DeepSeekNarrativeProvider(_settings(), transport=transport)
        output = provider.generate_stage(
            stage="antiAIEdit",
            job_type="auditions",
            project=project,
            context={},
            previous_output={},
        )

        self.assertEqual(
            [item["key"] for item in output["artifacts"]],
            ["documentary", "warmReflection", "thoughtfulMemoir"],
        )

    def test_audition_plan_selects_representative_memories_instead_of_all(self):
        _, _, project, _, _ = scenarios._fixture()
        provider = DeepSeekNarrativeProvider(_settings(), transport=lambda *_: {})

        request = provider.build_request(
            stage="storyPlan",
            job_type="auditions",
            project=project,
            context={},
            previous_output={},
        )

        prompt = request["json"]["messages"][0]["content"]
        self.assertIn("必须选择恰好 2 至 3 条", prompt)
        self.assertIn("不得改写、合并或补充记忆事实", prompt)

        factual_prompt = provider.build_request(
            stage="factualDraft",
            job_type="auditions",
            project=project,
            context={},
            previous_output={},
        )["json"]["messages"][0]["content"]
        self.assertIn("已由服务端按 selectionManifest 物理裁剪", factual_prompt)

        render_prompt = provider.build_request(
            stage="literaryRender",
            job_type="auditions",
            project=project,
            context={},
            previous_output={},
        )["json"]["messages"][0]["content"]
        self.assertIn("不可变 selectionManifest", render_prompt)

    def test_four_stage_adapter_commits_only_fact_guarded_artifacts(self):
        repo, _, project, _, job = scenarios._fixture(memory_count=4)
        snapshot = repo.get_snapshot(
            project_id=project.project_id,
            snapshot_id=job.memory_snapshot_id,
        )
        selected_ids = tuple(
            item.memory_version_id for item in snapshot.memory_refs[:2]
        )
        calls = []

        def transport(_url, _headers, body, _timeout):
            calls.append(body)
            prompt = body["messages"][0]["content"]
            if "本阶段只规划" in prompt:
                content = json.dumps({
                    "plan": {
                        "objective": "基于正式记忆完成试镜",
                        "structure": ["求学经历"],
                        "memoryVersionIds": list(selected_ids),
                        "materialGaps": [],
                        "risks": [],
                    }
                }, ensure_ascii=False)
            else:
                paragraph = (
                    "我在北方的一所大学学习计算机，这段经历构成了求学生活的重要部分。"
                    * 12
                )[:220]
                content = json.dumps({
                    "artifacts": [{
                        "key": key,
                        "text": paragraph,
                        "payload": {"paragraphs": [{
                            "paragraphId": f"{key}-p1",
                            "text": paragraph,
                            "memoryVersionIds": list(selected_ids),
                            "directQuote": False,
                            "uncertain": False,
                            "psychologyOrCausality": False,
                        }]},
                    } for key in (
                        "documentary",
                        "warmReflection",
                        "thoughtfulMemoir",
                    )]
                }, ensure_ascii=False)
            return {"choices": [{"message": {"content": content}}]}

        provider = DeepSeekNarrativeProvider(_settings(), transport=transport)
        result = NarrativeGenerationProcessor(repo, provider).run_job(
            project_id=project.project_id,
            job_id=job.job_id,
        )

        self.assertEqual(result.state, NarrativeJobState.READY_FOR_REVIEW)
        self.assertEqual(len(calls), 4)
        request_payloads = [
            json.loads(call["messages"][1]["content"])
            for call in calls
        ]
        self.assertEqual(len(request_payloads[0]["formalMemories"]), 4)
        self.assertEqual(
            [len(payload["formalMemories"]) for payload in request_payloads[1:]],
            [2, 2, 2],
        )
        self.assertEqual(
            {
                item["memoryVersionId"]
                for item in request_payloads[1]["formalMemories"]
            },
            set(selected_ids),
        )
        manifest = repo.get_selection_manifest(
            project_id=project.project_id,
            job_id=job.job_id,
        )
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.selected_memory_version_ids, selected_ids)
        artifacts = repo.list_artifacts(project_id=project.project_id)
        self.assertEqual(len(artifacts), 3)
        self.assertEqual({item.model_id for item in artifacts}, {"deepseek-chat"})
        self.assertEqual(
            {item.prompt_version for item in artifacts},
            {"narrative-test-prompt-v2"},
        )
        self.assertEqual(
            {item.pipeline_version for item in artifacts},
            {"narrative-test-pipeline-v3"},
        )

    def test_audition_keeps_valid_styles_when_one_style_cannot_be_repaired(self):
        repo, _, project, _, job = scenarios._fixture(memory_count=3)
        snapshot = repo.get_snapshot(
            project_id=project.project_id,
            snapshot_id=job.memory_snapshot_id,
        )
        selected_ids = tuple(
            item.memory_version_id for item in snapshot.memory_refs[:2]
        )

        def transport(_url, _headers, body, _timeout):
            prompt = body["messages"][0]["content"]
            if "本阶段只规划" in prompt:
                content = {"plan": {
                    "objective": "试镜",
                    "structure": [],
                    "memoryVersionIds": list(selected_ids),
                    "materialGaps": [],
                    "risks": [],
                }}
            elif "本次只修复一篇" in prompt:
                paragraph = "我记得那段经历，它构成了人生中清晰而具体的一页。" * 12
                paragraph = paragraph[:230]
                content = {"artifacts": [{
                    "key": "warmReflection",
                    "text": paragraph,
                    "payload": {"paragraphs": [{
                        "paragraphId": "warmReflection-p1",
                        "text": paragraph,
                        "memoryVersionIds": list(selected_ids[:1]),
                    }]},
                }]}
            else:
                paragraph = "我记得那段经历，它构成了人生中清晰而具体的一页。" * 12
                paragraph = paragraph[:230]
                content = {"artifacts": [{
                    "key": key,
                    "text": paragraph,
                    "payload": {"paragraphs": [{
                        "paragraphId": f"{key}-p1",
                        "text": paragraph,
                        "memoryVersionIds": list(
                            selected_ids[:1] if key == "warmReflection" else selected_ids
                        ),
                    }]},
                } for key in (
                    "documentary",
                    "warmReflection",
                    "thoughtfulMemoir",
                )]}
            return {"choices": [{"message": {
                "content": json.dumps(content, ensure_ascii=False)
            }}]}

        provider = DeepSeekNarrativeProvider(_settings(), transport=transport)
        result = NarrativeGenerationProcessor(repo, provider).run_job(
            project_id=project.project_id,
            job_id=job.job_id,
        )

        self.assertEqual(result.state, NarrativeJobState.FAILED)
        self.assertEqual(
            result.error_code,
            "audition_selection_mismatch:index=1,expectedCount=2,citedCount=1",
        )
        artifacts = repo.list_artifacts(project_id=project.project_id)
        self.assertEqual(
            {item.artifact_key for item in artifacts},
            {"documentary", "thoughtfulMemoir"},
        )
        self.assertTrue(all(
            item.payload.get("generationJobId") == job.job_id
            for item in artifacts
        ))

    def test_short_audition_is_repaired_individually_and_group_completes(self):
        repo, _, project, _, job = scenarios._fixture(memory_count=3)
        snapshot = repo.get_snapshot(
            project_id=project.project_id,
            snapshot_id=job.memory_snapshot_id,
        )
        selected_ids = tuple(
            item.memory_version_id for item in snapshot.memory_refs[:2]
        )
        calls = []

        def artifact(key, text):
            return {
                "key": key,
                "text": text,
                "payload": {"paragraphs": [{
                    "paragraphId": f"{key}-p1",
                    "text": text,
                    "memoryVersionIds": list(selected_ids),
                }]},
            }

        def transport(_url, _headers, body, _timeout):
            calls.append(body)
            prompt = body["messages"][0]["content"]
            if "本阶段只规划" in prompt:
                content = {"plan": {
                    "objective": "试镜",
                    "structure": [],
                    "memoryVersionIds": list(selected_ids),
                    "materialGaps": [],
                    "risks": [],
                }}
            elif "本次只修复一篇" in prompt:
                repaired = ("我记得那段经历，它构成了人生中清晰而具体的一页。" * 12)[:230]
                content = {"artifacts": [artifact("documentary", repaired)]}
            else:
                complete = ("我记得那段经历，它构成了人生中清晰而具体的一页。" * 12)[:230]
                content = {"artifacts": [
                    artifact("documentary", "内容太短"),
                    artifact("warmReflection", complete),
                    artifact("thoughtfulMemoir", complete),
                ]}
            return {"choices": [{"message": {
                "content": json.dumps(content, ensure_ascii=False)
            }}]}

        provider = DeepSeekNarrativeProvider(_settings(), transport=transport)
        result = NarrativeGenerationProcessor(repo, provider).run_job(
            project_id=project.project_id,
            job_id=job.job_id,
        )

        self.assertEqual(result.state, NarrativeJobState.READY_FOR_REVIEW)
        self.assertEqual(len(calls), 5)
        artifacts = repo.list_artifacts(project_id=project.project_id)
        self.assertEqual(len(artifacts), 3)
        self.assertEqual(
            {item.payload.get("generationJobId") for item in artifacts},
            {job.job_id},
        )

    def test_worker_resume_reuses_persisted_selection_without_replanning(self):
        repo, _, project, _, job = scenarios._fixture(memory_count=3)
        snapshot = repo.get_snapshot(
            project_id=project.project_id,
            snapshot_id=job.memory_snapshot_id,
        )
        selected_ids = tuple(
            item.memory_version_id for item in snapshot.memory_refs[:2]
        )
        selection_hash = sha256(
            json.dumps({
                "memorySnapshotId": job.memory_snapshot_id,
                "selectedMemoryVersionIds": list(selected_ids),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        repo.save_selection_manifest(NarrativeSelectionManifestRecord(
            manifest_id=str(uuid4()),
            project_id=project.project_id,
            job_id=job.job_id,
            memory_snapshot_id=job.memory_snapshot_id,
            selected_memory_version_ids=selected_ids,
            selection_hash=selection_hash,
            model_id="deepseek-chat",
            prompt_version="narrative-test-prompt-v2",
            created_at="2026-08-31T00:00:00+00:00",
        ))
        calls = []

        def transport(_url, _headers, body, _timeout):
            calls.append(body)
            paragraph = "我记得那段经历，它构成了人生中清晰而具体的一页。" * 12
            paragraph = paragraph[:230]
            content = {"artifacts": [{
                "key": key,
                "text": paragraph,
                "payload": {"paragraphs": [{
                    "paragraphId": f"{key}-p1",
                    "text": paragraph,
                    "memoryVersionIds": list(selected_ids),
                }]},
            } for key in (
                "documentary",
                "warmReflection",
                "thoughtfulMemoir",
            )]}
            return {"choices": [{"message": {
                "content": json.dumps(content, ensure_ascii=False)
            }}]}

        provider = DeepSeekNarrativeProvider(_settings(), transport=transport)
        result = NarrativeGenerationProcessor(repo, provider).run_job(
            project_id=project.project_id,
            job_id=job.job_id,
        )

        self.assertEqual(result.state, NarrativeJobState.READY_FOR_REVIEW)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(
            "本阶段只规划" not in call["messages"][0]["content"]
            for call in calls
        ))
        self.assertTrue(all(
            len(json.loads(call["messages"][1]["content"])["formalMemories"]) == 2
            for call in calls
        ))

    def test_factory_and_readiness_remain_fail_closed(self):
        disabled = _settings(
            narrative_generation_worker_enabled=False,
            narrative_generation_provider="disabled",
            narrative_generation_model="disabled",
            deepseek_api_key=None,
        )
        self.assertFalse(narrative_provider_ready(disabled))
        self.assertEqual(make_narrative_provider(disabled).model_id, "disabled")
        self.assertFalse(narrative_provider_ready(_settings(store_backend="memory")))
        self.assertFalse(
            narrative_provider_ready(_settings(async_effect_worker_enabled=False))
        )
        self.assertTrue(narrative_provider_ready(_settings()))
        self.assertIsInstance(
            make_narrative_provider(_settings()),
            DeepSeekNarrativeProvider,
        )


if __name__ == "__main__":
    unittest.main()
