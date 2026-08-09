from __future__ import annotations

from io import BytesIO
import subprocess
import unittest

from app.services.owner_truth_media_processing import (
    IsolatedDocumentTextProcessor,
    OwnerTruthMediaProcessingRetryableError,
    OwnerTruthMediaProcessingTerminalError,
)


class OwnerTruthDocumentParserRuntimeTests(unittest.TestCase):
    def test_text_is_parsed_in_subprocess_with_hashed_fragment_evidence(self) -> None:
        processor = IsolatedDocumentTextProcessor(timeout_seconds=5)

        extraction = processor.extract(
            source_object={"contentType": "text/plain"},
            payload=b"First private line.\nSecond private line.",
        )

        self.assertEqual(extraction.processor_id, "isolatedDocumentText")
        self.assertEqual(extraction.extracted_text, "First private line.\nSecond private line.")
        self.assertEqual(len(extraction.fragment_evidence), 2)
        self.assertEqual(extraction.fragment_evidence[0]["locatorType"], "line")
        self.assertEqual(extraction.fragment_evidence[0]["locatorValue"], 1)
        self.assertEqual(len(extraction.fragment_evidence[0]["textSha256"]), 64)
        self.assertNotIn("First private line", str(extraction.fragment_evidence))

    def test_timeout_is_retryable_and_does_not_copy_parser_output(self) -> None:
        private_marker = "private-parser-timeout-marker"

        def timeout_runner(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("parser", 1, output=private_marker)

        processor = IsolatedDocumentTextProcessor(
            timeout_seconds=1,
            runner=timeout_runner,
        )

        with self.assertRaises(OwnerTruthMediaProcessingRetryableError) as raised:
            processor.extract(
                source_object={"contentType": "text/plain"},
                payload=private_marker.encode("utf-8"),
            )

        self.assertEqual(raised.exception.reason_code, "documentParserTimedOut")
        self.assertNotIn(private_marker, str(raised.exception))

    def test_docx_archive_entry_limit_is_terminal_before_document_parse(self) -> None:
        from docx import Document

        buffer = BytesIO()
        document = Document()
        document.add_paragraph("Private DOCX text")
        document.save(buffer)
        processor = IsolatedDocumentTextProcessor(
            timeout_seconds=5,
            max_docx_entries=1,
        )

        with self.assertRaises(OwnerTruthMediaProcessingTerminalError) as raised:
            processor.extract(
                source_object={
                    "contentType": (
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    )
                },
                payload=buffer.getvalue(),
            )

        self.assertEqual(raised.exception.reason_code, "docxArchiveEntryLimitExceeded")

    def test_encrypted_pdf_is_rejected_with_stable_terminal_reason(self) -> None:
        from pypdf import PdfWriter

        buffer = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.encrypt("private-password")
        writer.write(buffer)

        with self.assertRaises(OwnerTruthMediaProcessingTerminalError) as raised:
            IsolatedDocumentTextProcessor(timeout_seconds=5).extract(
                source_object={"contentType": "application/pdf"},
                payload=buffer.getvalue(),
            )

        self.assertEqual(raised.exception.reason_code, "pdfEncryptedUnsupported")

    def test_input_limit_is_checked_before_starting_subprocess(self) -> None:
        called = False

        def runner(*_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("runner must not be called")

        processor = IsolatedDocumentTextProcessor(max_input_bytes=4, runner=runner)

        with self.assertRaises(OwnerTruthMediaProcessingTerminalError) as raised:
            processor.extract(
                source_object={"contentType": "text/plain"},
                payload=b"12345",
            )

        self.assertEqual(raised.exception.reason_code, "documentInputTooLarge")
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
