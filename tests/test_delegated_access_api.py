import unittest

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


client = TestClient(app)


class DelegatedAccessAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_legacy_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        self.previous_delegated_access_api_enabled = (
            main_module.DELEGATED_ACCESS_CONTRACT_API_ENABLED
        )
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = "delegated-access-machine-token"
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.DELEGATED_ACCESS_CONTRACT_API_ENABLED = True
        release_policy = ReleasePolicyService(
            shadow_mode=True,
            enforce_default_closed_stages=False,
        )
        main_module.RELEASE_POLICY_SERVICE = release_policy
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(release_policy)

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_login
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_gate
        main_module.DELEGATED_ACCESS_CONTRACT_API_ENABLED = (
            self.previous_delegated_access_api_enabled
        )

    def login(self, phone: str, nickname: str):
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": nickname, "password": "password123"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    @staticmethod
    def headers(login_body):
        return {"Authorization": f"Bearer {login_body['auth']['accessToken']}"}

    @staticmethod
    def care_snapshot(summary: str):
        return {
            "generatedAt": "2026-07-18T08:00:00Z",
            "windowStart": "2026-07-11T00:00:00Z",
            "windowEnd": "2026-07-18T08:00:00Z",
            "windowDayCount": 7,
            "dataCoverageSummary": "授权测试数据",
            "totalTurns": 3,
            "userTurnCount": 2,
            "characterCount": 24,
            "uniqueTokenCount": 12,
            "lexicalDiversity": 0.5,
            "negativeEmotionMentions": 0,
            "sleepMentions": 0,
            "bodyDiscomfortMentions": 0,
            "repetitionRatio": 0.0,
            "averageWordsPerMinute": 80.0,
            "slowSpeechTurnCount": 0,
            "longPauseTurnCount": 0,
            "emotionVolatilityScore": 0.1,
            "riskLevel": "stable",
            "summary": summary,
            "suggestions": [],
            "weeklyHighlights": [],
            "riskSignalDescriptions": [],
            "dailyTrend": [],
            "trendSummary": "稳定",
        }

    def accepted_relationship(self):
        owner = self.login("13800139001", "owner")
        member = self.login("13800139002", "member")
        owner_id = owner["user"]["id"]
        member_id = member["user"]["id"]
        invited = client.post(
            "/family/invite",
            headers=self.headers(owner),
            json={
                "userId": owner_id,
                "name": "家庭成员",
                "relation": "亲属",
                "phone": "13800139002",
            },
        )
        self.assertEqual(invited.status_code, 200)
        accepted = client.post(
            f"/family/invitations/{invited.json()['member']['invitationCode']}/accept",
            headers=self.headers(member),
            json={"phone": "13800139002"},
        )
        self.assertEqual(accepted.status_code, 200)
        relationship = accepted.json()["member"]
        self.assertEqual(relationship["relationshipStatus"], "accepted")
        self.assertEqual(relationship["memberSubjectId"], member_id)
        self.assertEqual(relationship["accessGrants"], [])
        return owner, member, owner_id, member_id, relationship

    def grant_care(self, owner, owner_id, member_id, relationship):
        response = client.post(
            "/family/access-grants",
            headers=self.headers(owner),
            json={
                "userId": owner_id,
                "relationshipId": relationship["relationshipId"],
                "granteeSubjectId": member_id,
                "purpose": "care.snapshot",
                "resourceType": "careSnapshot",
                "operations": ["read"],
                "expiresAt": "2099-01-01T00:00:00Z",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["grant"]

    def test_relationship_without_grant_is_denied_then_grant_and_revoke_revalidate_reads(self):
        owner, member, owner_id, member_id, relationship = self.accepted_relationship()

        denied_write = client.post(
            "/care/snapshots",
            headers=self.headers(owner),
            json={
                "userId": owner_id,
                "viewerFamilyMemberID": relationship["id"],
                "snapshot": self.care_snapshot("授权前不可见"),
            },
        )
        grant = self.grant_care(owner, owner_id, member_id, relationship)
        saved = client.post(
            "/care/snapshots",
            headers=self.headers(owner),
            json={
                "userId": owner_id,
                "viewerFamilyMemberID": relationship["id"],
                "snapshot": self.care_snapshot("授权后可见"),
            },
        )
        allowed_read = client.get(
            f"/care/snapshots/latest/{owner_id}",
            headers=self.headers(member),
            params={"viewerFamilyMemberID": relationship["id"]},
        )
        revoked = client.post(
            f"/family/access-grants/{owner_id}/{grant['id']}/revoke",
            headers=self.headers(owner),
            json={"expectedVersion": grant["rowVersion"], "reason": "ownerRequested"},
        )
        denied_after_revoke = client.get(
            f"/care/snapshots/latest/{owner_id}",
            headers=self.headers(member),
            params={"viewerFamilyMemberID": relationship["id"]},
        )

        self.assertEqual(denied_write.status_code, 403)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(allowed_read.status_code, 200)
        self.assertEqual(allowed_read.json()["item"]["snapshot"]["summary"], "授权后可见")
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(denied_after_revoke.status_code, 403)

    def test_relationship_pause_and_resume_change_effective_access_without_deleting_grant(self):
        owner, member, owner_id, member_id, relationship = self.accepted_relationship()
        self.grant_care(owner, owner_id, member_id, relationship)
        saved = client.post(
            "/care/snapshots",
            headers=self.headers(owner),
            json={
                "userId": owner_id,
                "viewerFamilyMemberID": relationship["id"],
                "snapshot": self.care_snapshot("关系生命周期"),
            },
        )
        self.assertEqual(saved.status_code, 200)

        paused = client.post(
            f"/family/relationships/{owner_id}/{relationship['relationshipId']}/lifecycle",
            headers=self.headers(owner),
            json={"operation": "pause", "expectedEpoch": relationship["relationshipEpoch"]},
        )
        denied = client.get(
            f"/care/snapshots/latest/{owner_id}",
            headers=self.headers(member),
            params={"viewerFamilyMemberID": relationship["id"]},
        )
        resumed = client.post(
            f"/family/relationships/{owner_id}/{relationship['relationshipId']}/lifecycle",
            headers=self.headers(owner),
            json={
                "operation": "resume",
                "expectedEpoch": paused.json()["relationship"]["relationshipEpoch"],
            },
        )
        allowed = client.get(
            f"/care/snapshots/latest/{owner_id}",
            headers=self.headers(member),
            params={"viewerFamilyMemberID": relationship["id"]},
        )

        self.assertEqual(paused.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(allowed.status_code, 200)

    def test_wrong_purpose_grant_does_not_authorize_care(self):
        owner, member, owner_id, member_id, relationship = self.accepted_relationship()
        granted = client.post(
            "/family/access-grants",
            headers=self.headers(owner),
            json={
                "userId": owner_id,
                "relationshipId": relationship["relationshipId"],
                "granteeSubjectId": member_id,
                "purpose": "family.persona",
                "resourceType": "familyMember",
                "resourceId": relationship["id"],
                "operations": ["read"],
                "expiresAt": "2099-01-01T00:00:00Z",
            },
        )
        denied = client.get(
            f"/care/snapshots/latest/{owner_id}",
            headers=self.headers(member),
            params={"viewerFamilyMemberID": relationship["id"]},
        )

        self.assertEqual(granted.status_code, 200)
        self.assertEqual(denied.status_code, 403)

    def test_owner_account_deletion_revokes_grant_and_restore_does_not_reactivate_it(self):
        owner, member, owner_id, member_id, relationship = self.accepted_relationship()
        grant = self.grant_care(owner, owner_id, member_id, relationship)
        saved = client.post(
            "/care/snapshots",
            headers=self.headers(owner),
            json={
                "userId": owner_id,
                "viewerFamilyMemberID": relationship["id"],
                "snapshot": self.care_snapshot("删除前可见"),
            },
        )
        self.assertEqual(saved.status_code, 200)

        deleted = client.post(
            "/auth/delete",
            headers=self.headers(owner),
            json={
                "userId": owner_id,
                "phone": "13800139001",
                "firstConfirmation": True,
                "secondConfirmation": True,
            },
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["delegatedGrantRevocation"]["revokedGrantCount"], 1)
        self.assertEqual(main_module.store.get_access_grant(grant["id"])["status"], "revoked")

        denied = client.get(
            f"/care/snapshots/latest/{owner_id}",
            headers=self.headers(member),
            params={"viewerFamilyMemberID": relationship["id"]},
        )
        self.assertEqual(denied.status_code, 403)

        restored = client.post(
            "/auth/restore",
            json={"phone": "13800139001", "nickname": "owner restored"},
        )
        self.assertEqual(restored.status_code, 200)
        still_denied = client.get(
            f"/care/snapshots/latest/{owner_id}",
            headers=self.headers(member),
            params={"viewerFamilyMemberID": relationship["id"]},
        )
        self.assertEqual(still_denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
