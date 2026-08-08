#!/usr/bin/env python3
"""Exercise the configured private COS adapter without using user data.

This script is intended to run inside the deployed API container after the
server `.env` is configured. It writes a random, value-free probe below the
private prefix, verifies COS HEAD metadata and encryption, reads it back, and
then deletes it. It never prints credentials, bucket names, object keys, or
payloads.
"""

from __future__ import annotations

from hashlib import sha256
import secrets
import sys
from uuid import uuid4

from app.core.config import Settings
from app.services.owner_truth_media_source_object import (
    OwnerTruthMediaCaptureUnavailable,
    build_private_media_object_store,
)


def _object_store(settings: Settings):
    return build_private_media_object_store(
        provider=settings.owner_truth_media_storage_provider,
        root=settings.owner_truth_media_storage_root,
        s3_bucket=settings.owner_truth_media_s3_bucket,
        s3_prefix=settings.owner_truth_media_s3_prefix,
        s3_region=settings.owner_truth_media_s3_region,
        s3_endpoint_url=settings.owner_truth_media_s3_endpoint_url,
        s3_access_key_id=settings.owner_truth_media_s3_access_key_id,
        s3_secret_access_key=settings.owner_truth_media_s3_secret_access_key,
        s3_server_side_encryption=settings.owner_truth_media_s3_server_side_encryption,
        s3_kms_key_id=settings.owner_truth_media_s3_kms_key_id,
    )


def main() -> int:
    settings = Settings.from_env()
    store = _object_store(settings)
    if store.provider_name != "cos":
        raise RuntimeError("COS private media storage is not configured")

    payload = b"dreamjourney-owner-truth-cos-provider-smoke:" + secrets.token_bytes(16)
    content_sha256 = sha256(payload).hexdigest()
    storage_key = f"owner-truth/provider-smoke/{uuid4()}/{content_sha256}.bin"
    wrote = False
    try:
        store.write(
            storage_key=storage_key,
            payload=payload,
            content_type="application/octet-stream",
            content_sha256=content_sha256,
        )
        wrote = True
        store.verify_upload(
            storage_key=storage_key,
            expected_file_size_bytes=len(payload),
            expected_content_type="application/octet-stream",
            expected_content_sha256=content_sha256,
        )
        if store.read(storage_key=storage_key) != payload:
            raise RuntimeError("COS private media readback verification failed")
    finally:
        if wrote:
            store.delete(storage_key=storage_key)

    print("Owner Truth COS private media provider smoke passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OwnerTruthMediaCaptureUnavailable, RuntimeError) as error:
        print(f"Owner Truth COS private media provider smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
