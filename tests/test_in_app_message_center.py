from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_app_message_center import (
    InAppMessageCenterCommandConflict,
    InAppMessageCenterMessage,
    InAppMessageCenterService,
    InAppMessageKind,
    InMemoryInAppMessageCenterRepository,
)
from app.services.in_memory_store import InMemoryStore


client = TestClient(app)


def _message(
    *,
    owner_id: str,
    sequence: int,
    kind: InAppMessageKind = InAppMessageKind.CANDIDATE_READY,
) -> InAppMessageCenterMessage:
    return InAppMessageCenterMessage(
        message_id=str(uuid4()),
        kind=kind,
        inbox_subject_id=owner_id,
        inbox_vault_id=f"vault-{owner_id}",
        resource_type="candidate" if kind is InAppMessageKind.CANDIDATE_READY else "task",
        resource_id=f"resource-{sequence}",
        resource_version=sequence,
        created_at=datetime(2026, 8, 19, 8, sequence, tzinfo=timezone.utc),
    )


class InAppMessageCenterServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryInAppMessageCenterRepository()
        self.service = InAppMessageCenterService(self.repository)

    def test_list_is_paginated_and_excludes_product_closed_message_kinds(self) -> None:
        owner_id = "owner-a"
        visible = [_message(owner_id=owner_id, sequence=index) for index in range(1, 5)]
        for message in visible:
            self.repository.seed(message)
        self.repository.seed(
            _message(owner_id=owner_id, sequence=5, kind=InAppMessageKind.TIME_LETTER)
        )
        self.repository.seed(
            _message(owner_id=owner_id, sequence=6, kind=InAppMessageKind.ECHO_REPLY)
        )
        self.repository.seed(_message(owner_id="owner-b", sequence=7))

        first = self.service.list_messages(owner_id, limit=2)
        second = self.service.list_messages(owner_id, limit=2, cursor=first.next_cursor)

        self.assertEqual(first.unread_count, 4)
        self.assertEqual(len(first.messages), 2)
        self.assertIsNotNone(first.next_cursor)
        self.assertEqual(len(second.messages), 2)
        self.assertIsNone(second.next_cursor)
        self.assertEqual(
            {item.message_id for item in first.messages + second.messages},
            {item.message_id for item in visible},
        )

    def test_single_read_is_idempotent_and_command_reuse_conflicts(self) -> None:
        first = _message(owner_id="owner-a", sequence=1)
        second = _message(owner_id="owner-a", sequence=2)
        self.repository.seed(first)
        self.repository.seed(second)
        command_id = str(uuid4())
        occurred_at = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)

        applied = self.service.mark_read(
            "owner-a",
            first.message_id,
            command_id=command_id,
            occurred_at=occurred_at,
        )
        replay = self.service.mark_read(
            "owner-a",
            first.message_id,
            command_id=command_id,
            occurred_at=occurred_at,
        )

        self.assertEqual(applied.outcome, "applied")
        self.assertEqual(applied.affected_count, 1)
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(replay.affected_count, 1)
        self.assertEqual(self.service.list_messages("owner-a").unread_count, 1)
        with self.assertRaises(InAppMessageCenterCommandConflict):
            self.service.mark_read(
                "owner-a",
                second.message_id,
                command_id=command_id,
                occurred_at=occurred_at,
            )

    def test_read_all_snapshot_does_not_consume_messages_created_after_command(self) -> None:
        self.repository.seed(_message(owner_id="owner-a", sequence=1))
        self.repository.seed(_message(owner_id="owner-a", sequence=2))
        command_id = str(uuid4())
        occurred_at = datetime(2026, 8, 19, 9, 30, tzinfo=timezone.utc)

        first = self.service.mark_all_read(
            "owner-a",
            command_id=command_id,
            occurred_at=occurred_at,
        )
        self.repository.seed(_message(owner_id="owner-a", sequence=3))
        replay = self.service.mark_all_read(
            "owner-a",
            command_id=command_id,
            occurred_at=occurred_at,
        )

        self.assertEqual(first.affected_count, 2)
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(replay.affected_count, 2)
        self.assertEqual(self.service.list_messages("owner-a").unread_count, 1)

    def test_read_all_does_not_consume_future_dated_message(self) -> None:
        command_time = datetime(2026, 8, 19, 9, 30, tzinfo=timezone.utc)
        current = _message(owner_id="owner-a", sequence=1)
        future = InAppMessageCenterMessage(
            message_id=str(uuid4()),
            kind=InAppMessageKind.CANDIDATE_READY,
            inbox_subject_id="owner-a",
            inbox_vault_id="vault-owner-a",
            resource_type="candidate",
            resource_id="future-candidate",
            resource_version=1,
            created_at=command_time + timedelta(seconds=1),
        )
        self.repository.seed(current)
        self.repository.seed(future)

        result = self.service.mark_all_read(
            "owner-a",
            command_id=str(uuid4()),
            occurred_at=command_time,
        )

        self.assertEqual(result.affected_count, 1)
        self.assertEqual(self.service.list_messages("owner-a").unread_count, 1)

    def test_delete_read_never_deletes_unread_or_other_account_messages(self) -> None:
        read_message = _message(owner_id="owner-a", sequence=1)
        unread_message = _message(owner_id="owner-a", sequence=2)
        other_message = _message(owner_id="owner-b", sequence=3)
        for message in (read_message, unread_message, other_message):
            self.repository.seed(message)
        self.service.mark_read(
            "owner-a",
            read_message.message_id,
            command_id=str(uuid4()),
            occurred_at=datetime.now(timezone.utc),
        )

        result = self.service.delete_read(
            "owner-a",
            command_id=str(uuid4()),
            occurred_at=datetime.now(timezone.utc),
        )

        self.assertEqual(result.affected_count, 1)
        owner_page = self.service.list_messages("owner-a")
        self.assertEqual([item.message_id for item in owner_page.messages], [unread_message.message_id])
        self.assertEqual(owner_page.unread_count, 1)
        self.assertEqual(self.service.list_messages("owner-b").unread_count, 1)


class InAppMessageCenterAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "消息中心测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        body = response.json()
        return str(body["user"]["id"]), {
            "Authorization": f"Bearer {body['auth']['accessToken']}"
        }

    def test_api_lists_reads_batches_and_isolates_principals(self) -> None:
        owner_id, headers = self._login("13800139981")
        other_id, other_headers = self._login("13800139982")
        repository = main_module.store.in_app_message_center_repository()
        owner_messages = [_message(owner_id=owner_id, sequence=index) for index in range(1, 4)]
        other_message = _message(owner_id=other_id, sequence=4)
        for message in (*owner_messages, other_message):
            repository.seed(message)

        listed = client.get(
            f"/v2/in-app-messages/{owner_id}",
            params={"limit": 2},
            headers=headers,
        )
        forbidden = client.get(f"/v2/in-app-messages/{other_id}", headers=headers)
        marked = client.post(
            f"/v2/in-app-messages/{owner_id}/{owner_messages[0].message_id}/read",
            json={"commandId": str(uuid4())},
            headers=headers,
        )
        marked_all = client.post(
            f"/v2/in-app-messages/{owner_id}/read-all",
            json={"commandId": str(uuid4())},
            headers=headers,
        )
        deleted = client.post(
            f"/v2/in-app-messages/{owner_id}/delete-read",
            json={"commandId": str(uuid4())},
            headers=headers,
        )
        other_listed = client.get(f"/v2/in-app-messages/{other_id}", headers=other_headers)

        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["schemaVersion"], "in-app-message-center-v1")
        self.assertEqual(listed.json()["unreadCount"], 3)
        self.assertEqual(len(listed.json()["items"]), 2)
        self.assertTrue(listed.json()["nextCursor"])
        self.assertNotIn("inboxVaultId", str(listed.json()))
        self.assertEqual(forbidden.status_code, 403, forbidden.text)
        self.assertEqual(marked.status_code, 200, marked.text)
        self.assertEqual(marked.json()["unreadCount"], 2)
        self.assertEqual(marked_all.status_code, 200, marked_all.text)
        self.assertEqual(marked_all.json()["unreadCount"], 0)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["affectedCount"], 3)
        self.assertEqual(
            client.get(f"/v2/in-app-messages/{owner_id}", headers=headers).json()["items"],
            [],
        )
        self.assertEqual(other_listed.status_code, 200, other_listed.text)
        self.assertEqual(other_listed.json()["unreadCount"], 1)

    def test_single_read_rejects_cross_account_message_as_not_found(self) -> None:
        owner_id, headers = self._login("13800139983")
        other_id, _ = self._login("13800139984")
        message = _message(owner_id=other_id, sequence=1)
        main_module.store.in_app_message_center_repository().seed(message)

        response = client.post(
            f"/v2/in-app-messages/{owner_id}/{message.message_id}/read",
            json={"commandId": str(uuid4())},
            headers=headers,
        )

        self.assertEqual(response.status_code, 404, response.text)


if __name__ == "__main__":
    unittest.main()
