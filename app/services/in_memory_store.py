from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.user_identity import stable_user_id


class InMemoryStore:
    def __init__(self):
        self._users: Dict[str, Dict[str, Any]] = {}
        self._kb_snapshots: Dict[str, Dict[str, Any]] = {}
        self._memories: Dict[str, List[Dict[str, Any]]] = {}
        self._archive_items: Dict[str, List[Dict[str, Any]]] = {}
        self._mailbox_letters: Dict[str, List[Dict[str, Any]]] = {}
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._password_credentials: Dict[str, Dict[str, Any]] = {}
        self._family_members: Dict[str, List[Dict[str, Any]]] = {}
        self._care_snapshots: Dict[str, List[Dict[str, Any]]] = {}
        self._echo_delayed_replies: Dict[str, List[Dict[str, Any]]] = {}
        self._push_device_tokens: Dict[str, List[Dict[str, Any]]] = {}

    def upsert_user(self, phone: str, nickname: str) -> Dict[str, Any]:
        user_id = stable_user_id(phone)
        user = {
            "id": user_id,
            "phone": phone,
            "nickname": nickname or "寻梦环游用户",
            "updatedAt": self._now(),
        }
        self._users[user_id] = user
        return deepcopy(user)

    def save_profile(self, user_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(profile)
        item["userId"] = user_id
        item["updatedAt"] = self._now()
        self._profiles[user_id] = item
        return deepcopy(item)

    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        profile = self._profiles.get(user_id)
        return None if profile is None else deepcopy(profile)

    def save_password_credential(self, user_id: str, credential: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(credential)
        item["userId"] = user_id
        item["updatedAt"] = self._now()
        self._password_credentials[user_id] = item
        return deepcopy(item)

    def get_password_credential(self, user_id: str) -> Optional[Dict[str, Any]]:
        credential = self._password_credentials.get(user_id)
        return None if credential is None else deepcopy(credential)

    def save_kb_snapshot(self, user_id: str, graph: Dict[str, Any]) -> Dict[str, Any]:
        snapshot = {
            "userId": user_id,
            "graph": deepcopy(graph),
            "updatedAt": self._now(),
        }
        self._kb_snapshots[user_id] = snapshot
        return deepcopy(snapshot)

    def get_kb_snapshot(self, user_id: str) -> Optional[Dict[str, Any]]:
        snapshot = self._kb_snapshots.get(user_id)
        if snapshot is None:
            return None
        return deepcopy(snapshot["graph"])

    def add_memory(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", f"memory_{len(self._memories.get(user_id, [])) + 1}")
        item["userId"] = user_id
        item["createdAt"] = self._now()
        self._memories.setdefault(user_id, []).insert(0, item)
        return deepcopy(item)

    def list_memories(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._memories.get(user_id, []))

    def add_archive_item(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", f"archive_{len(self._archive_items.get(user_id, [])) + 1}")
        item["userId"] = user_id
        item["createdAt"] = self._now()
        self._archive_items.setdefault(user_id, []).insert(0, item)
        return deepcopy(item)

    def list_archive_items(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._archive_items.get(user_id, []))

    def add_mailbox_letter(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", f"mailbox_{len(self._mailbox_letters.get(user_id, [])) + 1}")
        item["userId"] = user_id
        item["updatedAt"] = self._now()
        item.setdefault("createdAt", item["updatedAt"])

        letters = self._mailbox_letters.setdefault(user_id, [])
        letters[:] = [letter for letter in letters if letter.get("id") != item["id"]]
        letters.insert(0, item)
        return deepcopy(item)

    def list_mailbox_letters(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._mailbox_letters.get(user_id, []))

    def add_echo_delayed_reply(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", item.get("delayedReplyId") or f"echo_delayed_{len(self._echo_delayed_replies.get(user_id, [])) + 1}")
        item["userId"] = user_id
        item["createdAt"] = self._now()

        replies = self._echo_delayed_replies.setdefault(user_id, [])
        replies[:] = [reply for reply in replies if reply.get("id") != item["id"]]
        replies.insert(0, item)
        return deepcopy(item)

    def list_echo_delayed_replies(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._echo_delayed_replies.get(user_id, []))

    def save_push_device_token(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", item.get("deviceTokenId") or f"push_token_{len(self._push_device_tokens.get(user_id, [])) + 1}")
        item.setdefault("deviceTokenId", item["id"])
        item["userId"] = user_id
        item["updatedAt"] = self._now()

        tokens = self._push_device_tokens.setdefault(user_id, [])
        tokens[:] = [token for token in tokens if token.get("id") != item["id"]]
        tokens.insert(0, item)
        return deepcopy(item)

    def list_push_device_tokens(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._push_device_tokens.get(user_id, []))

    def add_family_member(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = deepcopy(payload)
        item.setdefault("id", f"family_{len(self._family_members.get(user_id, [])) + 1}")
        item.setdefault("invitationCode", "")
        item.setdefault("invitationURL", "")
        item["userId"] = user_id
        item["ownerUserId"] = user_id
        item["createdAt"] = self._now()
        self._family_members.setdefault(user_id, []).append(item)
        return deepcopy(item)

    def list_family_members(self, user_id: str) -> List[Dict[str, Any]]:
        return deepcopy(self._family_members.get(user_id, []))

    def accept_family_member(self, user_id: str, member_id: str, phone: str) -> Optional[Dict[str, Any]]:
        members = self._family_members.get(user_id, [])
        normalized_phone = self._normalized_phone(phone)
        for index, item in enumerate(members):
            if item.get("id") != member_id:
                continue
            expected_phone = self._normalized_phone(str(item.get("phone") or ""))
            if expected_phone and normalized_phone != expected_phone:
                return None
            if item.get("accessStatus") == "revoked" or item.get("invitationStatus") == "revoked":
                return None
            if item.get("accessStatus") == "active" and item.get("invitationStatus") == "accepted":
                accepted = deepcopy(item)
                accepted["ownerUserId"] = user_id
                return accepted
            accepted = deepcopy(item)
            accepted["accessStatus"] = "active"
            accepted["invitationStatus"] = "accepted"
            accepted["isOnline"] = True
            accepted["acceptedAt"] = self._now()
            accepted["lastUpdated"] = "刚刚接受邀请"
            accepted["ownerUserId"] = user_id
            members[index] = accepted
            return deepcopy(accepted)
        return None

    def accept_family_invitation_code(self, invitation_code: str, phone: str) -> Optional[Dict[str, Any]]:
        normalized_code = invitation_code.strip()
        if not normalized_code:
            return None
        normalized_phone = self._normalized_phone(phone)
        for user_id, members in self._family_members.items():
            for index, item in enumerate(members):
                if str(item.get("invitationCode") or "").strip() != normalized_code:
                    continue
                expected_phone = self._normalized_phone(str(item.get("phone") or ""))
                if expected_phone and normalized_phone != expected_phone:
                    return None
                if item.get("accessStatus") == "revoked" or item.get("invitationStatus") == "revoked":
                    return None
                if item.get("accessStatus") == "active" and item.get("invitationStatus") == "accepted":
                    accepted = deepcopy(item)
                    accepted["ownerUserId"] = user_id
                    return accepted
                accepted = deepcopy(item)
                accepted["accessStatus"] = "active"
                accepted["invitationStatus"] = "accepted"
                accepted["isOnline"] = True
                accepted["acceptedAt"] = self._now()
                accepted["lastUpdated"] = "刚刚接受邀请"
                accepted["ownerUserId"] = user_id
                members[index] = accepted
                return deepcopy(accepted)
        return None

    def revoke_family_member(self, user_id: str, member_id: str) -> Optional[Dict[str, Any]]:
        members = self._family_members.get(user_id, [])
        for index, item in enumerate(members):
            if item.get("id") != member_id:
                continue
            revoked = deepcopy(item)
            revoked["accessStatus"] = "revoked"
            revoked["invitationStatus"] = "revoked"
            revoked["isOnline"] = False
            revoked["revokedAt"] = self._now()
            revoked["lastUpdated"] = "访问已撤回"
            members[index] = revoked
            return deepcopy(revoked)
        return None

    def save_care_snapshot(
        self,
        user_id: str,
        snapshot: Dict[str, Any],
        viewer_family_member_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        item = {
            "id": f"care_{len(self._care_snapshots.get(user_id, [])) + 1}",
            "userId": user_id,
            "viewerFamilyMemberID": viewer_family_member_id,
            "snapshot": deepcopy(snapshot),
            "createdAt": self._now(),
        }
        self._care_snapshots.setdefault(user_id, []).insert(0, item)
        return deepcopy(item)

    def get_latest_care_snapshot(
        self,
        user_id: str,
        viewer_family_member_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        snapshots = self._care_snapshots.get(user_id, [])
        for item in snapshots:
            if item.get("viewerFamilyMemberID") == viewer_family_member_id:
                return deepcopy(item)
        return None

    def list_care_snapshots(
        self,
        user_id: str,
        viewer_family_member_id: Optional[str] = None,
        limit: int = 7,
    ) -> List[Dict[str, Any]]:
        snapshots = self._care_snapshots.get(user_id, [])
        filtered = [
            item for item in snapshots
            if item.get("viewerFamilyMemberID") == viewer_family_member_id
        ]
        return deepcopy(filtered[:max(1, min(limit, 30))])

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalized_phone(phone: str) -> str:
        return "".join(ch for ch in phone if ch.isdigit())
