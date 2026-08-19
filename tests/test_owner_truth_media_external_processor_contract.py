from __future__ import annotations

import unittest

import httpx

from app.core.config import Settings
from app.services.owner_truth_media_processing import (
    OwnerTruthMediaProcessingRetryableError,
    OwnerTruthMediaProcessingTerminalError,
    OwnerTruthMediaProcessorRouter,
)


class OwnerTruthMediaExternalProcessorContractTests(unittest.TestCase):
    def test_configured_image_ocr_sends_only_consented_bytes_and_returns_text(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"text": "照片中的手写生日祝福"})

        router = OwnerTruthMediaProcessorRouter.from_settings(
            Settings(
                owner_truth_media_image_ocr_provider="httpJson",
                owner_truth_media_image_ocr_url="https://ocr.private.example.test/extract",
                owner_truth_media_image_ocr_api_key="private-test-key",
            ),
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            ),
        )

        extraction = router.extract(
            source_object={
                "mediaKind": "image",
                "contentType": "image/png",
                "externalProcessingAllowed": True,
                "vaultId": "must-not-be-sent",
                "ownerSubjectId": "must-not-be-sent",
                "fileName": "must-not-be-sent.png",
            },
            payload=b"\x89PNG\r\n\x1a\nprivate-image-bytes",
        )

        self.assertEqual(extraction.processor_id, "httpImageOCR")
        self.assertEqual(extraction.extracted_text, "照片中的手写生日祝福")
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.url, httpx.URL("https://ocr.private.example.test/extract"))
        self.assertEqual(request.headers["content-type"], "image/png")
        self.assertEqual(request.headers["authorization"], "Bearer private-test-key")
        self.assertEqual(request.headers["x-dreamjourney-media-kind"], "image")
        self.assertEqual(
            request.headers["x-dreamjourney-processor-contract"],
            "owner-truth-image-understanding-v1",
        )
        self.assertEqual(request.content, b"\x89PNG\r\n\x1a\nprivate-image-bytes")
        self.assertNotIn("must-not-be-sent", request.headers.raw.__repr__())
        self.assertNotIn("must-not-be-sent", request.url.raw_path.decode("ascii"))

    def test_image_understanding_contract_returns_inferred_review_facets(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                json={
                    "schemaVersion": "owner-truth-image-understanding-v1",
                    "description": "一家人在杭州西湖合影。",
                    "ocrText": "2025 年春节",
                    "facets": {
                        "people": [{"value": "母亲", "confidence": 0.92}],
                        "time": [{"value": "2025 年春节", "confidence": 0.8}],
                        "places": [{"value": "杭州西湖", "confidence": 0.86}],
                    },
                },
            )

        router = OwnerTruthMediaProcessorRouter.from_settings(
            Settings(
                owner_truth_media_image_ocr_provider="httpJson",
                owner_truth_media_image_ocr_url="https://ocr.private.example.test/extract",
                owner_truth_media_image_ocr_api_key="private-test-key",
            ),
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            ),
        )

        extraction = router.extract(
            source_object={
                "mediaKind": "image",
                "contentType": "image/jpeg",
                "externalProcessingAllowed": True,
            },
            payload=b"private-image-bytes",
        )

        self.assertEqual(extraction.processor_version, "v2")
        self.assertEqual(extraction.extracted_text, "一家人在杭州西湖合影。\n2025 年春节")
        self.assertEqual(extraction.fragment_evidence[0]["locatorType"], "image")
        self.assertEqual(extraction.candidate_facets["people"][0], {
            "value": "母亲",
            "evidenceMode": "inferred",
            "confidence": 0.92,
        })
        self.assertEqual(extraction.candidate_facets["time"][0]["value"], "2025 年春节")
        self.assertEqual(extraction.candidate_facets["places"][0]["value"], "杭州西湖")
        self.assertEqual(len(extraction.candidate_facets_hash), 64)

    def test_malformed_image_understanding_contract_fails_closed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                json={
                    "schemaVersion": "owner-truth-image-understanding-v1",
                    "description": "这段描述不能掩盖无效线索。",
                    "facets": {
                        "people": [{"value": "母亲", "confidence": 1.5}],
                    },
                    "text": "legacy fallback must not be accepted",
                },
            )

        router = OwnerTruthMediaProcessorRouter.from_settings(
            Settings(
                owner_truth_media_image_ocr_provider="httpJson",
                owner_truth_media_image_ocr_url="https://ocr.private.example.test/extract",
                owner_truth_media_image_ocr_api_key="private-test-key",
            ),
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            ),
        )

        with self.assertRaisesRegex(
            OwnerTruthMediaProcessingTerminalError,
            "externalMediaProcessorResponseInvalid",
        ):
            router.extract(
                source_object={
                    "mediaKind": "image",
                    "contentType": "image/jpeg",
                    "externalProcessingAllowed": True,
                },
                payload=b"private-image-bytes",
            )

    def test_configured_audio_asr_accepts_transcript_without_disclosing_source_metadata(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"transcript": "这是一次私密的语音回忆"})

        router = OwnerTruthMediaProcessorRouter.from_settings(
            Settings(
                owner_truth_media_audio_asr_provider="httpJson",
                owner_truth_media_audio_asr_url="https://asr.private.example.test/transcribe",
                owner_truth_media_audio_asr_api_key="private-test-key",
            ),
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            ),
        )

        extraction = router.extract(
            source_object={
                "mediaKind": "audio",
                "contentType": "audio/mpeg",
                "externalProcessingAllowed": True,
            },
            payload=b"ID3private-audio-bytes",
        )

        self.assertEqual(extraction.processor_id, "httpAudioASR")
        self.assertEqual(extraction.extracted_text, "这是一次私密的语音回忆")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].headers["x-dreamjourney-media-kind"], "audio")
        self.assertEqual(requests[0].content, b"ID3private-audio-bytes")

    def test_external_processor_never_runs_without_upload_time_permission(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            del request
            calls += 1
            return httpx.Response(200, json={"text": "must not be returned"})

        router = OwnerTruthMediaProcessorRouter.from_settings(
            Settings(
                owner_truth_media_image_ocr_provider="httpJson",
                owner_truth_media_image_ocr_url="https://ocr.private.example.test/extract",
                owner_truth_media_image_ocr_api_key="private-test-key",
            ),
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            ),
        )

        with self.assertRaisesRegex(
            OwnerTruthMediaProcessingTerminalError,
            "externalMediaProcessingNotAuthorized",
        ):
            router.extract(
                source_object={
                    "mediaKind": "image",
                    "contentType": "image/png",
                    "externalProcessingAllowed": False,
                },
                payload=b"private-image-bytes",
            )

        self.assertEqual(calls, 0)

    def test_provider_transient_failure_is_retryable_and_provider_rejection_is_terminal(self) -> None:
        def unavailable(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(503, json={"error": "do not retain provider detail"})

        router = OwnerTruthMediaProcessorRouter.from_settings(
            Settings(
                owner_truth_media_image_ocr_provider="httpJson",
                owner_truth_media_image_ocr_url="https://ocr.private.example.test/extract",
                owner_truth_media_image_ocr_api_key="private-test-key",
            ),
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(unavailable),
                **kwargs,
            ),
        )
        source_object = {
            "mediaKind": "image",
            "contentType": "image/png",
            "externalProcessingAllowed": True,
        }

        with self.assertRaisesRegex(
            OwnerTruthMediaProcessingRetryableError,
            "externalMediaProcessorUnavailable",
        ):
            router.extract(source_object=source_object, payload=b"private-image-bytes")

        def rejected(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(422, json={"error": "do not retain provider detail"})

        rejected_router = OwnerTruthMediaProcessorRouter.from_settings(
            Settings(
                owner_truth_media_image_ocr_provider="httpJson",
                owner_truth_media_image_ocr_url="https://ocr.private.example.test/extract",
                owner_truth_media_image_ocr_api_key="private-test-key",
            ),
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(rejected),
                **kwargs,
            ),
        )
        with self.assertRaisesRegex(
            OwnerTruthMediaProcessingTerminalError,
            "externalMediaProcessorRejected",
        ):
            rejected_router.extract(source_object=source_object, payload=b"private-image-bytes")

    def test_invalid_or_missing_provider_configuration_stays_unavailable(self) -> None:
        router = OwnerTruthMediaProcessorRouter.from_settings(
            Settings(
                owner_truth_media_image_ocr_provider="httpJson",
                owner_truth_media_image_ocr_url="http://not-private.example.test/extract",
                owner_truth_media_image_ocr_api_key="",
            )
        )

        self.assertEqual(router.identity_for({"mediaKind": "image"}), ("disabledImageOCR", "v1"))
        with self.assertRaisesRegex(
            OwnerTruthMediaProcessingRetryableError,
            "imageOcrProviderConfigurationInvalid",
        ):
            router.extract(
                source_object={
                    "mediaKind": "image",
                    "contentType": "image/png",
                    "externalProcessingAllowed": True,
                },
                payload=b"private-image-bytes",
            )


if __name__ == "__main__":
    unittest.main()
