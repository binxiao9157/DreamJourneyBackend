import importlib.util
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
RUNNER = ROOT_DIR / "scripts/run-owner-truth-real-text-organization-quality.py"


def _load_runner_module():
    spec = importlib.util.spec_from_file_location(
        "owner_truth_real_text_organization_quality_runner",
        RUNNER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("quality runner could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _OrganizationProxySpy:
    def __init__(self) -> None:
        self.owner_calls: list[str] = []
        self.family_calls: list[str] = []

    def request_organization(self, *, text: str):
        self.owner_calls.append(text)
        return {"memories": [{"memoryKind": "knowledge"}]}

    def request_family_organization(self, *, text: str):
        self.family_calls.append(text)
        return {
            "memories": [
                {"memoryKind": "experience", "subjectRole": "memorySubject"},
                {"memoryKind": "emotion", "subjectRole": "reporterSelf"},
                {"memoryKind": "knowledge", "subjectRole": "unknown"},
            ]
        }


class OwnerTruthRealTextOrganizationQualityRunnerTests(unittest.TestCase):
    def test_runner_is_explicit_bounded_resumable_and_value_free(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")

        self.assertIn("OWNER_TRUTH_REAL_TEXT_ORGANIZATION_QUALITY_APPROVED", source)
        self.assertIn("--checkpoint", source)
        self.assertIn("--limit", source)
        self.assertIn("provider_error_case_result", source)
        self.assertIn("_provider_error_code(error)", source)
        self.assertIn('else "typed_schema"', source)
        self.assertIn('"modelId": model_id', source)
        self.assertIn('"promptVersion": prompt_version', source)
        self.assertIn('"corpusSha256": corpus_sha256', source)
        self.assertIn("checkpoint identity does not match this run", source)
        self.assertIn('case.category != "familyReport"', source)
        self.assertIn("request_family_organization", source)
        self.assertIn('memory.get("subjectRole") == "memorySubject"', source)
        self.assertIn('"responseContentRetained": False', source)
        self.assertIn('"privateVaultRead": False', source)
        self.assertNotIn("json.dumps(organization", source)
        self.assertNotIn("write_text(organization", source)
        self.assertNotIn("provider response", source.casefold())

    def test_provider_error_codes_do_not_retain_error_messages(self) -> None:
        runner = _load_runner_module()

        self.assertEqual(
            runner._provider_error_code(
                ValueError(
                    "organized text memory 0 violates typed schema: "
                    "private-material-must-not-appear"
                )
            ),
            "typed_schema_private",
        )

    def test_family_cases_use_family_prompt_and_exclude_non_subject_results(self) -> None:
        runner = _load_runner_module()
        proxy = _OrganizationProxySpy()

        result = runner._request_case_organization(
            proxy=proxy,
            case=SimpleNamespace(category="familyReport", fact_text="家属代录"),
        )

        self.assertEqual(proxy.family_calls, ["家属代录"])
        self.assertEqual(proxy.owner_calls, [])
        self.assertEqual(
            result,
            {
                "memories": [
                    {"memoryKind": "experience", "subjectRole": "memorySubject"}
                ]
            },
        )

    def test_owner_cases_keep_owner_prompt(self) -> None:
        runner = _load_runner_module()
        proxy = _OrganizationProxySpy()

        result = runner._request_case_organization(
            proxy=proxy,
            case=SimpleNamespace(category="school", fact_text="本人素材"),
        )

        self.assertEqual(proxy.owner_calls, ["本人素材"])
        self.assertEqual(proxy.family_calls, [])
        self.assertEqual(result, {"memories": [{"memoryKind": "knowledge"}]})

    def test_checkpoint_is_bound_to_model_prompt_and_corpus(self) -> None:
        runner = _load_runner_module()
        result = runner.OwnerTruthOrganizationQualityCaseResult(
            case_id="Q001",
            split="dev",
            category="school",
            candidate_count=1,
            supported_candidate_count=1,
            evidence_claim_count=1,
            supported_evidence_claim_count=1,
            anchor_recalled=True,
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            runner._write_checkpoint(
                path,
                {result.case_id: result},
                model_id="model-v1",
                prompt_version="prompt-v1",
                corpus_sha256="a" * 64,
            )

            loaded = runner._read_checkpoint(
                path,
                model_id="model-v1",
                prompt_version="prompt-v1",
                corpus_sha256="a" * 64,
            )
            self.assertEqual(set(loaded), {"Q001"})
            with self.assertRaisesRegex(ValueError, "identity does not match"):
                runner._read_checkpoint(
                    path,
                    model_id="model-v1",
                    prompt_version="prompt-v2",
                    corpus_sha256="a" * 64,
                )


if __name__ == "__main__":
    unittest.main()
