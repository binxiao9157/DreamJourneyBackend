import unittest

from app.core.config import Settings
from app.services.context_packet import ContextPacketBuilder
from app.services.in_memory_store import InMemoryStore


class ContextPacketPersonaPolicyTests(unittest.TestCase):
    user_id = "persona-policy-owner"

    def setUp(self):
        self.store = InMemoryStore()
        self.builder = ContextPacketBuilder(
            self.store,
            Settings(store_backend="memory"),
        )

    def _build(self, facts, *, persona_scope="personal", digital_human_id=None):
        self.store.save_kb_snapshot(
            self.user_id,
            {"people": [], "places": [], "events": [], "facts": facts},
        )
        return self.builder.build(
            {
                "userId": self.user_id,
                "intent": "echo_chat",
                "query": "persona policy",
                "personaScope": persona_scope,
                "digitalHumanId": digital_human_id or self.user_id,
            }
        )

    @staticmethod
    def _fact(fact_id, **overrides):
        fact = {
            "id": fact_id,
            "statement": f"Statement for {fact_id}",
            "confidence": "high",
            "privacyMetadata": {"scope": "generationAllowed"},
        }
        fact.update(overrides)
        return fact

    @staticmethod
    def _kb_ref_sets(packet):
        memory = {item["id"] for item in packet["memory"]["kbFacts"]}
        selected = {
            item["refId"]
            for item in packet["selectedContext"]
            if item["source"] == "kbFact"
        }
        generation = {
            item["refId"]
            for item in packet["generationContext"]["sourceRefs"]
            if item["source"] == "kbFact"
        }
        return memory, selected, generation

    def test_personal_allows_legacy_and_self_but_filters_explicit_mismatches(self):
        packet = self._build(
            [
                self._fact("legacy"),
                self._fact(
                    "self-match",
                    ownerUserId=self.user_id,
                    personaScope="self",
                    digitalHumanId=self.user_id,
                    evidenceStatus="observed",
                ),
                self._fact(
                    "owner-mismatch",
                    ownerUserId="another-owner",
                    personaScope="personal",
                    digitalHumanId=self.user_id,
                    evidenceStatus="confirmed",
                ),
                self._fact(
                    "scope-mismatch",
                    ownerUserId=self.user_id,
                    personaScope="family",
                    digitalHumanId=self.user_id,
                    evidenceStatus="confirmed",
                ),
                self._fact(
                    "digital-human-mismatch",
                    ownerUserId=self.user_id,
                    personaScope="personal",
                    digitalHumanId="another-persona",
                    evidenceStatus="confirmed",
                ),
            ]
        )

        expected = {"legacy", "self-match"}
        self.assertEqual(self._kb_ref_sets(packet), (expected, expected, expected))
        reasons = {item["refId"]: item["reason"] for item in packet["filteredContext"]}
        self.assertEqual(reasons["owner-mismatch"], "kb_fact_owner_user_id_mismatch")
        self.assertEqual(reasons["scope-mismatch"], "kb_fact_persona_scope_mismatch")
        self.assertEqual(
            reasons["digital-human-mismatch"],
            "kb_fact_digital_human_id_mismatch",
        )

    def test_family_requires_complete_matching_entity_metadata(self):
        target = "family-persona-target"
        packet = self._build(
            [
                self._fact(
                    "family-match",
                    ownerUserId=self.user_id,
                    personaScope="family",
                    digitalHumanId=target,
                    evidenceStatus="confirmed",
                ),
                self._fact(
                    "family-missing-owner",
                    personaScope="family",
                    digitalHumanId=target,
                    evidenceStatus="observed",
                ),
                self._fact(
                    "family-owner-mismatch",
                    ownerUserId="another-owner",
                    personaScope="family",
                    digitalHumanId=target,
                    evidenceStatus="observed",
                ),
                self._fact(
                    "family-scope-mismatch",
                    ownerUserId=self.user_id,
                    personaScope="personal",
                    digitalHumanId=target,
                    evidenceStatus="observed",
                ),
                self._fact(
                    "family-digital-human-mismatch",
                    ownerUserId=self.user_id,
                    personaScope="family",
                    digitalHumanId="another-target",
                    evidenceStatus="observed",
                ),
                self._fact(
                    "family-missing-evidence",
                    ownerUserId=self.user_id,
                    personaScope="family",
                    digitalHumanId=target,
                ),
            ],
            persona_scope="family",
            digital_human_id=target,
        )

        expected = {"family-match"}
        self.assertEqual(self._kb_ref_sets(packet), (expected, expected, expected))
        self.assertIsNone(packet["persona"]["viewerFamilyMemberID"])
        reasons = {item["refId"]: item["reason"] for item in packet["filteredContext"]}
        self.assertEqual(
            reasons["family-missing-owner"],
            "kb_fact_family_metadata_missing",
        )
        self.assertEqual(
            reasons["family-owner-mismatch"],
            "kb_fact_owner_user_id_mismatch",
        )
        self.assertEqual(
            reasons["family-scope-mismatch"],
            "kb_fact_persona_scope_mismatch",
        )
        self.assertEqual(
            reasons["family-digital-human-mismatch"],
            "kb_fact_digital_human_id_mismatch",
        )
        self.assertEqual(
            reasons["family-missing-evidence"],
            "kb_fact_evidence_status_not_allowed",
        )

    def test_evidence_confidence_and_privacy_policy_share_all_output_paths(self):
        packet = self._build(
            [
                self._fact("observed", evidenceStatus="observed"),
                self._fact("confirmed", evidenceStatus="confirmed"),
                self._fact("legacy-without-evidence"),
                self._fact("candidate", evidenceStatus="candidate"),
                self._fact("rejected", evidenceStatus="rejected"),
                self._fact("superseded", evidenceStatus="superseded"),
                self._fact("low-confidence", confidence="low", evidenceStatus="observed"),
                self._fact(
                    "not-generation-allowed",
                    evidenceStatus="observed",
                    privacyMetadata={"scope": "familyCircle"},
                ),
            ]
        )

        expected = {"observed", "confirmed", "legacy-without-evidence"}
        self.assertEqual(self._kb_ref_sets(packet), (expected, expected, expected))
        reasons = {item["refId"]: item["reason"] for item in packet["filteredContext"]}
        for ref_id in ("candidate", "rejected", "superseded"):
            self.assertEqual(reasons[ref_id], "kb_fact_evidence_status_not_allowed")
        self.assertEqual(reasons["low-confidence"], "kb_fact_low_confidence")
        self.assertEqual(
            reasons["not-generation-allowed"],
            "kb_fact_privacy_scope_not_generation_allowed",
        )


if __name__ == "__main__":
    unittest.main()
