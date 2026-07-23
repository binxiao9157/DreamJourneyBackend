#!/usr/bin/env python3
"""Verify the deployed Voice/DH scoped capability shadow remains default-deny.

This smoke intentionally creates no route request, database row, capability,
provider effect, session, credential lookup, or media side effect. It exists so
the production API container can prove the same fail-closed import/runtime
boundary without bundling the repository's unit-test package in the image.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

from app.services.voice_dh_authority import VoiceDHProvider, VoiceDHPurpose
from app.services.voice_dh_scoped_capability_shadow import (
    ScopedCapabilityAdmissionRequest,
    ScopedCapabilityAdmissionShadow,
    ScopedCapabilityAuthorityContext,
    ScopedCapabilityShadowDisposition,
)


NOW = datetime(2026, 7, 23, 3, 0, tzinfo=timezone.utc)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def main() -> None:
    require(
        any(path.exists() for path in (Path("/.dockerenv"), Path("/run/.containerenv"))),
        "scoped capability deployed smoke must run inside the deployed API container",
    )
    context = ScopedCapabilityAuthorityContext(
        vault_id="vault-scoped-capability-deployed-smoke",
        owner_subject_id="owner-scoped-capability-deployed-smoke",
        actor_subject_id="owner-scoped-capability-deployed-smoke",
        authority_epoch=0,
        audience="iosEchoRuntime",
    )
    request = ScopedCapabilityAdmissionRequest(
        request_id="scoped-capability-deployed-smoke-001",
        vault_id=context.vault_id,
        owner_subject_id=context.owner_subject_id,
        actor_subject_id=context.actor_subject_id,
        subject_id=context.owner_subject_id,
        authority_epoch=context.authority_epoch,
        purpose=VoiceDHPurpose.DH_AUDIO_DRIVE,
        provider=VoiceDHProvider.TENCENT_DIGITAL_HUMAN,
        resource="digitalHumanSession",
        nonce_hash=digest("deployed-smoke-nonce"),
        issued_at=NOW - timedelta(seconds=15),
        expires_at=NOW + timedelta(seconds=45),
        audience=context.audience,
        one_time=True,
        request_hash=digest("deployed-smoke-request"),
    )
    result = ScopedCapabilityAdmissionShadow().observe(
        context=context,
        request=request,
        enabled=True,
        now=NOW,
    )
    require(
        result.disposition is ScopedCapabilityShadowDisposition.BLOCKED,
        "G0 shadow must never admit a deployed capability",
    )
    summary = result.value_free_summary()
    for field in (
        "capabilityIssued",
        "nonceConsumed",
        "providerEffectAllowed",
        "providerEffectPerformed",
        "replayProtectionPersistent",
        "releaseVisible",
    ):
        require(summary[field] is False, f"{field} must remain false")
    for reason in (
        "g0NoCapabilityIssuer",
        "g2BrokerDeploymentRequired",
        "g3ProviderCredentialEvidenceRequired",
        "oneTimeReplayProtectionNotPersistent",
        "releasePolicyDefaultOff",
    ):
        require(reason in summary["reasonCodes"], f"missing default-deny reason: {reason}")
    serialized = json.dumps(summary, sort_keys=True)
    for forbidden in (request.request_id, request.request_hash, request.nonce_hash):
        require(forbidden not in serialized, "deployed smoke summary leaked an input value")
    print(
        "voiceDhScopedCapabilityShadowG0=true "
        f"status={summary['status']} "
        f"capabilityIssued={summary['capabilityIssued']} "
        f"providerEffectPerformed={summary['providerEffectPerformed']} "
        f"replayProtectionPersistent={summary['replayProtectionPersistent']}"
    )


if __name__ == "__main__":
    main()
