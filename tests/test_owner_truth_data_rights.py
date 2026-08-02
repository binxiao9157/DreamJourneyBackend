from __future__ import annotations

import unittest

from app.services.owner_truth_data_rights import (
    count_owner_truth_data_rights_records,
    read_owner_truth_data_rights_records,
)


class OwnerTruthDataRightsTests(unittest.TestCase):
    def test_family_contribution_grant_projection_is_minimized_and_owner_scoped(self) -> None:
        queries: list[str] = []
        family_grant = {
            "grantId": "grant-owner-1",
            "vaultId": "vault-owner-1",
            "relationshipId": "relationship-owner-1",
            "relationshipEpoch": 3,
            "scope": "submitTextSource",
            "status": "revoked",
            "rowVersion": 2,
            "createdAt": "2026-07-31T08:00:00+00:00",
            "updatedAt": "2026-07-31T09:00:00+00:00",
            "revokedAt": "2026-07-31T09:00:00+00:00",
            "revocationReason": "ownerRequested",
        }

        def fetchall(query: str, params: tuple[str]) -> list[dict]:
            self.assertEqual(params, ("subject-owner",))
            if "owner_truth.family_contribution_grants" not in query:
                return []
            queries.append(query)
            return [{"payload": family_grant}]

        records = read_owner_truth_data_rights_records(
            subject_id="subject-owner",
            fetchall=fetchall,
        )

        self.assertEqual(records["familyContributionGrant"], [family_grant])
        self.assertEqual(len(queries), 1)
        query = queries[0]
        self.assertIn("family_grant.owner_subject_id = vault.owner_subject_id", query)
        self.assertNotIn(" AS grant", query)
        self.assertIn("LIMIT 1000", query)
        self.assertNotIn("contributor_subject_id", query)
        self.assertNotIn("create_command_id_hash", query)
        self.assertNotIn("create_payload_hash", query)
        self.assertNotIn("revoke_command_id_hash", query)
        self.assertNotIn("revoke_payload_hash", query)

    def test_family_contribution_grant_count_uses_owner_vault_boundary(self) -> None:
        queries: list[str] = []

        def fetchone(query: str, params: tuple[str]) -> dict:
            self.assertEqual(params, ("subject-owner",))
            if "owner_truth.family_contribution_grants" in query:
                queries.append(query)
                return {"count": 2}
            return {"count": 0}

        counts = count_owner_truth_data_rights_records(
            subject_id="subject-owner",
            fetchone=fetchone,
        )

        self.assertEqual(counts["ownerTruthFamilyContributionGrant"], 2)
        self.assertEqual(len(queries), 1)
        self.assertIn(
            "family_grant.owner_subject_id = vault.owner_subject_id",
            queries[0],
        )
        self.assertNotIn(" AS grant", queries[0])


if __name__ == "__main__":
    unittest.main()
