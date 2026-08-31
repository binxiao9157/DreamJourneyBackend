import json
import unittest
from pathlib import Path

from app.domain.narrative.contracts import (
    BookProjectState,
    BookProjectType,
    NARRATIVE_FIXTURE_SCHEMA_VERSION,
    NarrativeArtifactState,
    NarrativeArtifactType,
    NarrativeCommandEnvelope,
    NarrativeCommandType,
    NarrativeContractError,
    NarrativeErrorCode,
    NarrativeErrorEnvelope,
    NarrativeJobState,
    NarrativeMemoryRef,
    NarrativeNarratorType,
    require_timestamp,
    require_uuid,
)
from app.services.narrative_project import NarrativeProjectService


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "narrative" / "contract_v1.json"


class NarrativeContractsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_freezes_cross_platform_wire_vocabulary(self) -> None:
        enums = self.fixture["enums"]
        expected = {
            "projectTypes": BookProjectType,
            "narratorTypes": NarrativeNarratorType,
            "bookProjectStates": BookProjectState,
            "artifactTypes": NarrativeArtifactType,
            "artifactStates": NarrativeArtifactState,
            "commandTypes": NarrativeCommandType,
            "jobStates": NarrativeJobState,
            "errorCodes": NarrativeErrorCode,
        }
        self.assertEqual(self.fixture["schemaVersion"], NARRATIVE_FIXTURE_SCHEMA_VERSION)
        for fixture_key, enum_type in expected.items():
            self.assertEqual(enums[fixture_key], [member.value for member in enum_type])

    def test_fixture_ids_timestamps_and_versions_are_strict(self) -> None:
        sample = self.fixture["sample"]
        for object_name, id_field in (
            ("project", "projectId"),
            ("artifact", "artifactVersionId"),
            ("artifact", "memorySnapshotId"),
            ("job", "jobId"),
            ("job", "commandId"),
        ):
            self.assertEqual(
                require_uuid(sample[object_name][id_field], field=id_field),
                sample[object_name][id_field],
            )
        for object_name, time_field in (
            ("project", "updatedAt"),
            ("artifact", "createdAt"),
            ("job", "createdAt"),
        ):
            self.assertEqual(
                require_timestamp(sample[object_name][time_field], field=time_field),
                sample[object_name][time_field],
            )
        self.assertGreaterEqual(sample["project"]["projectVersion"], 0)
        self.assertGreaterEqual(sample["artifact"]["versionNumber"], 1)

    def test_command_and_error_samples_decode_to_typed_contracts(self) -> None:
        command = NarrativeCommandEnvelope.from_mapping(self.fixture["sample"]["command"])
        self.assertEqual(command.command_type, NarrativeCommandType.GENERATE_AUDITIONS)
        self.assertEqual(command.expected_project_version, 7)
        self.assertTrue(command.confirmed)

        error = NarrativeErrorEnvelope.from_mapping(self.fixture["sample"]["error"])
        self.assertEqual(error.error_code, NarrativeErrorCode.PROJECT_VERSION_CONFLICT)
        self.assertEqual(error.current_project_version, 8)
        self.assertTrue(error.retryable)

    def test_unknown_and_malformed_values_fail_closed(self) -> None:
        invalid = dict(self.fixture["sample"]["command"])
        invalid["commandType"] = "generateAudioBook"
        with self.assertRaises(NarrativeContractError):
            NarrativeCommandEnvelope.from_mapping(invalid)

        invalid = dict(self.fixture["sample"]["error"])
        invalid["currentProjectVersion"] = -1
        with self.assertRaises(NarrativeContractError):
            NarrativeErrorEnvelope.from_mapping(invalid)

    def test_transition_examples_use_only_frozen_states(self) -> None:
        state_types = {
            "bookProject": BookProjectState,
            "artifact": NarrativeArtifactState,
            "job": NarrativeJobState,
        }
        for group_name, enum_type in state_types.items():
            cases = self.fixture["transitionCases"][group_name]
            self.assertTrue(cases["allowed"])
            self.assertTrue(cases["rejected"])
            for source, destination in cases["allowed"] + cases["rejected"]:
                enum_type(source)
                enum_type(destination)
            allowed = {tuple(pair) for pair in cases["allowed"]}
            rejected = {tuple(pair) for pair in cases["rejected"]}
            self.assertTrue(allowed.isdisjoint(rejected))

    def test_fixture_contains_no_out_of_scope_authority_or_media_contract(self) -> None:
        serialized = json.dumps(self.fixture, ensure_ascii=False)
        for forbidden in (
            "audio",
            "voiceId",
            "publication",
            "providerKey",
            "localBodyAuthority",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_readiness_story_clusters_match_ios_wire_contract(self) -> None:
        clusters = NarrativeProjectService._story_clusters([
            NarrativeMemoryRef(
                memory_id="11111111-1111-4111-8111-111111111111",
                memory_version_id="22222222-2222-4222-8222-222222222222",
                content_hash="1" * 64,
                content={"text": "一段已确认的人生经历"},
                memory_kind="experience",
                perspective_type="ownerRecalled",
                epistemic_status="ownerConfirmed",
                sensitivity="normal",
            )
        ])

        self.assertEqual(len(clusters), 1)
        self.assertEqual(
            set(clusters[0]),
            {"clusterKey", "title", "memoryVersionIds", "itemCount"},
        )
        self.assertEqual(
            clusters[0]["memoryVersionIds"],
            ["22222222-2222-4222-8222-222222222222"],
        )
        self.assertEqual(clusters[0]["itemCount"], 1)


if __name__ == "__main__":
    unittest.main()
