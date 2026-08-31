import json
import unittest

from app.core.config import Settings
from app.domain.narrative.contracts import NarrativeJobState
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

    def test_final_auditions_receive_one_bounded_format_repair(self):
        _, _, project, ref, _ = scenarios._fixture()
        calls = []

        def response_for(text):
            return {"choices": [{"message": {"content": json.dumps({
                "artifacts": [{
                    "key": key,
                    "text": text,
                    "payload": {"paragraphs": [{
                        "paragraphId": f"{key}-p1",
                        "text": text,
                        "memoryVersionIds": [ref.memory_version_id],
                    }]},
                } for key in ("documentary", "warmReflection", "thoughtfulMemoir")]
            }, ensure_ascii=False)}}]}

        def transport(_url, _headers, body, _timeout):
            calls.append(body)
            text = "我在北方求学。" * (8 if len(calls) == 1 else 30)
            return response_for(text)

        provider = DeepSeekNarrativeProvider(_settings(), transport=transport)
        output = provider.generate_stage(
            stage="antiAIEdit",
            job_type="auditions",
            project=project,
            context={"memoryFacts": []},
            previous_output={},
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(provider._audition_contract_violations(output), [])
        self.assertIn("未通过最终格式校验", calls[1]["messages"][0]["content"])
        self.assertIn("允许省略次要事实", calls[1]["messages"][0]["content"])
        self.assertIn("lengthMismatch:1:", calls[1]["messages"][0]["content"])

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
        self.assertIn("2 至 3 条代表性记忆", prompt)
        self.assertIn("不要试图覆盖全部人生材料", prompt)

        factual_prompt = provider.build_request(
            stage="factualDraft",
            job_type="auditions",
            project=project,
            context={},
            previous_output={},
        )["json"]["messages"][0]["content"]
        self.assertIn("previousStageOutput.plan.memoryVersionIds", factual_prompt)

        render_prompt = provider.build_request(
            stage="literaryRender",
            job_type="auditions",
            project=project,
            context={},
            previous_output={},
        )["json"]["messages"][0]["content"]
        self.assertIn("previousStageOutput.artifacts", render_prompt)

    def test_four_stage_adapter_commits_only_fact_guarded_artifacts(self):
        repo, _, project, ref, job = scenarios._fixture()
        calls = []

        def transport(_url, _headers, body, _timeout):
            calls.append(body)
            prompt = body["messages"][0]["content"]
            if "本阶段只规划" in prompt:
                content = json.dumps({
                    "plan": {
                        "objective": "基于正式记忆完成试镜",
                        "structure": ["求学经历"],
                        "memoryVersionIds": [ref.memory_version_id],
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
                            "memoryVersionIds": [ref.memory_version_id],
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
