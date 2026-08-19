"""Value-bounded child process for private TXT/Markdown/PDF/DOCX extraction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from app.services.owner_truth_media_processing import (
    LocalDocumentTextProcessor,
    OwnerTruthMediaProcessingRetryableError,
    OwnerTruthMediaProcessingTerminalError,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--input", required=True)
    parser.add_argument("--content-type", required=True)
    parser.add_argument("--max-input-bytes", required=True, type=int)
    parser.add_argument("--max-memory-bytes", required=True, type=int)
    parser.add_argument("--max-cpu-seconds", required=True, type=int)
    parser.add_argument("--max-pdf-pages", required=True, type=int)
    parser.add_argument("--max-docx-entries", required=True, type=int)
    parser.add_argument("--max-docx-uncompressed-bytes", required=True, type=int)
    parser.add_argument("--max-docx-compression-ratio", required=True, type=int)
    return parser


def _apply_resource_limits(*, max_memory_bytes: int, max_cpu_seconds: int) -> None:
    try:
        import resource
    except ImportError:  # pragma: no cover - production containers are Unix
        return
    cpu_limit = max(1, int(max_cpu_seconds))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
    if sys.platform.startswith("linux"):
        memory_limit = max(64 * 1024 * 1024, int(max_memory_bytes))
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    except (OSError, ValueError):
        pass


def _write(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        _apply_resource_limits(
            max_memory_bytes=args.max_memory_bytes,
            max_cpu_seconds=args.max_cpu_seconds,
        )
        path = Path(args.input)
        if path.stat().st_size > args.max_input_bytes:
            raise OwnerTruthMediaProcessingTerminalError("documentInputTooLarge")
        with path.open("rb") as handle:
            payload = handle.read(args.max_input_bytes + 1)
        if len(payload) > args.max_input_bytes:
            raise OwnerTruthMediaProcessingTerminalError("documentInputTooLarge")
        extraction = LocalDocumentTextProcessor(
            max_pdf_pages=args.max_pdf_pages,
            max_docx_entries=args.max_docx_entries,
            max_docx_uncompressed_bytes=args.max_docx_uncompressed_bytes,
            max_docx_compression_ratio=args.max_docx_compression_ratio,
        ).extract(
            source_object={"contentType": args.content_type},
            payload=payload,
        )
        _write(
            {
                "status": "succeeded",
                "extractedText": extraction.extracted_text,
                "truncated": extraction.truncated,
                "fragmentEvidence": list(extraction.fragment_evidence),
            }
        )
        return 0
    except OwnerTruthMediaProcessingRetryableError as error:
        _write(
            {
                "status": "failed",
                "errorType": "retryable",
                "reason": error.reason_code,
            }
        )
        return 2
    except OwnerTruthMediaProcessingTerminalError as error:
        _write(
            {
                "status": "failed",
                "errorType": "terminal",
                "reason": error.reason_code,
            }
        )
        return 3
    except (OSError, MemoryError):
        _write(
            {
                "status": "failed",
                "errorType": "retryable",
                "reason": "documentParserResourceLimitExceeded",
            }
        )
        return 4
    except Exception:
        _write(
            {
                "status": "failed",
                "errorType": "terminal",
                "reason": "documentParserFailed",
            }
        )
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
