import copy
from contextlib import contextmanager
from hashlib import sha256
import unittest

from app.async_effects.consumer_repository import InMemoryAsyncEffectConsumerRepository
from app.async_effects.repository import InMemoryEffectKernelRepository
from app.services.echo_delayed_reply_effects import (
    ECHO_DELAYED_REPLY_SCHEMA_VERSION,
    EchoDelayedReplyGeneratedAnswer,
    build_echo_delayed_reply_plan,
)
from app.services.echo_delayed_reply_service import (
    EchoDelayedReplyAtomicCompletionPersistenceError,
    EchoDelayedReplyAtomicCompletionService,
)


def _reply(**overrides):
    item = {
        "id": "delayed-echo-atomic-001",
        "ownerSubjectId": "owner-001",
        "vaultId": "vault-001",
        "conversationId": "conversation-001",
        "requestId": "request-001",
        "replyGeneration": 3,
        "contextHash": sha256(b"echo-context-atomic-v4").hexdigest(),
        "contextVersion": "echo-context-v4",
        "policyVersion": "echo-policy-v4",
        "authorityEpoch": 9,
        "rowVersion": 4,
        "deliverAt": "2026-07-20T09:00:00Z",
        "contextExpiresAt": "2026-07-20T10:00:00Z",
        "authorityState": "active",
        "deliveryState": "scheduled",
        "deliveryProtocolVersion": ECHO_DELAYED_REPLY_SCHEMA_VERSION,
    }
    item.update(overrides)
    return item


def _answer():
    return EchoDelayedReplyGeneratedAnswer(
        answer_text="This private delayed answer must not appear in mailbox or receipt evidence.",
        citation_receipt_hash=sha256(b"citation-atomic").hexdigest(),
        provider_result_hash=sha256(b"provider-atomic").hexdigest(),
    )


class _CompletionStore:
    def __init__(self, item):
        self.item = copy.deepcopy(item)
        self.answers = {}
        self.mailboxes = {}
        self.kernel = InMemoryEffectKernelRepository()
        self.consumer = InMemoryAsyncEffectConsumerRepository()
        self.fail_mailbox = False
        self.fail_finalize = False

    @contextmanager
    def request_unit_of_work(self, **_kwargs):
        snapshot = {
            "item": copy.deepcopy(self.item),
            "answers": copy.deepcopy(self.answers),
            "mailboxes": copy.deepcopy(self.mailboxes),
            "kernel": copy.deepcopy(self.kernel._records),
            "consumerInbox": copy.deepcopy(self.consumer._inbox),
            "consumerReceipts": copy.deepcopy(self.consumer._receipts),
        }
        try:
            yield self
        except Exception:
            self.item = snapshot["item"]
            self.answers = snapshot["answers"]
            self.mailboxes = snapshot["mailboxes"]
            self.kernel._records = snapshot["kernel"]
            self.consumer._inbox = snapshot["consumerInbox"]
            self.consumer._receipts = snapshot["consumerReceipts"]
            raise

    def effect_kernel_repository(self):
        return self.kernel

    def async_effect_consumer_repository(self):
        return self.consumer

    def get_echo_delayed_reply_for_completion(self, owner_subject_id, delayed_reply_id):
        if owner_subject_id != self.item["ownerSubjectId"] or delayed_reply_id != self.item["id"]:
            return None
        return copy.deepcopy(self.item)

    def persist_echo_delayed_reply_answer(
        self,
        owner_subject_id,
        snapshot,
        completion,
        payload,
        completed_at_iso,
    ):
        if owner_subject_id != self.item["ownerSubjectId"]:
            raise EchoDelayedReplyAtomicCompletionPersistenceError("wrong owner")
        item = copy.deepcopy(dict(payload))
        if item["id"] != completion.answer_id or item["completedAt"] != completed_at_iso:
            raise EchoDelayedReplyAtomicCompletionPersistenceError("answer identity mismatch")
        existing = self.answers.get(item["id"])
        if existing is not None and existing != item:
            raise EchoDelayedReplyAtomicCompletionPersistenceError("Answer identity conflict")
        self.answers[item["id"]] = item
        return copy.deepcopy(item)

    def add_mailbox_letter(self, user_id, payload):
        if self.fail_mailbox:
            raise EchoDelayedReplyAtomicCompletionPersistenceError("injected mailbox failure")
        if user_id != self.item["ownerSubjectId"]:
            raise EchoDelayedReplyAtomicCompletionPersistenceError("wrong mailbox owner")
        item = copy.deepcopy(payload)
        existing = self.mailboxes.get(item["id"])
        if existing is not None and existing != item:
            raise EchoDelayedReplyAtomicCompletionPersistenceError("mailbox identity conflict")
        self.mailboxes[item["id"]] = item
        return copy.deepcopy(item)

    def update_echo_delayed_reply_completion(
        self,
        owner_subject_id,
        delayed_reply_id,
        snapshot,
        completion,
        expected_row_version,
        completed_at_iso,
    ):
        if self.fail_finalize:
            return None
        current = self.item
        if owner_subject_id != current["ownerSubjectId"] or delayed_reply_id != current["id"]:
            return None
        if expected_row_version != current.get("rowVersion"):
            return None
        if (
            current.get("deliveryState") not in {"scheduled", "ready", "generating"}
            or current.get("replyGeneration") != snapshot.reply_generation
            or current.get("contextHash") != snapshot.context_hash
            or current.get("authorityEpoch") != snapshot.authority_epoch
        ):
            return None
        updated = copy.deepcopy(current)
        updated["deliveryState"] = completion.outcome
        updated["completedAt"] = completed_at_iso
        updated["completionSummary"] = dict(completion.value_free_summary())
        updated["responseAnswerId"] = completion.answer_id if completion.answer is not None else None
        updated["rowVersion"] += 1
        self.item = updated
        return copy.deepcopy(updated)


class EchoDelayedReplyAtomicCompletionServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = _CompletionStore(_reply())
        self.plan = build_echo_delayed_reply_plan(
            self.store.item,
            now_iso="2026-07-20T09:00:01Z",
        )
        self.service = EchoDelayedReplyAtomicCompletionService(self.store)

    def test_completed_reply_persists_private_answer_then_inbox_and_receipt(self):
        result = self.service.complete(
            self.plan,
            generated_answer=_answer(),
            now_iso="2026-07-20T09:00:01Z",
        )

        self.assertEqual(result.outcome, "completed")
        self.assertEqual(self.store.item["deliveryState"], "completed")
        self.assertEqual(len(self.store.answers), 1)
        self.assertEqual(len(self.store.mailboxes), 1)
        self.assertEqual(self.store.kernel.record_count(), 1)
        self.assertEqual(len(self.store.consumer._receipts), 1)
        mailbox = next(iter(self.store.mailboxes.values()))
        self.assertTrue(mailbox["metadataOnly"])
        self.assertTrue(mailbox["contentRedacted"])
        self.assertEqual(mailbox["sourceAnswerId"], self.store.item["responseAnswerId"])
        self.assertNotIn(_answer().answer_text, str(mailbox))
        self.assertNotIn(_answer().answer_text, str(self.store.consumer._receipts))
        self.assertNotIn(_answer().answer_text, str(result.value_free_summary()))

    def test_double_due_dispatch_is_terminal_replay_without_duplicate_answer_or_inbox(self):
        first = self.service.complete(
            self.plan,
            generated_answer=_answer(),
            now_iso="2026-07-20T09:00:01Z",
        )
        second = self.service.complete(
            self.plan,
            generated_answer=_answer(),
            now_iso="2026-07-20T09:00:02Z",
        )

        self.assertEqual(first.outcome, "completed")
        self.assertEqual(second.outcome, "already_terminal")
        self.assertEqual(len(self.store.answers), 1)
        self.assertEqual(len(self.store.mailboxes), 1)
        self.assertEqual(self.store.kernel.record_count(), 1)
        self.assertEqual(len(self.store.consumer._receipts), 1)

    def test_context_expiry_becomes_blocked_and_never_creates_answer_or_inbox(self):
        self.store.item["contextExpiresAt"] = "2026-07-20T09:00:00Z"
        plan = build_echo_delayed_reply_plan(
            self.store.item,
            now_iso="2026-07-20T09:00:01Z",
        )

        result = self.service.complete(
            plan,
            generated_answer=_answer(),
            now_iso="2026-07-20T09:00:01Z",
        )

        self.assertEqual(result.outcome, "blocked")
        self.assertEqual(result.completion.reason_code, "contextExpired")
        self.assertEqual(self.store.item["deliveryState"], "blocked")
        self.assertEqual(self.store.answers, {})
        self.assertEqual(self.store.mailboxes, {})
        self.assertEqual(self.store.kernel.record_count(), 1)
        self.assertEqual(len(self.store.consumer._receipts), 1)

    def test_changed_context_after_plan_is_not_allowed_to_write_an_old_answer(self):
        self.store.item["contextHash"] = sha256(b"newer-context").hexdigest()

        with self.assertRaisesRegex(EchoDelayedReplyAtomicCompletionPersistenceError, "changed before blocked"):
            self.service.complete(
                self.plan,
                generated_answer=_answer(),
                now_iso="2026-07-20T09:00:01Z",
            )

        self.assertEqual(self.store.answers, {})
        self.assertEqual(self.store.mailboxes, {})
        self.assertEqual(self.store.kernel.record_count(), 0)
        self.assertEqual(len(self.store.consumer._receipts), 0)
        self.assertEqual(self.store.item["deliveryState"], "scheduled")

    def test_provider_result_followed_by_mailbox_failure_rolls_back_every_business_write(self):
        self.store.fail_mailbox = True

        with self.assertRaisesRegex(EchoDelayedReplyAtomicCompletionPersistenceError, "injected mailbox failure"):
            self.service.complete(
                self.plan,
                generated_answer=_answer(),
                now_iso="2026-07-20T09:00:01Z",
            )

        self.assertEqual(self.store.answers, {})
        self.assertEqual(self.store.mailboxes, {})
        self.assertEqual(self.store.kernel.record_count(), 0)
        self.assertEqual(len(self.store.consumer._inbox), 0)
        self.assertEqual(len(self.store.consumer._receipts), 0)
        self.assertEqual(self.store.item["deliveryState"], "scheduled")

    def test_final_cas_failure_rolls_back_provider_result_persistence_and_mailbox(self):
        self.store.fail_finalize = True

        with self.assertRaisesRegex(EchoDelayedReplyAtomicCompletionPersistenceError, "Answer/Inbox completion"):
            self.service.complete(
                self.plan,
                generated_answer=_answer(),
                now_iso="2026-07-20T09:00:01Z",
            )

        self.assertEqual(self.store.answers, {})
        self.assertEqual(self.store.mailboxes, {})
        self.assertEqual(self.store.kernel.record_count(), 0)
        self.assertEqual(len(self.store.consumer._receipts), 0)
        self.assertEqual(self.store.item["deliveryState"], "scheduled")

    def test_not_due_plan_does_not_persist_provider_result(self):
        not_due = build_echo_delayed_reply_plan(
            _reply(deliverAt="2026-07-20T09:01:00Z"),
            now_iso="2026-07-20T09:00:01Z",
        )
        store = _CompletionStore(_reply(deliverAt="2026-07-20T09:01:00Z"))
        service = EchoDelayedReplyAtomicCompletionService(store)

        result = service.complete(
            not_due,
            generated_answer=_answer(),
            now_iso="2026-07-20T09:00:01Z",
        )

        self.assertEqual(result.outcome, "not_due")
        self.assertEqual(store.answers, {})
        self.assertEqual(store.mailboxes, {})
        self.assertEqual(store.kernel.record_count(), 0)


if __name__ == "__main__":
    unittest.main()
