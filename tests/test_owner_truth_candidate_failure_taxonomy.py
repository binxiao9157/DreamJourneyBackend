from __future__ import annotations

import unittest

import httpx

from app.async_effects.owner_truth_candidate_extraction_worker import (
    _classify_candidate_extraction_failure,
)
from app.services.owner_truth_interview_candidate_proposal import (
    _candidate_extraction_failure_category,
)


class OwnerTruthCandidateFailureTaxonomyTests(unittest.TestCase):
    def test_provider_and_runtime_failures_keep_distinct_safe_categories(self) -> None:
        request = httpx.Request("POST", "https://provider.invalid/v1/organize")
        cases = (
            (
                httpx.HTTPStatusError(
                    "private authorization response",
                    request=request,
                    response=httpx.Response(401, request=request),
                ),
                "candidateExtraction.providerAuthorization.rejected",
                "authorization",
                False,
            ),
            (
                httpx.HTTPStatusError(
                    "private quota response",
                    request=request,
                    response=httpx.Response(402, request=request),
                ),
                "candidateExtraction.providerQuota.unavailable",
                "quota",
                False,
            ),
            (
                httpx.HTTPStatusError(
                    "private rate-limit response",
                    request=request,
                    response=httpx.Response(429, request=request),
                ),
                "candidateExtraction.providerRateLimited",
                "rateLimit",
                True,
            ),
            (
                httpx.ReadTimeout("private timeout response", request=request),
                "candidateExtraction.providerRequest.timeout",
                "timeout",
                True,
            ),
            (
                ValueError("private malformed provider payload"),
                "candidateExtraction.responseContract.invalid",
                "contract",
                False,
            ),
            (
                httpx.ConnectError("private transport response", request=request),
                "candidateExtraction.providerRequest.transport",
                "transport",
                True,
            ),
            (
                RuntimeError("private runtime configuration"),
                "candidateExtraction.runtime.blocked",
                "configuration",
                False,
            ),
        )

        for error, code, category, retryable in cases:
            with self.subTest(category=category):
                failure = _classify_candidate_extraction_failure(error)
                self.assertEqual(failure.code, code)
                self.assertEqual(failure.retryable, retryable)
                self.assertEqual(
                    _candidate_extraction_failure_category(failure.code),
                    category,
                )

    def test_retry_exhaustion_is_not_misreported_as_the_original_failure(self) -> None:
        self.assertEqual(
            _candidate_extraction_failure_category(
                "candidateExtraction.providerRequest.timeout"
            ),
            "timeout",
        )
        self.assertEqual(
            _candidate_extraction_failure_category(
                "candidateExtractionRetriesExhausted"
            ),
            "internal",
        )


if __name__ == "__main__":
    unittest.main()
