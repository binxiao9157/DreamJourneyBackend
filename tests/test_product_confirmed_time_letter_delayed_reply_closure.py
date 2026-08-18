import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService
from scripts.dispatch_due_time_letters import product_closed_summary


client = TestClient(app)


class ProductConfirmedTimeLetterDelayedReplyClosureTests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        main_module.store = InMemoryStore()
        service = ReleasePolicyService(
            authenticated_owner_v4_enabled=True,
            enforce_default_closed_stages=False,
        )
        main_module.RELEASE_POLICY_SERVICE = service
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(service)

    def tearDown(self):
        main_module.store = self.previous_store
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate

    def assert_product_closed(self, response, feature: str):
        self.assertEqual(response.status_code, 403, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "release_policy_denied")
        self.assertEqual(detail["feature"], feature)
        self.assertEqual(detail["reason"], "productClosed")
        self.assertFalse(detail["retryable"])

    def machine_post(self, path: str, payload: dict):
        token = "pc-00-02-machine-token"
        with patch.object(main_module, "BACKEND_API_TOKEN", token):
            return client.post(
                path,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )

    def test_policy_keeps_both_features_closed_for_every_rollout_input(self):
        service = ReleasePolicyService(
            authenticated_owner_v4_enabled=True,
            enforce_default_closed_stages=False,
        )

        for feature in ("timeLetters", "echoDelayedReplies"):
            for audience, cohort in (
                ("owner", "authenticatedOwner"),
                ("owner", "closedPilotAdultSelf"),
                ("qa", "internalQA"),
            ):
                decision = service.build_snapshot(
                    audience=audience,
                    cohort=cohort,
                    client_build=999,
                    requested_feature=feature,
                ).features[0]
                self.assertFalse(decision.enabled, (feature, audience, cohort))
                self.assertFalse(decision.releaseVisible, (feature, audience, cohort))
                self.assertEqual(decision.reason, "productClosed")
            self.assertEqual(service.command_mode_for(feature), "enforce")

        self.assertTrue(
            {"timeLetters", "echoDelayedReplies"}.issubset(
                service.public_descriptor()["productClosedFeatures"]
            )
        )

    def test_route_gate_blocks_new_writes_and_dispatch_but_preserves_old_reads(self):
        gate = ReleasePolicyCommandGate(ReleasePolicyService())

        self.assertEqual(
            gate.feature_for_request("POST", "/archive/items", {"kind": "timeLetter"}),
            "timeLetters",
        )
        self.assertIsNone(gate.feature_for_request("POST", "/mailbox/letters", {}))
        self.assertEqual(
            gate.feature_for_request("POST", "/archive/time-letters/dispatch-due", {}),
            "timeLetters",
        )
        self.assertEqual(
            gate.feature_for_request("POST", "/echo/delayed-replies", {}),
            "echoDelayedReplies",
        )
        self.assertEqual(
            gate.feature_for_request("POST", "/echo/delayed-replies/dispatch-due", {}),
            "echoDelayedReplies",
        )

        for method, path in (
            ("GET", "/archive/time-letters/owner-a/letter-a/detail"),
            ("GET", "/mailbox/letters/owner-a"),
            ("POST", "/mailbox/letters/owner-a/message-a/read"),
            ("POST", "/mailbox/letters/owner-a/message-a/archive"),
            ("GET", "/echo/delayed-replies/owner-a"),
            ("GET", "/echo/delayed-replies/owner-a/reply-a/answer"),
        ):
            self.assertIsNone(gate.feature_for_request(method, path, {}), (method, path))

        self.assertIsNone(gate.feature_for_request("POST", "/devices/push-token", {}))
        self.assertEqual(
            gate.feature_for_request("POST", "/family/invite", {}),
            "familyManagement",
        )

    def test_new_creation_and_dispatch_do_not_mutate_storage(self):
        main_module.store.add_archive_item(
            "owner-closed",
            {
                "id": "time-letter-existing",
                "kind": "timeLetter",
                "title": "已封存测试信件",
                "deliveryState": "sealed",
                "deliveryStatus": "scheduled",
                "openAt": "2026-08-18T00:00:00Z",
                "recipients": [{"id": "self", "name": "我", "type": "self"}],
                "metadata": {
                    "contentKind": "time_letter",
                    "deliveryState": "sealed",
                    "timeLetterStatus": "sealed",
                    "openAt": "2026-08-18T00:00:00Z",
                    "recipientIds": "self",
                },
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        main_module.store.add_echo_delayed_reply(
            "owner-closed",
            {
                "id": "reply-existing",
                "delayedReplyId": "reply-existing",
                "userId": "owner-closed",
                "deliverAt": "2026-08-18T00:00:00Z",
                "status": "waiting",
                "trigger": "tenRoundBaseline",
            },
        )

        create_letter = client.post(
            "/archive/items",
            json={
                "userId": "owner-closed",
                "id": "time-letter-new",
                "kind": "timeLetter",
                "title": "不应创建",
            },
        )
        create_reply = client.post(
            "/echo/delayed-replies",
            json={
                "userId": "owner-closed",
                "delayedReplyId": "reply-new",
                "deliverAt": "2026-08-18T01:00:00Z",
                "roundCount": 10,
                "trigger": "tenRoundBaseline",
            },
        )
        dispatch_letters = self.machine_post(
            "/archive/time-letters/dispatch-due",
            {"now": "2026-08-18T02:00:00Z", "limit": 10},
        )
        dispatch_replies = self.machine_post(
            "/echo/delayed-replies/dispatch-due",
            {"now": "2026-08-18T02:00:00Z", "limit": 10},
        )
        retained_letters = client.get("/archive/items/owner-closed")
        retained_replies = client.get("/echo/delayed-replies/owner-closed")

        self.assert_product_closed(create_letter, "timeLetters")
        self.assert_product_closed(create_reply, "echoDelayedReplies")
        self.assert_product_closed(dispatch_letters, "timeLetters")
        self.assert_product_closed(dispatch_replies, "echoDelayedReplies")
        self.assertEqual(
            [item["id"] for item in main_module.store.list_archive_items("owner-closed")],
            ["time-letter-existing"],
        )
        self.assertEqual(
            [item["id"] for item in main_module.store.list_echo_delayed_replies("owner-closed")],
            ["reply-existing"],
        )
        self.assertEqual(main_module.store.list_mailbox_letters("owner-closed"), [])
        self.assertEqual(retained_letters.status_code, 200, retained_letters.text)
        self.assertEqual(
            [item["id"] for item in retained_letters.json()["items"]],
            ["time-letter-existing"],
        )
        self.assertEqual(retained_replies.status_code, 200, retained_replies.text)
        self.assertEqual(
            [item["id"] for item in retained_replies.json()["items"]],
            ["reply-existing"],
        )

    def test_ordinary_push_registration_remains_available(self):
        raw_token = "ab" * 32
        response = client.post(
            "/devices/push-token",
            json={
                "userId": "owner-notifications",
                "deviceToken": raw_token,
                "platform": "ios",
                "environment": "sandbox",
                "deviceId": "pc-00-02-notification-device",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "registered")
        self.assertNotIn(raw_token, str(response.json()))

        mailbox = self.machine_post(
            "/mailbox/letters",
            {
                "userId": "owner-notifications",
                "id": "family-invitation-notice",
                "kind": "familyInvitation",
                "title": "家人邀请",
                "status": "unread",
                "privacyMetadata": {"scope": "familyCircle"},
            },
        )
        self.assertEqual(mailbox.status_code, 200, mailbox.text)
        self.assertEqual(mailbox.json()["item"]["id"], "family-invitation-notice")
        self.assertEqual(mailbox.json()["item"]["status"], "unread")

    def test_worker_contract_is_a_successful_zero_delivery_noop(self):
        summary = product_closed_summary("2026-08-18T02:00:00Z")

        self.assertEqual(summary["status"], "productClosed")
        self.assertEqual(summary["reason"], "productClosed")
        self.assertEqual(summary["itemCount"], 0)
        self.assertEqual(summary["reminderCount"], 0)
        self.assertFalse(summary["providerDeliveryAttempted"])


if __name__ == "__main__":
    unittest.main()
