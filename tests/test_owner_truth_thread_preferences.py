from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from app.domain.owner_truth.conversation import (
    InterviewBoundary,
    RestoreDoNotAskInterviewBoundaryCommand,
    SetInterviewBoundaryCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_thread_preferences import (
    OwnerTruthThreadPreferenceConflict,
    OwnerTruthThreadPreferenceCooldownActive,
    OwnerTruthThreadPreferenceService,
    RestoreCooldownThreadPreferenceCommand,
    ThreadPreferenceState,
)


class OwnerTruthThreadPreferenceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.owner_id = "owner-thread-preference"
        self.context = OwnerTruthCommandContext(
            vault_id="vault-thread-preference",
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.thread_id = str(uuid4())
        self.session_id = str(uuid4())
        self.now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
        with self.store.request_unit_of_work(
            correlation_id="thread-preference-start",
            command_id="thread-preference-start",
        ):
            started = OwnerTruthConversationService(
                self.store.owner_truth_conversation_repository()
            ).start_session(
                command=StartInterviewSessionCommand(
                    command_id="thread-preference-start",
                    thread_id=self.thread_id,
                    session_id=self.session_id,
                    expected_thread_version=0,
                    entry_mode="naturalInput",
                ),
                context=self.context,
            )
        self.assertEqual(started.session_version, 1)

    def _service(self, *, now: datetime | None = None) -> OwnerTruthThreadPreferenceService:
        return OwnerTruthThreadPreferenceService(
            self.store,
            enabled=True,
            cooldown_seconds=60,
            now=lambda: now or self.now,
        )

    def _set_boundary(
        self,
        *,
        boundary: InterviewBoundary,
        expected_session_version: int,
        command_id: str,
    ):
        return self._service().set_boundary(
            context=self.context,
            command=SetInterviewBoundaryCommand(
                command_id=command_id,
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=expected_session_version,
                boundary=boundary,
            ),
        )

    def _start_same_vault_thread(self, *, command_id: str) -> tuple[str, str]:
        thread_id = str(uuid4())
        session_id = str(uuid4())
        with self.store.request_unit_of_work(
            correlation_id=command_id,
            command_id=command_id,
        ):
            started = OwnerTruthConversationService(
                self.store.owner_truth_conversation_repository()
            ).start_session(
                command=StartInterviewSessionCommand(
                    command_id=command_id,
                    thread_id=thread_id,
                    session_id=session_id,
                    expected_thread_version=0,
                    entry_mode="naturalInput",
                ),
                context=self.context,
            )
        self.assertEqual(started.session_version, 1)
        return thread_id, session_id

    def test_cooldown_is_thread_scoped_and_becomes_effectively_eligible_after_expiry(self) -> None:
        paused = self._set_boundary(
            boundary=InterviewBoundary.COOLDOWN,
            expected_session_version=1,
            command_id="thread-preference-cooldown",
        )
        self.assertEqual(paused.session.boundary, InterviewBoundary.COOLDOWN)
        self.assertIsNotNone(paused.preference)
        assert paused.preference is not None
        self.assertEqual(paused.preference.preference.preference, ThreadPreferenceState.COOLDOWN)
        self.assertEqual(
            paused.preference.preference.cooldown_until,
            self.now + timedelta(seconds=60),
        )
        self.assertFalse(
            self._service().permits_recommendation(
                context=self.context,
                thread_id=self.thread_id,
            )
        )

        replayed = self._set_boundary(
            boundary=InterviewBoundary.COOLDOWN,
            expected_session_version=1,
            command_id="thread-preference-cooldown",
        )
        self.assertEqual(replayed.session.outcome, "deduplicated")
        self.assertEqual(replayed.session.session_version, paused.session.session_version)
        self.assertIsNotNone(replayed.preference)
        assert replayed.preference is not None
        self.assertEqual(replayed.preference.outcome, "deduplicated")
        self.assertEqual(
            replayed.preference.preference.cooldown_until,
            paused.preference.preference.cooldown_until,
        )

        with self.assertRaises(OwnerTruthThreadPreferenceCooldownActive):
            self._service(now=self.now + timedelta(seconds=59)).restore_cooldown(
                context=self.context,
                command=RestoreCooldownThreadPreferenceCommand(
                    command_id="thread-preference-restore-early",
                    thread_id=self.thread_id,
                    session_id=self.session_id,
                    expected_session_version=2,
                ),
            )

        self.assertFalse(
            self._service(now=self.now + timedelta(seconds=59)).permits_recommendation(
                context=self.context,
                thread_id=self.thread_id,
            )
        )
        self.assertTrue(
            self._service(now=self.now + timedelta(seconds=60)).permits_recommendation(
                context=self.context,
                thread_id=self.thread_id,
            )
        )

        # Explicit restore remains available for a later interaction flow. The
        # elapsed recommendation policy itself must not mutate the session.
        restored = self._service(now=self.now + timedelta(seconds=60)).restore_cooldown(
            context=self.context,
            command=RestoreCooldownThreadPreferenceCommand(
                command_id="thread-preference-restore-elapsed",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=2,
            ),
        )
        self.assertEqual(restored.session.boundary, InterviewBoundary.OPEN)
        assert restored.preference is not None
        self.assertEqual(restored.preference.preference.preference, ThreadPreferenceState.OPEN)
        self.assertTrue(
            self._service().permits_recommendation(
                context=self.context,
                thread_id=self.thread_id,
            )
        )

    def test_same_vault_thread_preferences_do_not_leak_or_accept_cross_thread_sessions(self) -> None:
        service = self._service()

        first_paused = self._set_boundary(
            boundary=InterviewBoundary.COOLDOWN,
            expected_session_version=1,
            command_id="thread-preference-first-thread-cooldown",
        )
        self.assertFalse(
            service.permits_recommendation(context=self.context, thread_id=self.thread_id)
        )
        # The conversation service permits one active session per Vault. Once
        # the first session is paused by its own preference, a later thread in
        # that same Vault can begin without inheriting the first preference.
        second_thread_id, second_session_id = self._start_same_vault_thread(
            command_id="thread-preference-second-thread-start"
        )
        self.assertTrue(
            service.permits_recommendation(context=self.context, thread_id=second_thread_id)
        )
        repository = self.store.owner_truth_thread_preference_repository()
        self.assertEqual(
            repository.read(context=self.context, thread_id=self.thread_id).preference,
            ThreadPreferenceState.COOLDOWN,
        )
        self.assertIsNone(repository.read(context=self.context, thread_id=second_thread_id))

        # A session belongs to exactly one opaque ConversationThread.  Reusing
        # the second thread's active session to mutate the first thread must
        # fail before any preference record is changed.
        with self.assertRaises(OwnerTruthThreadPreferenceConflict):
            service.set_boundary(
                context=self.context,
                command=SetInterviewBoundaryCommand(
                    command_id="thread-preference-cross-thread-set",
                    thread_id=self.thread_id,
                    session_id=second_session_id,
                    expected_session_version=1,
                    boundary=InterviewBoundary.DO_NOT_ASK,
                ),
            )
        self.assertEqual(
            repository.read(context=self.context, thread_id=self.thread_id).preference,
            ThreadPreferenceState.COOLDOWN,
        )
        self.assertIsNone(repository.read(context=self.context, thread_id=second_thread_id))

        second_paused = service.set_boundary(
            context=self.context,
            command=SetInterviewBoundaryCommand(
                command_id="thread-preference-second-thread-do-not-ask",
                thread_id=second_thread_id,
                session_id=second_session_id,
                expected_session_version=1,
                boundary=InterviewBoundary.DO_NOT_ASK,
            ),
        )
        assert first_paused.preference is not None
        assert second_paused.preference is not None
        self.assertEqual(
            first_paused.preference.preference.preference,
            ThreadPreferenceState.COOLDOWN,
        )
        self.assertEqual(
            second_paused.preference.preference.preference,
            ThreadPreferenceState.DO_NOT_ASK,
        )
        self.assertFalse(
            service.permits_recommendation(context=self.context, thread_id=second_thread_id)
        )

        # Once the first thread's cooldown elapsed, the second thread's paused
        # session still cannot restore it.  This proves restore is bound to the
        # same thread/session pair instead of only the shared Owner Vault.
        elapsed_service = self._service(now=self.now + timedelta(seconds=60))
        with self.assertRaises(OwnerTruthThreadPreferenceConflict):
            elapsed_service.restore_cooldown(
                context=self.context,
                command=RestoreCooldownThreadPreferenceCommand(
                    command_id="thread-preference-cross-thread-restore",
                    thread_id=self.thread_id,
                    session_id=second_session_id,
                    expected_session_version=2,
                ),
            )
        self.assertEqual(
            repository.read(context=self.context, thread_id=self.thread_id).preference,
            ThreadPreferenceState.COOLDOWN,
        )
        self.assertEqual(
            repository.read(context=self.context, thread_id=second_thread_id).preference,
            ThreadPreferenceState.DO_NOT_ASK,
        )
        self.assertFalse(
            service.permits_recommendation(context=self.context, thread_id=second_thread_id)
        )
        self.assertTrue(
            elapsed_service.permits_recommendation(context=self.context, thread_id=self.thread_id)
        )

    def test_do_not_ask_requires_confirmed_explicit_restore_and_skip_once_stays_session_only(self) -> None:
        skipped = self._set_boundary(
            boundary=InterviewBoundary.SKIP_ONCE,
            expected_session_version=1,
            command_id="thread-preference-skip-once",
        )
        self.assertIsNone(skipped.preference)

        # The one-shot boundary is consumed by the next owner narrative in the
        # existing conversation flow. Start a separate active vault/session so
        # this test only proves that doNotAsk owns persistent preference state.
        second_store = InMemoryStore()
        second_context = OwnerTruthCommandContext(
            vault_id="vault-thread-preference-do-not-ask",
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        second_thread_id = str(uuid4())
        second_session_id = str(uuid4())
        with second_store.request_unit_of_work(
            correlation_id="thread-preference-dna-start",
            command_id="thread-preference-dna-start",
        ):
            OwnerTruthConversationService(
                second_store.owner_truth_conversation_repository()
            ).start_session(
                command=StartInterviewSessionCommand(
                    command_id="thread-preference-dna-start",
                    thread_id=second_thread_id,
                    session_id=second_session_id,
                    expected_thread_version=0,
                    entry_mode="naturalInput",
                ),
                context=second_context,
            )
        service = OwnerTruthThreadPreferenceService(
            second_store,
            enabled=True,
            cooldown_seconds=60,
            now=lambda: self.now,
        )
        paused = service.set_boundary(
            context=second_context,
            command=SetInterviewBoundaryCommand(
                command_id="thread-preference-do-not-ask",
                thread_id=second_thread_id,
                session_id=second_session_id,
                expected_session_version=1,
                boundary=InterviewBoundary.DO_NOT_ASK,
            ),
        )
        assert paused.preference is not None
        self.assertEqual(paused.preference.preference.preference, ThreadPreferenceState.DO_NOT_ASK)
        self.assertFalse(
            service.permits_recommendation(context=second_context, thread_id=second_thread_id)
        )

        restored = service.restore_do_not_ask(
            context=second_context,
            command=RestoreDoNotAskInterviewBoundaryCommand(
                command_id="thread-preference-do-not-ask-restore",
                thread_id=second_thread_id,
                session_id=second_session_id,
                expected_session_version=2,
                confirmed=True,
            ),
        )
        self.assertEqual(restored.session.boundary, InterviewBoundary.OPEN)
        assert restored.preference is not None
        self.assertEqual(restored.preference.preference.preference, ThreadPreferenceState.OPEN)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
