from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.services.delegated_access import (
    AccessGrantPurpose,
    DelegatedAccessService,
    GrantOperation,
    ResourceScopeType,
)


class TimeLetterAccessError(ValueError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_LOCAL_CONTENT_KEYS = {
    "localPath",
    "fileURL",
    "absolutePath",
    "imageLocalPath",
    "localImagePath",
    "rawAudioURL",
    "rawVideoURL",
    "rawTranscript",
}


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_open(open_at: str, now_iso: str) -> bool:
    open_datetime = _parse_iso_datetime(open_at)
    now_datetime = _parse_iso_datetime(now_iso)
    if open_datetime is not None and now_datetime is not None:
        return open_datetime <= now_datetime
    return bool(open_at and now_iso and open_at <= now_iso)


def is_time_letter_open(
    item: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    open_at = str(item.get("openAt") or metadata.get("openAt") or "").strip()
    effective_now = now or datetime.now(timezone.utc)
    return _is_open(open_at, effective_now.astimezone(timezone.utc).isoformat())


def time_letter_recipient_records(item: Dict[str, Any]) -> list[Dict[str, str]]:
    recipients = item.get("recipients")
    if isinstance(recipients, list):
        records = []
        for recipient in recipients:
            if not isinstance(recipient, dict):
                continue
            recipient_id = str(recipient.get("id") or "").strip()
            if not recipient_id:
                continue
            records.append(
                {
                    "id": recipient_id,
                    "name": str(recipient.get("name") or recipient_id).strip() or recipient_id,
                    "type": str(recipient.get("type") or ("self" if recipient_id == "self" else "family")).strip(),
                }
            )
        if records:
            return records

    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    recipient_ids = [
        token.strip()
        for token in str(metadata.get("recipientIds") or "").replace(",", "|").split("|")
        if token.strip()
    ]
    recipient_names = [
        token.strip()
        for token in str(metadata.get("recipientNames") or "").replace("、", "|").replace(",", "|").split("|")
        if token.strip()
    ]
    return [
        {
            "id": recipient_id,
            "name": recipient_names[index] if index < len(recipient_names) else recipient_id,
            "type": "self" if recipient_id == "self" else "family",
        }
        for index, recipient_id in enumerate(recipient_ids)
    ]


def family_member_for_recipient(
    store: Any,
    owner_user_id: str,
    recipient_id: str,
) -> Optional[Dict[str, Any]]:
    for member in store.list_family_members(owner_user_id):
        if str(member.get("id") or "") != recipient_id:
            continue
        return member
    return None


def _family_member_is_active(member: Dict[str, Any]) -> bool:
    return member.get("accessStatus") == "active" and member.get("invitationStatus") == "accepted"


def active_family_member_for_recipient(
    store: Any,
    owner_user_id: str,
    recipient_id: str,
    *,
    time_letter_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    member = family_member_for_recipient(store, owner_user_id, recipient_id)
    if member is not None:
        if member.get("accessStatus") == "active" and member.get("invitationStatus") == "accepted":
            relationship = store.get_family_relationship_by_member(owner_user_id, recipient_id)
            if relationship is None:
                return None
            member_subject_id = str(relationship.get("memberSubjectId") or "").strip()
            access = DelegatedAccessService(store).authorize(
                owner_subject_id=owner_user_id,
                grantee_subject_id=member_subject_id,
                family_member_id=recipient_id,
                purpose=AccessGrantPurpose.TIME_LETTER_READ,
                operation=GrantOperation.READ,
                resource_type=ResourceScopeType.TIME_LETTER,
                resource_id=time_letter_id,
            )
            if access.allowed:
                return member
        return None
    return None


def time_letter_in_app_reminder_payloads(
    store: Any,
    item: Dict[str, Any],
    delivered_at_iso: str,
) -> list[tuple[str, Dict[str, Any]]]:
    owner_user_id = str(item.get("userId") or "").strip()
    item_id = str(item.get("id") or "").strip()
    title = str(item.get("title") or "时间信件").strip() or "时间信件"
    open_at = str(item.get("openAt") or (item.get("metadata") or {}).get("openAt") or "").strip()
    if not owner_user_id or not item_id:
        return []

    reminders: list[tuple[str, Dict[str, Any]]] = []
    seen_reminder_ids: set[str] = set()

    def append_reminder(target_user_id: str, recipient_id: str, recipient_name: str, role: str) -> None:
        reminder_id = f"time-letter-{item_id}-{recipient_id}"
        if reminder_id in seen_reminder_ids:
            return
        seen_reminder_ids.add(reminder_id)
        reminders.append(
            (
                target_user_id,
                {
                    "id": reminder_id,
                    "kind": "timeLetterReminder",
                    "ownerUserId": owner_user_id,
                    "sourceArchiveItemId": item_id,
                    "recipientId": recipient_id,
                    "recipientName": recipient_name,
                    "recipientRole": role,
                    "title": f"{title}已到打开时间",
                    "deliverAt": open_at,
                    "deliveredAt": delivered_at_iso,
                    "status": "unread",
                    "boundaryAcknowledged": True,
                    "metadataOnly": True,
                    "contentRedacted": True,
                    "privacyMetadata": {"scope": "generationAllowed"},
                },
            )
        )

    append_reminder(owner_user_id, "self", "我", "owner")

    for recipient in time_letter_recipient_records(item):
        recipient_id = recipient["id"]
        if recipient_id == "self":
            continue
        member = active_family_member_for_recipient(
            store,
            owner_user_id,
            recipient_id,
            time_letter_id=item_id,
        )
        if member is None:
            continue
        relationship = store.get_family_relationship_by_member(owner_user_id, recipient_id)
        target_user_id = str((relationship or {}).get("memberSubjectId") or "").strip()
        if not target_user_id:
            continue
        append_reminder(
            target_user_id,
            recipient_id,
            str(member.get("name") or recipient.get("name") or recipient_id).strip() or recipient_id,
            "recipient",
        )

    return reminders


def dispatch_due_time_letters_for_store(store: Any, now_iso: str, limit: int) -> Dict[str, Any]:
    items = store.mark_due_time_letters_delivered(
        cutoff_iso=now_iso,
        delivered_at_iso=now_iso,
        limit=limit,
    )
    reminders = []
    for item in items:
        for target_user_id, reminder_payload in time_letter_in_app_reminder_payloads(store, item, now_iso):
            reminders.append(store.add_mailbox_letter(target_user_id, reminder_payload))

    return {
        "status": "dispatched",
        "cutoff": now_iso,
        "itemCount": len(items),
        "reminderCount": len(reminders),
        "items": items,
        "reminders": reminders,
        "providerDeliveryAttempted": False,
    }


def _find_time_letter(store: Any, owner_user_id: str, item_id: str) -> Dict[str, Any]:
    for item in store.list_archive_items(owner_user_id):
        if str(item.get("id") or "") == item_id and str(item.get("kind") or "").strip() == "timeLetter":
            return item
    raise TimeLetterAccessError(404, "timeLetter not found")


def _safe_detail_item(item: Dict[str, Any]) -> Dict[str, Any]:
    safe_item = deepcopy(item)
    for key in _LOCAL_CONTENT_KEYS:
        safe_item.pop(key, None)
    metadata = safe_item.get("metadata")
    if isinstance(metadata, dict):
        safe_metadata = deepcopy(metadata)
        for key in _LOCAL_CONTENT_KEYS:
            safe_metadata.pop(key, None)
        safe_item["metadata"] = safe_metadata
    safe_item["metadataOnly"] = False
    safe_item["contentRedacted"] = False
    return safe_item


def time_letter_detail_for_viewer(
    store: Any,
    owner_user_id: str,
    item_id: str,
    viewer_user_id: str,
    now_iso: str,
) -> Dict[str, Any]:
    owner_user_id = str(owner_user_id or "").strip()
    item_id = str(item_id or "").strip()
    viewer_user_id = str(viewer_user_id or "").strip()
    if not owner_user_id or not item_id:
        raise TimeLetterAccessError(400, "ownerUserId and itemId are required")
    if not viewer_user_id:
        raise TimeLetterAccessError(400, "viewerUserId is required")

    item = _find_time_letter(store, owner_user_id, item_id)
    open_at = str(item.get("openAt") or (item.get("metadata") or {}).get("openAt") or "").strip()
    if not _is_open(open_at, now_iso):
        raise TimeLetterAccessError(403, "timeLetter is not open yet")

    if viewer_user_id == owner_user_id:
        return {
            "status": "available",
            "access": {"role": "owner", "viewerUserId": viewer_user_id, "ownerUserId": owner_user_id},
            "item": _safe_detail_item(item),
        }

    for recipient in time_letter_recipient_records(item):
        recipient_id = recipient["id"]
        if recipient_id == "self":
            continue
        member = family_member_for_recipient(store, owner_user_id, recipient_id)
        if member is None:
            continue
        relationship = store.get_family_relationship_by_member(owner_user_id, recipient_id)
        recipient_user_id = str((relationship or {}).get("memberSubjectId") or "").strip()
        if viewer_user_id == recipient_user_id:
            if not _family_member_is_active(member):
                raise TimeLetterAccessError(403, "family recipient is not active")
            access = DelegatedAccessService(store).authorize(
                owner_subject_id=owner_user_id,
                grantee_subject_id=viewer_user_id,
                family_member_id=recipient_id,
                purpose=AccessGrantPurpose.TIME_LETTER_READ,
                operation=GrantOperation.READ,
                resource_type=ResourceScopeType.TIME_LETTER,
                resource_id=item_id,
            )
            if not access.allowed:
                raise TimeLetterAccessError(403, "active timeLetter grant is required")
            return {
                "status": "available",
                "access": {
                    "role": "recipient",
                    "viewerUserId": viewer_user_id,
                    "ownerUserId": owner_user_id,
                    "familyMemberId": recipient_id,
                },
                "item": _safe_detail_item(item),
            }

    raise TimeLetterAccessError(403, "viewer is not a recipient")
