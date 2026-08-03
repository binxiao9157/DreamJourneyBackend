"""Private Stage 2 processing for verified Owner Truth media SourceObjects.

The upload boundary owns safety and private bytes. This module owns only the
next step: queueing a verified object, deterministically extracting text when
that can happen locally, and creating an ``import`` Source for the existing
Owner-reviewed candidate flow. It never creates a MemoryVersion, Persona, or
public search record, and it never fabricates OCR or ASR output.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from typing import Any, Callable, Mapping, Optional, Protocol
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from app.async_effects.consumer_repository import AsyncEffectConsumerCompletionCommand
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget, EffectReceiptSummary
from app.domain.owner_truth.contracts import SourceKind
from app.domain.owner_truth.source_commands import CreateTextSourceCommand, OwnerTruthCommandContext
from app.services.owner_truth_media_source_object import OwnerTruthMediaUploadInvalid
from app.services.owner_truth_source import build_source_created_effect_intent


OWNER_TRUTH_MEDIA_PROCESSING_SCHEMA_VERSION = "owner-truth-media-processing-v1"
OWNER_TRUTH_MEDIA_PROCESSING_OPERATION_TYPE = "ownerTruth.mediaSourceObject.process"
OWNER_TRUTH_MEDIA_PROCESSING_EVENT_TYPE = "ownerTruth.mediaSourceObject.processingRequested"
OWNER_TRUTH_MEDIA_PROCESSING_JOB_TYPE = "ownerTruth.mediaSourceObject.process"
OWNER_TRUTH_MEDIA_PROCESSING_CONSUMER = "ownerTruth.mediaProcessing"
OWNER_TRUTH_MEDIA_PROCESSING_MAX_ATTEMPTS = 3
_MAX_EXTRACTED_TEXT_CHARACTERS = 20_000


class OwnerTruthMediaProcessingError(RuntimeError):
    """The private media processor could not produce a valid state transition."""


class OwnerTruthMediaProcessingRetryableError(OwnerTruthMediaProcessingError):
    """The current provider/runtime is unavailable and can be retried later."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = _opaque_identifier(reason_code, field="reason_code")
        super().__init__(self.reason_code)


class OwnerTruthMediaProcessingTerminalError(OwnerTruthMediaProcessingError):
    """The verified object is permanently unsuitable for its requested parser."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = _opaque_identifier(reason_code, field="reason_code")
        super().__init__(self.reason_code)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _opaque_identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 80 or not normalized[0].isalpha():
        raise OwnerTruthMediaProcessingError(f"{field} must be an opaque identifier")
    if not all(character.isalnum() or character in "._-" for character in normalized):
        raise OwnerTruthMediaProcessingError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthMediaProcessingError(f"{field} is invalid") from exc


@dataclass(frozen=True)
class MediaProcessingEnqueueResult:
    source_object: Mapping[str, Any]
    effect: Optional[EffectReceiptSummary]
    intent: Optional[AsyncEffectIntent]

    @property
    def queued(self) -> bool:
        return self.intent is not None


@dataclass(frozen=True)
class MediaTextExtraction:
    processor_id: str
    processor_version: str
    extracted_text: str
    truncated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "processor_id", _opaque_identifier(self.processor_id, field="processor_id"))
        object.__setattr__(
            self,
            "processor_version",
            _opaque_identifier(self.processor_version, field="processor_version"),
        )
        normalized = str(self.extracted_text or "").strip()
        if not normalized:
            raise OwnerTruthMediaProcessingTerminalError("documentContainsNoExtractableText")
        if len(normalized) > _MAX_EXTRACTED_TEXT_CHARACTERS:
            normalized = normalized[:_MAX_EXTRACTED_TEXT_CHARACTERS]
            object.__setattr__(self, "truncated", True)
        object.__setattr__(self, "extracted_text", normalized)

    @property
    def extracted_text_sha256(self) -> str:
        return _hash(self.extracted_text)

    def result_hash(self, *, source_object: Mapping[str, Any]) -> str:
        return _hash(
            _canonical_json(
                {
                    "contentSha256": str(source_object["contentSha256"]),
                    "extractedTextSha256": self.extracted_text_sha256,
                    "processorId": self.processor_id,
                    "processorVersion": self.processor_version,
                    "schemaVersion": OWNER_TRUTH_MEDIA_PROCESSING_SCHEMA_VERSION,
                    "sourceObjectId": str(source_object["sourceObjectId"]),
                    "truncated": self.truncated,
                }
            )
        )


class OwnerTruthMediaProcessor(Protocol):
    def extract(self, *, source_object: Mapping[str, Any], payload: bytes) -> MediaTextExtraction:
        ...


class LocalDocumentTextProcessor:
    """Parses private text, PDF and DOCX content without a network provider."""

    processor_id = "localDocumentText"
    processor_version = "v1"

    def extract(self, *, source_object: Mapping[str, Any], payload: bytes) -> MediaTextExtraction:
        content_type = str(source_object.get("contentType") or "")
        if content_type == "text/plain":
            try:
                text = payload.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise OwnerTruthMediaProcessingTerminalError("documentTextEncodingUnsupported") from exc
            return MediaTextExtraction(
                processor_id=self.processor_id,
                processor_version=self.processor_version,
                extracted_text=_normalize_extracted_text(text),
            )
        if content_type == "application/pdf":
            return self._extract_pdf(payload)
        if content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            return self._extract_docx(payload)
        raise OwnerTruthMediaProcessingTerminalError("documentContentTypeUnsupported")

    def _extract_pdf(self, payload: bytes) -> MediaTextExtraction:
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - packaging contract covers this
            raise OwnerTruthMediaProcessingRetryableError("documentParserUnavailable") from exc
        try:
            reader = PdfReader(BytesIO(payload), strict=True)
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:100])
        except Exception as exc:
            raise OwnerTruthMediaProcessingTerminalError("pdfTextExtractionFailed") from exc
        return MediaTextExtraction(
            processor_id=self.processor_id,
            processor_version=self.processor_version,
            extracted_text=_normalize_extracted_text(text),
        )

    def _extract_docx(self, payload: bytes) -> MediaTextExtraction:
        try:
            from docx import Document
        except ImportError as exc:  # pragma: no cover - packaging contract covers this
            raise OwnerTruthMediaProcessingRetryableError("documentParserUnavailable") from exc
        try:
            document = Document(BytesIO(payload))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        except Exception as exc:
            raise OwnerTruthMediaProcessingTerminalError("docxTextExtractionFailed") from exc
        return MediaTextExtraction(
            processor_id=self.processor_id,
            processor_version=self.processor_version,
            extracted_text=_normalize_extracted_text(text),
        )


class DisabledImageOCRProcessor:
    processor_id = "disabledImageOCR"
    processor_version = "v1"

    def extract(self, *, source_object: Mapping[str, Any], payload: bytes) -> MediaTextExtraction:
        del source_object, payload
        raise OwnerTruthMediaProcessingRetryableError("imageOcrProviderUnavailable")


class DisabledAudioASRProcessor:
    processor_id = "disabledAudioASR"
    processor_version = "v1"

    def extract(self, *, source_object: Mapping[str, Any], payload: bytes) -> MediaTextExtraction:
        del source_object, payload
        raise OwnerTruthMediaProcessingRetryableError("audioAsrProviderUnavailable")


class UnavailableExternalMediaProcessor:
    """An explicitly configured, but not runnable, external processor.

    Keeping this as a processor rather than falling back to a local placeholder
    means the worker leaves an auditable retryable state.  It must never invent
    OCR or ASR text while configuration is incomplete.
    """

    def __init__(self, *, processor_id: str, reason_code: str) -> None:
        self.processor_id = _opaque_identifier(processor_id, field="processor_id")
        self.processor_version = "v1"
        self._reason_code = _opaque_identifier(reason_code, field="reason_code")

    def extract(self, *, source_object: Mapping[str, Any], payload: bytes) -> MediaTextExtraction:
        del source_object, payload
        raise OwnerTruthMediaProcessingRetryableError(self._reason_code)


class HTTPPrivateMediaTextProcessor:
    """Minimal private binary-to-text provider contract for OCR and ASR.

    The adapter deliberately sends no object URL, filename, owner, vault, or
    client identifier.  A provider receives only the explicitly-consented
    bytes, MIME type and media kind.  It must return JSON with either a
    non-empty ``text`` or ``transcript`` string.  This keeps concrete third
    party choice behind a server-side adapter contract rather than coupling the
    mobile client to one provider's credentials or response shape.
    """

    _RESPONSE_TEXT_FIELDS = ("text", "transcript")

    def __init__(
        self,
        *,
        processor_id: str,
        endpoint: str,
        api_key: str,
        timeout_seconds: float,
        max_payload_bytes: int,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.processor_id = _opaque_identifier(processor_id, field="processor_id")
        self.processor_version = "v1"
        self._endpoint = _external_processor_endpoint(endpoint)
        self._api_key = _external_processor_api_key(api_key)
        self._timeout_seconds = _external_processor_timeout(timeout_seconds)
        self._max_payload_bytes = _external_processor_max_payload_bytes(max_payload_bytes)
        self._client_factory = client_factory or httpx.Client

    def extract(self, *, source_object: Mapping[str, Any], payload: bytes) -> MediaTextExtraction:
        if not bool(source_object.get("externalProcessingAllowed", False)):
            raise OwnerTruthMediaProcessingTerminalError("externalMediaProcessingNotAuthorized")
        if len(payload) > self._max_payload_bytes:
            raise OwnerTruthMediaProcessingTerminalError("externalMediaProcessorPayloadTooLarge")
        media_kind = str(source_object.get("mediaKind") or "")
        content_type = str(source_object.get("contentType") or "")
        if media_kind not in {"image", "audio"} or not content_type:
            raise OwnerTruthMediaProcessingTerminalError("externalMediaProcessorInputInvalid")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": content_type,
            "X-DreamJourney-Media-Kind": media_kind,
            "X-DreamJourney-Processor-Contract": "owner-truth-media-text-v1",
        }
        try:
            with self._client_factory(
                timeout=self._timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    self._endpoint,
                    content=payload,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise OwnerTruthMediaProcessingRetryableError("externalMediaProcessorTimedOut") from exc
        except httpx.HTTPError as exc:
            raise OwnerTruthMediaProcessingRetryableError("externalMediaProcessorUnavailable") from exc
        except Exception as exc:
            raise OwnerTruthMediaProcessingRetryableError("externalMediaProcessorUnavailable") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise OwnerTruthMediaProcessingRetryableError("externalMediaProcessorUnavailable")
        if response.status_code < 200 or response.status_code >= 300:
            raise OwnerTruthMediaProcessingTerminalError("externalMediaProcessorRejected")
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise OwnerTruthMediaProcessingTerminalError("externalMediaProcessorResponseInvalid") from exc
        if not isinstance(body, Mapping):
            raise OwnerTruthMediaProcessingTerminalError("externalMediaProcessorResponseInvalid")
        for field in self._RESPONSE_TEXT_FIELDS:
            value = body.get(field)
            if isinstance(value, str) and value.strip():
                return MediaTextExtraction(
                    processor_id=self.processor_id,
                    processor_version=self.processor_version,
                    extracted_text=_normalize_extracted_text(value),
                )
        raise OwnerTruthMediaProcessingTerminalError("externalMediaProcessorResponseInvalid")


def _external_processor_endpoint(value: object) -> str:
    endpoint = str(value or "").strip()
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise OwnerTruthMediaProcessingError("external media processor endpoint is invalid")
    return endpoint


def _external_processor_api_key(value: object) -> str:
    key = str(value or "").strip()
    if not key or len(key) > 1024 or any(character.isspace() for character in key):
        raise OwnerTruthMediaProcessingError("external media processor api key is invalid")
    return key


def _external_processor_timeout(value: object) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise OwnerTruthMediaProcessingError("external media processor timeout is invalid") from exc
    if timeout < 1 or timeout > 120:
        raise OwnerTruthMediaProcessingError("external media processor timeout is invalid")
    return timeout


def _external_processor_max_payload_bytes(value: object) -> int:
    if type(value) is not int or value < 1 or value > 50 * 1024 * 1024:
        raise OwnerTruthMediaProcessingError("external media processor max payload is invalid")
    return value


class OwnerTruthMediaProcessorRouter:
    """Routes only to configured, explicit processors; no silent substitution."""

    def __init__(
        self,
        *,
        document_processor: OwnerTruthMediaProcessor | None = None,
        image_processor: OwnerTruthMediaProcessor | None = None,
        audio_processor: OwnerTruthMediaProcessor | None = None,
    ) -> None:
        self._document_processor = document_processor or LocalDocumentTextProcessor()
        self._image_processor = image_processor or DisabledImageOCRProcessor()
        self._audio_processor = audio_processor or DisabledAudioASRProcessor()

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        client_factory: Callable[..., Any] | None = None,
    ) -> "OwnerTruthMediaProcessorRouter":
        return cls(
            image_processor=_processor_from_settings(
                provider=getattr(settings, "owner_truth_media_image_ocr_provider", "disabled"),
                endpoint=getattr(settings, "owner_truth_media_image_ocr_url", None),
                api_key=getattr(settings, "owner_truth_media_image_ocr_api_key", None),
                timeout_seconds=getattr(
                    settings,
                    "owner_truth_media_external_processor_timeout_seconds",
                    30.0,
                ),
                max_payload_bytes=getattr(
                    settings,
                    "owner_truth_media_external_processor_max_payload_bytes",
                    10 * 1024 * 1024,
                ),
                processor_id="httpImageOCR",
                unavailable_processor_id="disabledImageOCR",
                unavailable_reason="imageOcrProviderUnavailable",
                configuration_reason="imageOcrProviderConfigurationInvalid",
                client_factory=client_factory,
            ),
            audio_processor=_processor_from_settings(
                provider=getattr(settings, "owner_truth_media_audio_asr_provider", "disabled"),
                endpoint=getattr(settings, "owner_truth_media_audio_asr_url", None),
                api_key=getattr(settings, "owner_truth_media_audio_asr_api_key", None),
                timeout_seconds=getattr(
                    settings,
                    "owner_truth_media_external_processor_timeout_seconds",
                    30.0,
                ),
                max_payload_bytes=getattr(
                    settings,
                    "owner_truth_media_external_processor_max_payload_bytes",
                    10 * 1024 * 1024,
                ),
                processor_id="httpAudioASR",
                unavailable_processor_id="disabledAudioASR",
                unavailable_reason="audioAsrProviderUnavailable",
                configuration_reason="audioAsrProviderConfigurationInvalid",
                client_factory=client_factory,
            ),
        )

    def extract(self, *, source_object: Mapping[str, Any], payload: bytes) -> MediaTextExtraction:
        media_kind = str(source_object.get("mediaKind") or "")
        if media_kind == "document":
            return self._document_processor.extract(source_object=source_object, payload=payload)
        if media_kind == "image":
            return self._image_processor.extract(source_object=source_object, payload=payload)
        if media_kind == "audio":
            return self._audio_processor.extract(source_object=source_object, payload=payload)
        if media_kind == "video":
            raise OwnerTruthMediaProcessingTerminalError("videoProcessingNotApplicable")
        raise OwnerTruthMediaProcessingTerminalError("mediaKindUnsupported")

    def identity_for(self, source_object: Mapping[str, Any]) -> tuple[str, str]:
        media_kind = str(source_object.get("mediaKind") or "")
        processor: OwnerTruthMediaProcessor | None = {
            "document": self._document_processor,
            "image": self._image_processor,
            "audio": self._audio_processor,
        }.get(media_kind)
        if processor is None:
            return ("videoStorageOnly", "v1") if media_kind == "video" else ("mediaProcessing", "v1")
        processor_id = _opaque_identifier(getattr(processor, "processor_id", ""), field="processor_id")
        processor_version = _opaque_identifier(
            getattr(processor, "processor_version", ""),
            field="processor_version",
        )
        return processor_id, processor_version


def _processor_from_settings(
    *,
    provider: object,
    endpoint: object,
    api_key: object,
    timeout_seconds: object,
    max_payload_bytes: object,
    processor_id: str,
    unavailable_processor_id: str,
    unavailable_reason: str,
    configuration_reason: str,
    client_factory: Callable[..., Any] | None,
) -> OwnerTruthMediaProcessor:
    normalized_provider = str(provider or "disabled").strip().lower()
    if normalized_provider in {"", "disabled"}:
        return UnavailableExternalMediaProcessor(
            processor_id=unavailable_processor_id,
            reason_code=unavailable_reason,
        )
    if normalized_provider != "httpjson":
        return UnavailableExternalMediaProcessor(
            processor_id=unavailable_processor_id,
            reason_code=configuration_reason,
        )
    try:
        return HTTPPrivateMediaTextProcessor(
            processor_id=processor_id,
            endpoint=str(endpoint or ""),
            api_key=str(api_key or ""),
            timeout_seconds=float(timeout_seconds),
            max_payload_bytes=int(max_payload_bytes),
            client_factory=client_factory,
        )
    except (TypeError, ValueError, OwnerTruthMediaProcessingError):
        return UnavailableExternalMediaProcessor(
            processor_id=unavailable_processor_id,
            reason_code=configuration_reason,
        )


class OwnerTruthMediaProcessingStore(Protocol):
    def owner_truth_media_source_object_repository(self) -> Any:
        ...

    def effect_kernel_repository(self) -> Any:
        ...


class OwnerTruthMediaProcessingCoordinator:
    """Adds one value-free processing effect after a verified private upload."""

    def __init__(self, store: OwnerTruthMediaProcessingStore) -> None:
        self._store = store

    def queue_verified_source_object(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_object: Mapping[str, Any],
    ) -> MediaProcessingEnqueueResult:
        if str(source_object.get("vaultId") or "") != context.vault_id:
            raise OwnerTruthMediaProcessingError("media source object vault does not match command context")
        if str(source_object.get("ownerSubjectId") or "") != context.owner_subject_id:
            raise OwnerTruthMediaProcessingError("media source object owner does not match command context")
        repository = self._store.owner_truth_media_source_object_repository()
        queued = repository.queue_processing(
            vault_id=context.vault_id,
            source_object_id=_uuid(source_object.get("sourceObjectId"), field="source_object_id"),
            owner_subject_id=context.owner_subject_id,
        )
        if str(queued.get("processingStatus") or "") == "notApplicable":
            return MediaProcessingEnqueueResult(source_object=queued, effect=None, intent=None)
        intent = build_media_source_object_processing_effect_intent(source_object=queued)
        effect = self._store.effect_kernel_repository().accept(intent)
        return MediaProcessingEnqueueResult(source_object=queued, effect=effect, intent=intent)


def build_media_source_object_processing_effect_intent(
    *,
    source_object: Mapping[str, Any],
) -> AsyncEffectIntent:
    if str(source_object.get("state") or "") != "verified":
        raise OwnerTruthMediaProcessingError("only verified media can request processing")
    if str(source_object.get("safetyStatus") or "") != "clean":
        raise OwnerTruthMediaProcessingError("only safety-cleared media can request processing")
    if str(source_object.get("processingStatus") or "") not in {"queued", "retryableFailed"}:
        raise OwnerTruthMediaProcessingError("media object is not queued for processing")
    source_object_id = _uuid(source_object.get("sourceObjectId"), field="source_object_id")
    storage_version = source_object.get("storageVersion")
    processing_generation = source_object.get("processingGeneration")
    authority_epoch = source_object.get("authorityEpoch")
    if type(storage_version) is not int or storage_version < 1:
        raise OwnerTruthMediaProcessingError("media object storage version is invalid")
    if type(processing_generation) is not int or processing_generation < 1:
        raise OwnerTruthMediaProcessingError("media object processing generation is invalid")
    if type(authority_epoch) is not int or authority_epoch < 0:
        raise OwnerTruthMediaProcessingError("media object authority epoch is invalid")
    payload_hash = _hash(
        _canonical_json(
            {
                "contentSha256": str(source_object.get("contentSha256") or ""),
                "magicMime": str(source_object.get("magicMime") or ""),
                "mediaKind": str(source_object.get("mediaKind") or ""),
                "schemaVersion": OWNER_TRUTH_MEDIA_PROCESSING_SCHEMA_VERSION,
                "sourceObjectId": source_object_id,
                "processingGeneration": processing_generation,
                "storageVersion": storage_version,
            }
        )
    )
    return AsyncEffectIntent(
        operation_type=OWNER_TRUTH_MEDIA_PROCESSING_OPERATION_TYPE,
        target=AsyncEffectTarget(
            owner_subject_id=str(source_object.get("ownerSubjectId") or ""),
            vault_id=str(source_object.get("vaultId") or ""),
            resource_type="mediaSourceObject",
            resource_id=source_object_id,
            # The immutable private byte version and a processing request are
            # separate lifecycles. A manual retry must create a new effect
            # without pretending that the uploaded file changed.
            resource_version=processing_generation,
            purpose="privateMediaProcessing",
            authority_epoch=authority_epoch,
        ),
        payload_hash=payload_hash,
        event_type=OWNER_TRUTH_MEDIA_PROCESSING_EVENT_TYPE,
        job_type=OWNER_TRUTH_MEDIA_PROCESSING_JOB_TYPE,
        max_attempts=OWNER_TRUTH_MEDIA_PROCESSING_MAX_ATTEMPTS,
    )


@dataclass(frozen=True)
class OwnerTruthMediaProcessingConsumerCommand(AsyncEffectConsumerCompletionCommand):
    """Typed terminal evidence for the private media processor worker."""

    processing_state: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            self.intent.operation_type != OWNER_TRUTH_MEDIA_PROCESSING_OPERATION_TYPE
            or self.intent.event_type != OWNER_TRUTH_MEDIA_PROCESSING_EVENT_TYPE
            or self.intent.job_type != OWNER_TRUTH_MEDIA_PROCESSING_JOB_TYPE
        ):
            raise OwnerTruthMediaProcessingError("media processing consumer requires its typed effect")
        target = self.intent.target
        if target.resource_type != "mediaSourceObject" or target.purpose != "privateMediaProcessing":
            raise OwnerTruthMediaProcessingError("media processing consumer requires its typed target")
        if self.consumer_name != OWNER_TRUTH_MEDIA_PROCESSING_CONSUMER:
            raise OwnerTruthMediaProcessingError("media processing consumer name is invalid")
        if self.business_target_key != self.intent.business_target_key:
            raise OwnerTruthMediaProcessingError("media processing consumer target is invalid")
        normalized_state = str(self.processing_state or "").strip()
        if normalized_state not in {"succeeded", "failed", "notApplicable"}:
            raise OwnerTruthMediaProcessingError("media processing terminal state is invalid")
        if normalized_state in {"succeeded", "notApplicable"} and self.outcome != "completed":
            raise OwnerTruthMediaProcessingError("successful media processing must complete")
        if normalized_state == "failed" and self.outcome != "failed":
            raise OwnerTruthMediaProcessingError("failed media processing must record failure")
        object.__setattr__(self, "processing_state", normalized_state)


def build_import_source_command(
    *,
    source_object: Mapping[str, Any],
    extraction: MediaTextExtraction,
) -> CreateTextSourceCommand:
    source_object_id = _uuid(source_object.get("sourceObjectId"), field="source_object_id")
    derived_source_id = str(
        uuid5(
            NAMESPACE_URL,
            "dreamjourney-owner-truth-media-import-source-v1:"
            f"{source_object_id}:{source_object['contentSha256']}:{extraction.extracted_text_sha256}",
        )
    )
    return CreateTextSourceCommand(
        command_id=(
            "media-processing:"
            f"{source_object_id}:{source_object['contentSha256']}:{extraction.processor_id}"
        ),
        source_id=derived_source_id,
        expected_version=0,
        text=extraction.extracted_text,
        metadata={
            "contentSha256": str(source_object["contentSha256"]),
            "extractedTextSha256": extraction.extracted_text_sha256,
            "mediaKind": str(source_object["mediaKind"]),
            "origin": "mediaSourceObjectProcessing",
            "processorId": extraction.processor_id,
            "processorVersion": extraction.processor_version,
            "sourceObjectId": source_object_id,
            "textTruncated": extraction.truncated,
        },
        source_kind=SourceKind.IMPORT,
        expected_authority_epoch=int(source_object["authorityEpoch"]),
    )


def build_media_processing_candidate_effect(
    *,
    context: OwnerTruthCommandContext,
    source_object: Mapping[str, Any],
    extraction: MediaTextExtraction,
    store: Any,
) -> tuple[str, EffectReceiptSummary]:
    """Persist one private import Source and request existing Candidate review work."""

    command = build_import_source_command(source_object=source_object, extraction=extraction)
    record = command.write_record(context=context)
    source = store.create_owner_truth_source(record)
    effect = store.effect_kernel_repository().accept(
        build_source_created_effect_intent(record=record, source=source)
    )
    return source.source_id, effect


def _normalize_extracted_text(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


__all__ = [
    "DisabledAudioASRProcessor",
    "DisabledImageOCRProcessor",
    "HTTPPrivateMediaTextProcessor",
    "LocalDocumentTextProcessor",
    "MediaProcessingEnqueueResult",
    "MediaTextExtraction",
    "OWNER_TRUTH_MEDIA_PROCESSING_CONSUMER",
    "OWNER_TRUTH_MEDIA_PROCESSING_EVENT_TYPE",
    "OWNER_TRUTH_MEDIA_PROCESSING_JOB_TYPE",
    "OWNER_TRUTH_MEDIA_PROCESSING_MAX_ATTEMPTS",
    "OWNER_TRUTH_MEDIA_PROCESSING_OPERATION_TYPE",
    "OWNER_TRUTH_MEDIA_PROCESSING_SCHEMA_VERSION",
    "OwnerTruthMediaProcessingConsumerCommand",
    "OwnerTruthMediaProcessingCoordinator",
    "OwnerTruthMediaProcessingError",
    "OwnerTruthMediaProcessingRetryableError",
    "OwnerTruthMediaProcessingTerminalError",
    "OwnerTruthMediaProcessorRouter",
    "UnavailableExternalMediaProcessor",
    "build_import_source_command",
    "build_media_processing_candidate_effect",
    "build_media_source_object_processing_effect_intent",
]
