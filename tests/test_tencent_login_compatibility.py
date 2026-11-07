"""Real provider adapter and identity paths, synthetic transport only."""
import json
import unittest
import httpx
from unittest.mock import patch
import test_phone_registration as base_tests
from test_phone_registration import op
from app.services.tencent_sms import TencentSMSAdapter, BudgetedTencentSMSAdapter
from app.services.identity_bindings import IdentityChallengeConfigurationError
from app.services.passwords import verify_password


def adapter(handler):
    return TencentSMSAdapter(secret_id='synthetic', secret_key='synthetic',
        region='ap-guangzhou', app_id='test', sign_name='测试',
        template_id='test', transport=httpx.MockTransport(handler))


class TencentLoginHTTPTests(unittest.TestCase):
    setUp = base_tests.RegistrationAPITests.setUp
    admin = base_tests.RegistrationAPITests.admin

    def test_register_then_sms_login_same_account_while_paused(self):
        sent = []
        def transport(request):
            body = json.loads(request.content)
            self.assertEqual(body['PhoneNumberSet'], ['+8613800000001'])
            sent.append(body)
            return httpx.Response(200, json={'Response': {'SendStatusSet': [{
                'PhoneNumber': '+8613800000001', 'Code': 'Ok', 'SerialNo': 'synthetic'}]}})
        self.identity.adapter = BudgetedTencentSMSAdapter(adapter(transport), self.store)
        headers = self.admin()
        self.assertEqual(self.client.post('/ops/registration/policy', headers=headers,
            json={'cap': 5, 'paused': False, 'smsBudget': 2, 'revision': 0, 'operationId': op()}).status_code, 200)
        invite = self.client.post('/ops/registration/invitations', headers=headers,
            json={'operationId': op(), 'source': 'compatibility-test'}).json()
        a = self.client.post('/v2/auth/phone-registration/challenges', json={
            'invitationCode': invite['invitationCode'], 'phone': '13800000001',
            'legalConsent': True, 'operationId': op()})
        self.assertEqual(a.status_code, 200, a.text)
        a = a.json(); base = {'attemptId': a['attemptId'], 'attemptToken': a['attemptToken']}
        v = self.client.post('/v2/auth/phone-registration/verify',
            json={**base, 'code': sent[0]['TemplateParamSet'][0]})
        self.assertEqual(v.status_code, 200, v.text)
        done = self.client.post('/v2/auth/phone-registration/complete', json={**base,
            'grant': v.json()['grant'], 'password': 'SyntheticPassword123',
            'legalConsent': True, 'operationId': op()})
        self.assertEqual(done.status_code, 200, done.text)
        subject = done.json()['user']['id']
        binding_before = self.identity.target_reference(identity_type='phone', target='13800000001')
        state = self.client.get('/ops/registration/state').json()
        self.assertEqual(self.client.post('/ops/registration/policy', headers=headers,
            json={'cap': 5, 'paused': True, 'smsBudget': 2,
                  'revision': state['revision'], 'operationId': op()}).status_code, 200)
        login = self.client.post('/v2/auth/challenges', json={
            'identityType': 'phone', 'target': '13800000001', 'purpose': 'login'})
        self.assertEqual(login.status_code, 202, login.text)
        self.assertEqual(len(sent), 2)
        cid = login.json()['challenge']['challengeId']
        self.assertEqual(self.store.get_auth_challenge(cid)['deliveryState'], 'accepted')
        otp = sent[1]['TemplateParamSet'][0]
        wrong = otp[:-1] + str((int(otp[-1]) + 1) % 10)
        self.assertEqual(self.client.post(f'/v2/auth/challenges/{cid}/verify', json={'code': wrong}).status_code, 401)
        good = self.client.post(f'/v2/auth/challenges/{cid}/verify', json={'code': otp})
        self.assertEqual(good.status_code, 200, good.text)
        self.assertEqual(good.json()['user']['id'], subject)
        self.assertEqual(self.client.post(f'/v2/auth/challenges/{cid}/verify', json={'code': otp}).status_code, 401)
        self.assertEqual(len(self.store._subjects), 1)
        self.assertTrue(verify_password('SyntheticPassword123', self.store.get_password_credential(subject)))
        for phone in ('13800000001', '+8613800000001', '8613800000001', '008613800000001'):
            self.assertEqual(self.identity.target_reference(identity_type='phone', target=phone), binding_before)
        self.assertEqual(self.store._registration_state['policy']['smsUsed'], 2)
        exhausted = self.client.post('/v2/auth/challenges', json={
            'identityType': 'phone', 'target': '13800000002', 'purpose': 'login'})
        self.assertEqual(exhausted.status_code, 202, exhausted.text)
        blocked_id = exhausted.json()['challenge']['challengeId']
        self.assertEqual(self.store.get_auth_challenge(blocked_id)['deliveryState'], 'undeliverable')
        self.assertEqual(self.client.post(f'/v2/auth/challenges/{blocked_id}/verify', json={'code': otp}).status_code, 401)
        self.assertEqual(len(sent), 2)
        self.assertEqual(len(self.store._subjects), 1)


class TencentWireTests(unittest.TestCase):
    def test_all_shared_purposes_and_formats(self):
        seen = []
        def transport(request):
            body = json.loads(request.content)
            self.assertEqual(body['PhoneNumberSet'], ['+8613800000001'])
            seen.append(body)
            return httpx.Response(200, json={'Response': {'SendStatusSet': [{
                'PhoneNumber': '+8613800000001', 'Code': 'Ok'}]}})
        a = adapter(transport)
        for purpose in ('login', 'register', 'passwordreset', 'sensitiveoperation'):
            for target in ('8613800000001', '+8613800000001'):
                with self.subTest(purpose=purpose, target=target):
                    receipt = a.deliver(identity_type='phone', target=target, purpose=purpose,
                        challenge_id=op(), verification_code='246810')
                    self.assertEqual(receipt.delivery_state, 'accepted')
        self.assertEqual(len(seen), 8)

    def test_invalid_targets_never_call_provider(self):
        def transport(request):
            self.fail('invalid target reached provider')
        for target in ('008613800000001', '86not-a-phone', '１２３４５６７', '', '0', '1'*16):
            with self.subTest(target=target), self.assertRaises(IdentityChallengeConfigurationError):
                adapter(transport).deliver(identity_type='phone', target=target,
                    purpose='login', challenge_id=op(), verification_code='246810')

    def test_provider_mismatch_and_error_are_not_success(self):
        for result, expected in [({'SendStatusSet': [{'PhoneNumber': '+8613800000002', 'Code': 'Ok'}]}, 'unknown'),
                ({'Error': {'Code': 'LimitExceeded'}}, 'undeliverable')]:
            with self.subTest(result=result):
                a = adapter(lambda r: httpx.Response(200, json={'Response': result}))
                self.assertEqual(a.deliver(identity_type='phone', target='8613800000001',
                    purpose='login', challenge_id=op(), verification_code='246810').delivery_state, expected)
