#!/usr/bin/env python3
"""Exercise the M0-A private conversation lane in a disposable Postgres DB.

The smoke creates an isolated database, applies all migrations, then proves
owner/vault isolation, command replay, optimistic version checks, append-only
message records, restart reads, and the controlled path from an acknowledged
batch to one conversation Source plus a default-off extraction effect. It also
proves partial ordinary and individual sensitive Candidate review, while
keeping MemoryVersion activation outside this private review lane. It never
writes to the configured application database.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Callable
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.conversation import (
    AcknowledgeInterviewReviewBatchCommand,
    AppendInterviewMessageCommand,
    ConversationMessageAuthor,
    ConversationMessageKind,
    CreateInterviewReviewBatchCommand,
    InterviewBoundary,
    InterviewPacingEvent,
    InterviewReviewBatchState,
    InterviewReviewBatchTrigger,
    OwnerTruthConversationAccessDenied,
    OwnerTruthConversationVersionConflict,
    PauseInterviewForTopicSwitchCommand,
    RecordInterviewPacingCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.interview_orchestration import InterviewAction
from app.domain.owner_truth.interview_candidate_proposal import (
    AdmitInterviewReviewBatchForCandidateProposalCommand,
)
from app.domain.owner_truth.interview_candidate_batch_decision import (
    OwnerTruthInterviewCandidateBatchAcceptCommand,
    OwnerTruthInterviewCandidateBatchSelection,
)
from app.domain.owner_truth.interview_candidate_single_review import (
    OwnerTruthInterviewCandidateSingleReviewCommand,
)
from app.domain.owner_truth.candidate_decisions import CandidateReviewAction
from app.domain.owner_truth.candidate_extraction import (
    CandidateEvidenceSpan,
    CandidateProposal,
    CandidateReviewMode,
    ExtractionResultStatus,
    SyntheticCandidateExtractionCommand,
)
from app.domain.owner_truth.contracts import (
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.services.owner_truth_candidate_extraction import (
    OwnerTruthCandidateExtractionService,
)
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_interview_candidate_proposal import (
    OwnerTruthInterviewCandidateProposalService,
)
from app.services.owner_truth_interview_candidate_review import (
    OwnerTruthInterviewCandidateReviewCompositionService,
)
from app.services.owner_truth_interview_candidate_batch_decision import (
    OwnerTruthInterviewCandidateBatchDecisionService,
)
from app.services.owner_truth_interview_candidate_single_review import (
    OwnerTruthInterviewCandidateSingleReviewService,
)
from app.services.owner_truth_interview_session_orchestration import (
    InterviewSessionOrchestrationSignals,
    OwnerTruthInterviewSessionOrchestrationService,
)
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def dsn_for_database(base_dsn: str, database_name: str) -> str:
    parameters = conninfo_to_dict(base_dsn)
    parameters["dbname"] = database_name
    return make_conninfo(**parameters)


def create_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))


def drop_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))


def invoke(
    store: PostgresStore,
    *,
    command_id: str,
    operation: Callable[[OwnerTruthConversationService], object],
) -> object:
    with store.request_unit_of_work(
        correlation_id=f"owner-truth-conversation-smoke-{command_id}",
        command_id=command_id,
    ):
        return operation(OwnerTruthConversationService(store.owner_truth_conversation_repository()))


def invoke_orchestration(
    store: PostgresStore,
    *,
    command_id: str,
    operation: Callable[[OwnerTruthInterviewSessionOrchestrationService], object],
) -> object:
    with store.request_unit_of_work(
        correlation_id=f"owner-truth-conversation-orchestration-smoke-{command_id}",
        command_id=command_id,
    ):
        conversation_service = OwnerTruthConversationService(
            store.owner_truth_conversation_repository()
        )
        return operation(
            OwnerTruthInterviewSessionOrchestrationService(
                conversation_service=conversation_service
            )
        )


def invoke_candidate_proposal(
    store: PostgresStore,
    *,
    command_id: str,
    operation: Callable[[OwnerTruthInterviewCandidateProposalService], object],
) -> object:
    with store.request_unit_of_work(
        correlation_id=f"owner-truth-interview-candidate-proposal-smoke-{command_id}",
        command_id=command_id,
    ):
        return operation(OwnerTruthInterviewCandidateProposalService(store))


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_owner_truth_conversation_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)

    store = None
    restarted_store = None
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-conversation-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require("0033" in applied["appliedVersions"], "review batch candidate proposal migration must apply")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=2)
        store.open_pool(wait=True)
        context = OwnerTruthCommandContext(
            vault_id="conversation-vault-a",
            owner_subject_id="conversation-owner-a",
            actor_subject_id="conversation-owner-a",
            policy_version="owner-truth-v1",
        )
        thread_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        start = StartInterviewSessionCommand(
            command_id="start-conversation-smoke",
            thread_id=thread_id,
            session_id=session_id,
            expected_thread_version=0,
            entry_mode="naturalInput",
        )
        started = invoke(
            store,
            command_id="start-conversation-smoke",
            operation=lambda service: service.start_session(command=start, context=context),
        )
        require(started.outcome == "created", "start must create one session")
        replayed_start = invoke(
            store,
            command_id="start-conversation-smoke-replay",
            operation=lambda service: service.start_session(command=start, context=context),
        )
        require(replayed_start.outcome == "deduplicated", "start replay must deduplicate")

        append = AppendInterviewMessageCommand(
            command_id="append-conversation-smoke",
            thread_id=thread_id,
            session_id=session_id,
            message_id=message_id,
            expected_thread_version=1,
            expected_session_version=1,
            author=ConversationMessageAuthor.OWNER,
            kind=ConversationMessageKind.NARRATIVE,
            text="这是一条仅用于隔离数据库验证的私人访谈消息。",
        )
        appended = invoke(
            store,
            command_id="append-conversation-smoke",
            operation=lambda service: service.append_message(command=append, context=context),
        )
        require(appended.outcome == "created", "append must create one private message")
        require(appended.message_sequence == 1, "first message sequence must be one")
        replayed_append = invoke(
            store,
            command_id="append-conversation-smoke-replay",
            operation=lambda service: service.append_message(command=append, context=context),
        )
        require(replayed_append.outcome == "deduplicated", "append replay must deduplicate")

        pacing = invoke(
            store,
            command_id="record-conversation-pacing-smoke",
            operation=lambda service: service.record_pacing(
                command=RecordInterviewPacingCommand(
                    command_id="record-conversation-pacing-smoke",
                    thread_id=thread_id,
                    session_id=session_id,
                    expected_session_version=2,
                    event=InterviewPacingEvent.DEEPENING_COMPLETED,
                ),
                context=context,
            ),
        )
        require(pacing.outcome == "created", "pacing transition must persist")
        require(pacing.session_version == 3, "pacing transition must advance session version")

        bridged = invoke_orchestration(
            store,
            command_id="read-only-orchestration-bridge",
            operation=lambda service: service.decide(
                session_id=session_id,
                context=context,
                signals=InterviewSessionOrchestrationSignals(
                    topic_id="topic-smoke-private-story",
                    topic_incomplete=False,
                ),
            ),
        )
        require(
            bridged.decision.action is InterviewAction.LISTEN,
            "read-only bridge must use persisted active session state",
        )
        require(
            bridged.persisted_deepening_turn_count == 1
            and bridged.persisted_candidate_batch_turn_count == 1,
            "bridge must read persisted pacing counters instead of transient caller values",
        )
        bridge_summary = str(bridged.value_free_summary())
        require(
            append.text not in bridge_summary and session_id not in bridge_summary,
            "bridge trace must not leak private message content or session identifiers",
        )

        stale_rejected = False
        try:
            invoke(
                store,
                command_id="append-conversation-smoke-stale",
                operation=lambda service: service.append_message(
                    command=AppendInterviewMessageCommand(
                        command_id="append-conversation-smoke-stale",
                        thread_id=thread_id,
                        session_id=session_id,
                        message_id=str(uuid.uuid4()),
                        expected_thread_version=1,
                        expected_session_version=1,
                        author=ConversationMessageAuthor.OWNER,
                        kind=ConversationMessageKind.NARRATIVE,
                        text="这个陈旧版本必须被拒绝。",
                    ),
                    context=context,
                ),
            )
        except OwnerTruthConversationVersionConflict:
            stale_rejected = True
        require(stale_rejected, "stale expectedVersion must be rejected")

        other_context = OwnerTruthCommandContext(
            vault_id=context.vault_id,
            owner_subject_id="conversation-owner-b",
            actor_subject_id="conversation-owner-b",
            policy_version="owner-truth-v1",
        )
        cross_owner_rejected = False
        try:
            invoke(
                store,
                command_id="cross-owner-read",
                operation=lambda service: service.read_session(
                    session_id=session_id,
                    context=other_context,
                ),
            )
        except OwnerTruthConversationAccessDenied:
            cross_owner_rejected = True
        require(cross_owner_rejected, "cross-owner read must be denied")

        topic_switch = PauseInterviewForTopicSwitchCommand(
            command_id="pause-conversation-topic-switch-smoke",
            thread_id=thread_id,
            session_id=session_id,
            expected_thread_version=2,
            expected_session_version=3,
        )
        paused = invoke(
            store,
            command_id="pause-conversation-topic-switch-smoke",
            operation=lambda service: service.pause_for_topic_switch(
                command=topic_switch,
                context=context,
            ),
        )
        require(paused.state.value == "paused", "topic switch must pause the interview session")
        require(paused.thread_version == 3, "topic switch must advance the thread version")
        require(paused.session_version == 4, "topic switch must advance the session version")
        replayed_topic_switch = invoke(
            store,
            command_id="pause-conversation-topic-switch-smoke-replay",
            operation=lambda service: service.pause_for_topic_switch(
                command=topic_switch,
                context=context,
            ),
        )
        require(
            replayed_topic_switch.outcome == "deduplicated",
            "topic switch replay must deduplicate",
        )
        paused_bridge = invoke_orchestration(
            store,
            command_id="read-only-orchestration-paused",
            operation=lambda service: service.decide(
                session_id=session_id,
                context=context,
                signals=InterviewSessionOrchestrationSignals(
                    topic_id="topic-smoke-private-story",
                    topic_incomplete=True,
                ),
            ),
        )
        require(
            paused_bridge.decision.action is InterviewAction.PAUSE,
            "persisted topic-switch pause must fail closed in the bridge",
        )
        require(
            paused_bridge.decision.review_batch_due,
            "session exit with an unreviewed private turn must surface one review boundary",
        )

        review_batch = invoke(
            store,
            command_id="create-conversation-review-batch-smoke",
            operation=lambda service: service.create_review_batch(
                command=CreateInterviewReviewBatchCommand(
                    command_id="create-conversation-review-batch-smoke",
                    thread_id=thread_id,
                    session_id=session_id,
                    expected_session_version=4,
                ),
                context=context,
            ),
        )
        require(review_batch.outcome == "created", "review batch must persist")
        require(
            review_batch.review_batch.trigger is InterviewReviewBatchTrigger.SESSION_EXIT,
            "paused session must create an exit review batch",
        )
        require(
            review_batch.review_batch.captured_candidate_batch_turn_count == 1,
            "review batch must freeze only the unreviewed owner-turn count",
        )
        replayed_review_batch = invoke(
            store,
            command_id="create-conversation-review-batch-smoke-replay",
            operation=lambda service: service.create_review_batch(
                command=CreateInterviewReviewBatchCommand(
                    command_id="create-conversation-review-batch-smoke",
                    thread_id=thread_id,
                    session_id=session_id,
                    expected_session_version=4,
                ),
                context=context,
            ),
        )
        require(
            replayed_review_batch.outcome == "deduplicated",
            "review batch command replay must deduplicate",
        )

        acknowledged_review_batch = invoke(
            store,
            command_id="acknowledge-conversation-review-batch-smoke",
            operation=lambda service: service.acknowledge_review_batch(
                command=AcknowledgeInterviewReviewBatchCommand(
                    command_id="acknowledge-conversation-review-batch-smoke",
                    thread_id=thread_id,
                    session_id=session_id,
                    review_batch_id=review_batch.review_batch.review_batch_id,
                    expected_session_version=5,
                    expected_review_batch_version=1,
                ),
                context=context,
            ),
        )
        require(
            acknowledged_review_batch.outcome == "acknowledged",
            "review boundary acknowledgement must persist",
        )
        require(
            acknowledged_review_batch.review_batch.state is InterviewReviewBatchState.ACKNOWLEDGED,
            "review batch must become terminal after acknowledgement",
        )
        replayed_acknowledgement = invoke(
            store,
            command_id="acknowledge-conversation-review-batch-smoke-replay",
            operation=lambda service: service.acknowledge_review_batch(
                command=AcknowledgeInterviewReviewBatchCommand(
                    command_id="acknowledge-conversation-review-batch-smoke",
                    thread_id=thread_id,
                    session_id=session_id,
                    review_batch_id=review_batch.review_batch.review_batch_id,
                    expected_session_version=5,
                    expected_review_batch_version=1,
                ),
                context=context,
            ),
        )
        require(
            replayed_acknowledgement.outcome == "deduplicated",
            "review acknowledgement replay must deduplicate",
        )

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM owner_truth.sources WHERE vault_id = %s", (context.vault_id,))
                require(
                    cursor.fetchone()[0] == 0,
                    "acknowledgement alone must not promote a private message into a Source",
                )

        candidate_proposal = invoke_candidate_proposal(
            store,
            command_id="admit-conversation-review-batch-candidate-proposal",
            operation=lambda service: service.admit_review_batch(
                command=AdmitInterviewReviewBatchForCandidateProposalCommand(
                    command_id="admit-conversation-review-batch-candidate-proposal",
                    review_batch_id=review_batch.review_batch.review_batch_id,
                    expected_review_batch_version=2,
                ),
                context=context,
            ),
        )
        require(candidate_proposal.outcome == "created", "explicit review admission must create one Source effect")
        require(
            candidate_proposal.owner_message_count == 1,
            "candidate proposal source must contain only the acknowledged owner-turn window",
        )
        replayed_candidate_proposal = invoke_candidate_proposal(
            store,
            command_id="admit-conversation-review-batch-candidate-proposal-replay",
            operation=lambda service: service.admit_review_batch(
                command=AdmitInterviewReviewBatchForCandidateProposalCommand(
                    command_id="admit-conversation-review-batch-candidate-proposal",
                    review_batch_id=review_batch.review_batch.review_batch_id,
                    expected_review_batch_version=2,
                ),
                context=context,
            ),
        )
        require(
            replayed_candidate_proposal.outcome == "deduplicated",
            "review batch candidate proposal admission must deduplicate",
        )
        require(
            replayed_candidate_proposal.source_id == candidate_proposal.source_id,
            "replayed admission must point to the same immutable conversation Source",
        )

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT payload_hash FROM async_effects.operations WHERE operation_id = %s",
                    (candidate_proposal.effect_operation_id,),
                )
                effect_row = cursor.fetchone()
                require(
                    effect_row is not None,
                    "candidate proposal admission must retain source effect payload hash",
                )
                source_effect_payload_hash = str(effect_row[0])

        source_effect_intent = AsyncEffectIntent(
            operation_type="ownerTruth.source.created",
            target=AsyncEffectTarget(
                owner_subject_id=context.owner_subject_id,
                vault_id=context.vault_id,
                resource_type="source",
                resource_id=candidate_proposal.source_id,
                resource_version=candidate_proposal.source_version,
                purpose="candidateExtraction",
                authority_epoch=0,
            ),
            payload_hash=source_effect_payload_hash,
        )
        require(
            source_effect_intent.operation_id == candidate_proposal.effect_operation_id,
            "candidate extraction must reuse the admitted immutable Source effect",
        )
        extraction_command = SyntheticCandidateExtractionCommand(
            intent=source_effect_intent,
            extractor_id="deterministicInterviewFixture",
            model_id="fixture-v1",
            prompt_version="interview-candidate-review-v1",
            policy_version=context.policy_version,
            source_content_hash=candidate_proposal.source_content_hash,
            status=ExtractionResultStatus.SUCCEEDED,
            proposals=(
                CandidateProposal(
                    memory_kind=MemoryKind.EXPERIENCE,
                    perspective_type=PerspectiveType.FIRST_PERSON,
                    epistemic_status=EpistemicStatus.RECALLED,
                    sensitivity=SensitivityLevel.STANDARD,
                    content={"summary": "这是一条可批量审核的访谈候选。"},
                    evidence_span=CandidateEvidenceSpan(start=0, end=5),
                    confidence=0.72,
                    review_mode=CandidateReviewMode.BATCH,
                ),
                CandidateProposal(
                    memory_kind=MemoryKind.EXPERIENCE,
                    perspective_type=PerspectiveType.FIRST_PERSON,
                    epistemic_status=EpistemicStatus.RECALLED,
                    sensitivity=SensitivityLevel.SENSITIVE,
                    content={"summary": "这是一条必须逐条确认的敏感访谈候选。"},
                    evidence_span=CandidateEvidenceSpan(start=0, end=5),
                    confidence=0.64,
                    review_mode=CandidateReviewMode.SINGLE,
                ),
            ),
        )
        extraction_service = OwnerTruthCandidateExtractionService(store)
        extracted = extraction_service.record(extraction_command)
        replayed_extraction = extraction_service.record(extraction_command)
        require(extracted.outcome == "created", "conversation Source extraction must persist")
        require(
            replayed_extraction.outcome == "deduplicated",
            "conversation Source extraction replay must deduplicate",
        )
        require(
            len(extracted.candidate_ids) == 2,
            "conversation Source extraction must produce two pending Candidates",
        )
        composition = OwnerTruthInterviewCandidateReviewCompositionService(store).compose(
            review_batch_id=review_batch.review_batch.review_batch_id,
            context=context,
        )
        require(
            composition.readiness.value == "reviewReady",
            "admitted batch Candidates must become reviewable without direct activation",
        )
        require(
            len(composition.batch_candidates) == 1
            and len(composition.single_candidates) == 1,
            "standard batch Candidate and sensitive Candidate must use separate review paths",
        )
        composition_summary = composition.public_summary()
        require(
            "这是一条可批量审核的访谈候选。" not in str(composition_summary)
            and "这是一条必须逐条确认的敏感访谈候选。" not in str(composition_summary),
            "review composition summary must remain value-free",
        )
        batch_item = composition.batch_candidates[0]
        batch_accept_command = OwnerTruthInterviewCandidateBatchAcceptCommand(
            command_id="interview-batch-accept-smoke",
            review_batch_id=review_batch.review_batch.review_batch_id,
            selections=(
                OwnerTruthInterviewCandidateBatchSelection(
                    candidate_id=batch_item.candidate_id,
                    expected_candidate_version=batch_item.candidate_row_version,
                ),
            ),
            reason_code="ownerReviewed",
        )
        batch_accept_service = OwnerTruthInterviewCandidateBatchDecisionService(store)
        batch_accepted = batch_accept_service.accept_selected(
            command=batch_accept_command,
            context=context,
        )
        replayed_batch_accept = batch_accept_service.accept_selected(
            command=batch_accept_command,
            context=context,
        )
        require(
            batch_accepted.outcome == "created"
            and batch_accepted.accepted_candidate_count == 1
            and batch_accepted.candidate_results[0].outcome == "created",
            "selected standard Candidate must receive one terminal DecisionReceipt",
        )
        require(
            replayed_batch_accept.outcome == "deduplicated"
            and replayed_batch_accept.candidate_results[0].outcome == "deduplicated",
            "batch acceptance replay must not create another DecisionReceipt",
        )
        remaining_composition = OwnerTruthInterviewCandidateReviewCompositionService(store).compose(
            review_batch_id=review_batch.review_batch.review_batch_id,
            context=context,
        )
        require(
            len(remaining_composition.batch_candidates) == 0
            and len(remaining_composition.single_candidates) == 1,
            "partial batch acceptance must leave the sensitive Candidate pending for single review",
        )
        single_item = remaining_composition.single_candidates[0]
        single_review_command = OwnerTruthInterviewCandidateSingleReviewCommand(
            command_id="interview-single-review-smoke",
            review_batch_id=review_batch.review_batch.review_batch_id,
            candidate_id=single_item.candidate_id,
            expected_candidate_version=single_item.candidate_row_version,
            action=CandidateReviewAction.REJECT,
            corrected_value=None,
            corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            reason_code="ownerReviewed",
        )
        single_review_service = OwnerTruthInterviewCandidateSingleReviewService(store)
        single_reviewed = single_review_service.review_single(
            command=single_review_command,
            context=context,
        )
        replayed_single_review = single_review_service.review_single(
            command=single_review_command,
            context=context,
        )
        require(
            single_reviewed.outcome == "created"
            and single_reviewed.review.decision.value == "rejected",
            "sensitive Candidate must receive one terminal single-review DecisionReceipt",
        )
        require(
            replayed_single_review.outcome == "deduplicated"
            and replayed_single_review.review.outcome == "deduplicated",
            "single-review replay must not create another DecisionReceipt",
        )
        exhausted_composition = OwnerTruthInterviewCandidateReviewCompositionService(store).compose(
            review_batch_id=review_batch.review_batch.review_batch_id,
            context=context,
        )
        require(
            exhausted_composition.readiness.value == "noCandidates",
            "terminal single review must leave no pending Candidate in the admitted batch",
        )

        follow_up_thread_id = str(uuid.uuid4())
        follow_up_session_id = str(uuid.uuid4())
        follow_up = invoke(
            store,
            command_id="start-conversation-after-topic-switch",
            operation=lambda service: service.start_session(
                command=StartInterviewSessionCommand(
                    command_id="start-conversation-after-topic-switch",
                    thread_id=follow_up_thread_id,
                    session_id=follow_up_session_id,
                    expected_thread_version=0,
                    entry_mode="naturalInput",
                ),
                context=context,
            ),
        )
        require(
            follow_up.state.value == "active",
            "an explicit replacement session must be possible after a topic switch pause",
        )

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM owner_truth.conversation_messages WHERE vault_id = %s",
                    (context.vault_id,),
                )
                require(cursor.fetchone()[0] == 1, "exactly one private message must persist")
                cursor.execute(
                    """
                    SELECT source_kind, metadata ->> 'reviewBatchId' AS review_batch_id,
                        content_payload ->> 'text' AS source_text
                    FROM owner_truth.sources
                    WHERE vault_id = %s AND id = %s
                    """,
                    (context.vault_id, candidate_proposal.source_id),
                )
                admitted_source = cursor.fetchone()
                require(admitted_source is not None, "candidate proposal admission must retain one Source")
                require(admitted_source[0] == "conversation", "admitted Source must be a conversation Source")
                require(
                    admitted_source[1] == review_batch.review_batch.review_batch_id,
                    "conversation Source must retain review-batch provenance without raw message metadata",
                )
                require(
                    admitted_source[2] == append.text,
                    "conversation Source must contain only the acknowledged owner message",
                )
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM owner_truth.interview_review_batch_candidate_admissions
                    WHERE vault_id = %s AND review_batch_id = %s
                    """,
                    (context.vault_id, review_batch.review_batch.review_batch_id),
                )
                require(cursor.fetchone()[0] == 1, "one review batch must have one admission record")
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM owner_truth.interview_review_batch_candidate_decisions
                    WHERE vault_id = %s AND review_batch_id = %s
                    """,
                    (context.vault_id, review_batch.review_batch.review_batch_id),
                )
                require(
                    cursor.fetchone()[0] == 2,
                    "partial batch and sensitive single review must retain two root command ledger records",
                )
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM async_effects.operations
                    WHERE vault_id = %s AND operation_id = %s
                    """,
                    (context.vault_id, candidate_proposal.effect_operation_id),
                )
                require(cursor.fetchone()[0] == 1, "admission must retain one default-off extraction effect")
                for relation in ("memory_candidates", "decision_receipts", "memories", "memory_versions"):
                    cursor.execute(f"SELECT COUNT(*) FROM owner_truth.{relation}")
                    count = cursor.fetchone()[0]
                    if relation == "memory_candidates":
                        require(
                            count == 2,
                            "synthetic review composition may create only its two pending Candidates",
                        )
                    elif relation == "decision_receipts":
                        require(
                            count == 2,
                            "partial batch and sensitive single review must create exactly two Candidate DecisionReceipts",
                        )
                    else:
                        require(
                            count == 0,
                            f"interview Candidate review must not activate owner_truth.{relation}",
                        )
                immutable_message_rejected = False
                try:
                    cursor.execute(
                        "UPDATE owner_truth.conversation_messages SET content_hash = %s WHERE id = %s",
                        ("tampered", message_id),
                    )
                except Exception:
                    immutable_message_rejected = True
                    connection.rollback()
                require(immutable_message_rejected, "conversation message must be append-only")

        store.close_pool()
        store = None
        restarted_store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=2)
        restarted_store.open_pool(wait=True)
        restored = invoke(
            restarted_store,
            command_id="read-after-restart",
            operation=lambda service: service.read_session(session_id=session_id, context=context),
        )
        require(restored.row_version == 6, "session state must survive a store restart")
        require(restored.boundary is InterviewBoundary.OPEN, "open boundary must survive topic switch")
        require(restored.deepening_turn_count == 1, "pacing state must survive restart")
        require(restored.candidate_batch_turn_count == 0, "acknowledgement must consume only its batch")
        require(restored.pending_review_batch_id is None, "acknowledgement must clear pending batch")
        restored_batches = invoke(
            restarted_store,
            command_id="list-conversation-review-batch-after-restart",
            operation=lambda service: service.list_review_batches(
                session_id=session_id,
                context=context,
            ),
        )
        require(len(restored_batches) == 1, "one review batch must survive restart")
        require(
            restored_batches[0].state is InterviewReviewBatchState.ACKNOWLEDGED,
            "acknowledged review state must survive restart",
        )
        print("owner_truth_conversation_postgres_smoke=passed")
    finally:
        if restarted_store is not None:
            restarted_store.close_pool()
        if store is not None:
            store.close_pool()
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
