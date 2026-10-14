import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import app.main as main
from tests.test_delegated_access_api import DelegatedAccessAPITests
from app.services.identity_bindings import IdentityBindingService, SyntheticIdentityChallengeAdapter


class FamilyOnboardingV2Tests(unittest.TestCase):
    def setUp(self):
        DelegatedAccessAPITests.setUp(self)
        main.AUTH_LEGACY_PHONE_LOGIN_ENABLED = False
        self.client = TestClient(main.app)
        self.identity = IdentityBindingService(main.store, hmac_key='local-family-test-key-' + 'x'*40,
            hmac_key_version='v1', challenge_ttl_seconds=300, max_attempts=5, adapter=SyntheticIdentityChallengeAdapter('246810'),
            auth_session_service=main._auth_session_service())
        self.binding_patch = patch.object(main, '_identity_binding_service', return_value=self.identity)
        self.binding_patch.start()
        self.addCleanup(self.binding_patch.stop)
        self.owner = self.login('13800139101')
        self.member = self.login('13800139102')
        self.stranger = self.login('13800139103')

    def tearDown(self):
        DelegatedAccessAPITests.tearDown(self)

    def login(self, phone):
        c = self.identity.create_challenge(identity_type='phone', target=phone, purpose='login')
        return self.identity.verify_challenge(c['challenge']['challengeId'], '246810')

    @staticmethod
    def headers(login):
        return {'Authorization': 'Bearer ' + login['auth']['accessToken']}

    def invite(self, **extra):
        r = self.client.post('/family/invite', headers=self.headers(self.owner), json={
            'userId': self.owner['user']['id'], 'phone':'13800139102', 'name':'家人',
            'relation':'家人', **extra})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['member']

    def accept(self, m, login=None, phone='13800139102'):
        return self.client.post('/family/invitations/' + m['invitationCode'] + '/accept',
            headers=self.headers(login or self.member), json={'phone':phone})

    def test_v2_accept_binds_real_subject_and_survives_refresh_and_repeat(self):
        m = self.invite()
        r = self.accept(m)
        self.assertEqual(r.status_code, 200, r.text)
        expected = self.member['user']['id']
        self.assertTrue(expected.startswith('sub_'))
        self.assertEqual(r.json()['member']['memberSubjectId'], expected)
        again = self.accept(m)
        self.assertEqual(again.status_code, 200, again.text)
        refreshed = self.client.get('/family/members/' + self.owner['user']['id'], headers=self.headers(self.owner))
        item = next(i for i in refreshed.json()['members'] if i['id'] == m['id'])
        self.assertEqual(item['memberSubjectId'], expected)
        self.assertEqual(item['accessGrants'], [])

    def test_invite_cannot_self_accept_or_forge_recipient(self):
        m = self.invite(accessStatus='active', invitationStatus='accepted',
            acceptedUserId=self.stranger['user']['id'], memberUserId=self.stranger['user']['id'],
            invitationCode='ATTACKER-CODE', accessGrants=[{'purpose':'family.persona'}])
        self.assertEqual(m['invitationStatus'], 'pending')
        self.assertEqual(m['relationshipStatus'], 'pending')
        self.assertEqual(m['accessGrants'], [])
        self.assertNotEqual(m['invitationCode'], 'ATTACKER-CODE')
        r = self.accept(m)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['member']['memberSubjectId'], self.member['user']['id'])

    def test_wrong_account_cannot_accept_by_knowing_phone(self):
        m = self.invite()
        self.assertEqual(self.accept(m, self.stranger).status_code, 403)
        self.assertEqual(self.accept(m, self.owner).status_code, 403)
        self.assertEqual(self.accept(m, phone='13800139103').status_code, 403)

    def test_country_code_equivalence(self):
        m = self.invite(phone='+86 138-0013-9102')
        r = self.accept(m)
        self.assertEqual(r.status_code, 200, r.text)

    def test_self_invite_is_rejected(self):
        r = self.client.post('/family/invite', headers=self.headers(self.owner), json={
            'userId':self.owner['user']['id'], 'phone':'13800139101', 'name':'自己', 'relation':'家人'})
        self.assertEqual(r.status_code, 400)

if __name__ == '__main__':
    unittest.main()
