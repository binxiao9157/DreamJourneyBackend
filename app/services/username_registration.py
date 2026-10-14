"""Invite-only username enrollment; does not claim verified phone ownership."""
from datetime import datetime, timezone
import secrets
from typing import Mapping
from app.services.passwords import make_password_credential


def invitation_is_available(item, now_iso: str) -> bool:
    if not isinstance(item, Mapping) or item.get("invitationKind") != "accountRegistration":
        return False
    if item.get("registrationSubjectId") or item.get("invitationStatus") != "pending" or item.get("accessStatus") != "pending":
        return False
    try:
        expires = datetime.fromisoformat(str(item["invitationExpiresAt"]).replace("Z", "+00:00"))
        return expires > datetime.fromisoformat(now_iso)
    except (KeyError, ValueError, TypeError):
        return False


def register_username(store, identity, *, code: str, username: str, password: str, nickname: str = "") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    code = str(code or "").strip().upper()
    if not 16 <= len(code) <= 96 or not 8 <= len(password) <= 128:
        raise ValueError("usernameRegistrationInvalid")
    # Check admission before the expensive password hash; atomic store rechecks.
    if not invitation_is_available(store.get_family_invitation(code), now):
        raise ValueError("usernameRegistrationUnavailable")
    reference = identity.target_reference(identity_type="username", target=username)
    key_state = store.ensure_identity_hash_key_version(identity.hmac_key_version, identity._hmac_key_fingerprint)
    if key_state.get("outcome") != "ready":
        raise ValueError("usernameRegistrationUnavailable")
    subject = "sub_" + secrets.token_urlsafe(24)
    display = str(nickname or username).strip()[:80]
    if not store.register_invited_username(code=code, reference=reference, subject_id=subject,
            binding_id="idb_" + secrets.token_urlsafe(24), credential=make_password_credential(password),
            nickname=display, now_iso=now):
        raise ValueError("usernameRegistrationUnavailable")
    return {"status": "registered", "contractVersion": 2, "user": {"id": subject, "nickname": display}}
