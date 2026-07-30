from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts/backend-owner-truth-postgres-smoke.py"


class OwnerTruthPostgresSmokeContractTests(unittest.TestCase):
    def test_conversation_repository_is_created_inside_each_unit_of_work(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")

        self.assertNotIn(
            "conversation_service = OwnerTruthConversationService(\n"
            "                store.owner_truth_conversation_repository()\n"
            "            )",
            source,
        )
        self.assertEqual(source.count("store.owner_truth_conversation_repository()"), 2)
        start_uow = source.index('command_id="ownerTruthInterviewTurnContextStart"')
        start_repository = source.index("store.owner_truth_conversation_repository()", start_uow)
        append_uow = source.index('command_id="ownerTruthInterviewTurnContextAppend"')
        append_repository = source.index(
            "store.owner_truth_conversation_repository()",
            append_uow,
        )
        self.assertLess(start_uow, start_repository)
        self.assertLess(append_uow, append_repository)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
