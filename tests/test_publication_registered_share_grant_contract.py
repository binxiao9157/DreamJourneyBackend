from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import unittest
from uuid import uuid4

from fastapi import HTTPException

import app.main as main_module
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.in_memory_store import InMemoryStore
from app.services.publication_visitor_access import (
    InMemoryPublicationVisitorAccessRepository,
    PublicationGrantScope,
    PublicationVisitorEligibility,
    StaticPublicationVisitorEligibilityResolver,
)
from app.domain.publication.share_grant_session import (
    PublicationAdultVerificationState,
    PublicationVisitorRelationshipOrigin,
)
from app.services.user_identity import stable_user_id


class PublicationRegisteredShareGrantContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.store = InMemoryStore()
        self.repository = InMemoryPublicationVisitorAccessRepository()
        self.store._publication_visitor_access_repository = self.repository
        main_module.store = self.store
        owner = self.store.upsert_user(
            phone="13800139940",
            nickname="发布账户",
        )
        owner_id = str(owner["id"])
        self.context = OwnerTruthCommandContext(
            vault_id="publication-registered-grant-vault",
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        self.publication_id = str(uuid4())
        self.publication_version_id = str(uuid4())
        self.repository.seed_scope(
            PublicationGrantScope(
                vault_id=self.context.vault_id,
                owner_subject_id=self.context.owner_subject_id,
                authority_epoch=0,
                publication_id=self.publication_id,
                publication_version_id=self.publication_version_id,
                projection_state="active",
            )
        )
        self.repository._projection_content_reader = lambda publication_id, version_id: {
            "projectionState": "active",
            "displayTitle": "一起散步的下午",
            "displayBody": "这是本人确认后公开的内容。",
            "aiDisclosure": "内容经过 AI 辅助整理。",
            "projectionHash": "a" * 64,
            "publicCitationHash": "b" * 64,
        }

    def tearDown(self) -> None:
        main_module.store = self.previous_store

    def _payload(self, *, recipient_type: str, recipient_value: str) -> dict[str, object]:
        return {
            "commandId": str(uuid4()),
            "publicationId": self.publication_id,
            "publicationVersionId": self.publication_version_id,
            "recipient": {
                "type": recipient_type,
                "value": recipient_value,
            },
            "expiresAt": (
                datetime.now(timezone.utc) + timedelta(days=7) - timedelta(seconds=2)
            ).isoformat(),
        }

    @staticmethod
    def _json(response) -> dict[str, object]:
        return json.loads(response.body.decode("utf-8"))

    def test_phone_recipient_is_registered_masked_and_has_no_product_balance(self) -> None:
        phone = "13800139941"
        self.store.upsert_user(phone=phone, nickname="受邀账户")

        issued = self._json(
            main_module._publication_issue_grant_for_context(
                self.context,
                self._payload(recipient_type="phone", recipient_value=phone),
                product_contract=True,
            )
        )
        listed = self._json(
            main_module._publication_list_grants_for_context(
                self.context,
                product_contract=True,
            )
        )

        self.assertEqual(issued["schemaVersion"], "publication-owner-grant-issue-v1")
        self.assertEqual(issued["recipientDisplayLabel"], "手机号尾号 9941")
        self.assertFalse(issued["credentialIssued"])
        self.assertNotIn("grantCredential", issued)
        self.assertNotIn("useRemaining", issued)
        self.assertEqual(listed["schemaVersion"], "publication-owner-grant-list-v2")
        self.assertEqual(listed["grants"][0]["recipientDisplayLabel"], "手机号尾号 9941")
        self.assertNotIn("useRemaining", listed["grants"][0])
        serialized = json.dumps({"issued": issued, "listed": listed}, ensure_ascii=False)
        self.assertNotIn(phone, serialized)
        self.assertNotIn(stable_user_id(phone), serialized)

    def test_account_id_recipient_can_be_revoked_without_exposing_identity(self) -> None:
        phone = "13800139942"
        recipient = self.store.upsert_user(phone=phone, nickname="账户受邀人")
        recipient_id = str(recipient["id"])
        issued = self._json(
            main_module._publication_issue_grant_for_context(
                self.context,
                self._payload(recipient_type="accountId", recipient_value=recipient_id),
                product_contract=True,
            )
        )

        revoked = self._json(
            main_module._publication_revoke_grant_for_context(
                self.context,
                grant_id=str(issued["grantId"]),
                payload={"commandId": str(uuid4())},
            )
        )
        listed = self._json(
            main_module._publication_list_grants_for_context(
                self.context,
                product_contract=True,
            )
        )

        self.assertEqual(issued["recipientDisplayLabel"], f"账户 · {recipient_id[-6:]}")
        self.assertEqual(revoked["outcome"], "revoked")
        self.assertEqual(listed["grants"][0]["state"], "revoked")
        self.assertNotIn(recipient_id, json.dumps(listed, ensure_ascii=False))

    def test_unknown_or_self_recipient_fails_closed_with_same_public_error(self) -> None:
        cases = (
            ("phone", "13800139943"),
            ("accountId", self.context.owner_subject_id),
        )
        for recipient_type, recipient_value in cases:
            with self.subTest(recipient_type=recipient_type):
                with self.assertRaises(HTTPException) as raised:
                    main_module._publication_issue_grant_for_context(
                        self.context,
                        self._payload(
                            recipient_type=recipient_type,
                            recipient_value=recipient_value,
                        ),
                        product_contract=True,
                    )
                self.assertEqual(raised.exception.status_code, 409)
                self.assertEqual(
                    raised.exception.detail,
                    {"code": "publicationGrantRecipientUnavailable"},
                )

    def test_product_contract_rejects_client_supplied_use_limit_or_raw_user_id(self) -> None:
        phone = "13800139944"
        self.store.upsert_user(phone=phone, nickname="受邀账户")
        for forbidden_key, forbidden_value in (
            ("useLimit", 1),
            ("granteeUserId", stable_user_id(phone)),
        ):
            payload = self._payload(recipient_type="phone", recipient_value=phone)
            payload[forbidden_key] = forbidden_value
            with self.subTest(forbidden_key=forbidden_key):
                with self.assertRaises(HTTPException) as raised:
                    main_module._publication_issue_grant_for_context(
                        self.context,
                        payload,
                        product_contract=True,
                    )
                self.assertEqual(raised.exception.status_code, 400)

    def test_registered_recipient_lists_only_own_active_invitations_without_credentials(self) -> None:
        recipient_phone = "13800139945"
        other_phone = "13800139946"
        recipient = self.store.upsert_user(phone=recipient_phone, nickname="受邀账户")
        other = self.store.upsert_user(phone=other_phone, nickname="其他账户")
        issued = self._json(
            main_module._publication_issue_grant_for_context(
                self.context,
                self._payload(recipient_type="phone", recipient_value=recipient_phone),
                product_contract=True,
            )
        )

        invited = self._json(
            main_module._publication_list_invitations_for_subject(str(recipient["id"]))
        )
        unrelated = self._json(
            main_module._publication_list_invitations_for_subject(str(other["id"]))
        )

        self.assertEqual(invited["schemaVersion"], "publication-visitor-invitation-list-v1")
        self.assertEqual(len(invited["invitations"]), 1)
        summary = invited["invitations"][0]
        self.assertEqual(summary["grantId"], issued["grantId"])
        self.assertEqual(summary["title"], "一起散步的下午")
        self.assertEqual(summary["state"], "active")
        serialized = json.dumps(invited, ensure_ascii=False)
        for private_value in (
            recipient_phone,
            str(recipient["id"]),
            self.context.owner_subject_id,
            "grantCredential",
            "useRemaining",
            "vaultId",
        ):
            self.assertNotIn(private_value, serialized)
        self.assertEqual(unrelated["invitations"], [])

    def test_registered_recipient_admits_without_share_link_credential(self) -> None:
        recipient_phone = "13800139947"
        recipient = self.store.upsert_user(phone=recipient_phone, nickname="受邀账户")
        recipient_id = str(recipient["id"])
        issued = self._json(
            main_module._publication_issue_grant_for_context(
                self.context,
                self._payload(recipient_type="phone", recipient_value=recipient_phone),
                product_contract=True,
            )
        )
        previous_resolver = main_module.PUBLICATION_VISITOR_ELIGIBILITY_RESOLVER
        main_module.PUBLICATION_VISITOR_ELIGIBILITY_RESOLVER = StaticPublicationVisitorEligibilityResolver(
            {
                recipient_id: PublicationVisitorEligibility(
                    adult_verification=PublicationAdultVerificationState.VERIFIED,
                    relationship_origin=PublicationVisitorRelationshipOrigin.DIRECT,
                )
            }
        )
        try:
            admitted = self._json(
                main_module._publication_admit_visitor_for_subject(
                    recipient_id,
                    grant_id=str(issued["grantId"]),
                    payload={
                        "commandId": str(uuid4()),
                        "sessionCredential": "registered-session-" + "s" * 32,
                    },
                    product_contract=True,
                )
            )
        finally:
            main_module.PUBLICATION_VISITOR_ELIGIBILITY_RESOLVER = previous_resolver

        self.assertEqual(admitted["schemaVersion"], "publication-visitor-admission-v2")
        self.assertEqual(admitted["grantId"], issued["grantId"])
        self.assertNotIn("ownerSubjectId", admitted)
        self.assertNotIn("useRemaining", admitted)
        self.assertNotIn("grantCredential", admitted)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
