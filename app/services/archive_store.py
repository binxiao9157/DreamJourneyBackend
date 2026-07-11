from typing import Any, Dict


class ArchiveItemOwnershipConflict(Exception):
    """An archive ID is already bound to a different owner."""


class ArchiveItemNotFound(Exception):
    """The requested archive item does not exist for the owner."""


class ArchiveItemDeletionForbidden(Exception):
    """The archive item cannot be deleted in its current state."""


def is_sealed_time_letter(item: Dict[str, Any]) -> bool:
    if str(item.get("kind") or "").strip() != "timeLetter":
        return False
    metadata = item.get("metadata")
    metadata_delivery_state = ""
    if isinstance(metadata, dict):
        metadata_delivery_state = str(metadata.get("deliveryState") or "").strip()
    return str(item.get("deliveryState") or metadata_delivery_state).strip() == "sealed"
