import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import httpx
from app.services.in_memory_store import InMemoryStore
from app.services.identity_bindings import IdentityBindingService, SyntheticIdentityChallengeAdapter, IdentityChallengeVerificationFailed
from app.services.phone_registration import PhoneRegistrationService, RegistrationError, phone_target
from app.services.tencent_sms import TencentSMSAdapter
from app.services.passwords import verify_password


def op(): return uuid.uuid4().hex


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.store=InMemoryStore()
        self.identity=IdentityBindingService(self.store,hmac_key='identity-local-test-'+'x'*40,hmac_key_version='v1',
            adapter=SyntheticIdentityChallengeAdapter('246810'),challenge_ttl_seconds=300,max_attempts=5)
        self.service=PhoneRegistrationService(self.store,self.identity,secret='registration-local-test-'+'r'*40)
        self.service.configure(cap=10,paused=False,sms_budget=50,revision=0,operation=op(),actor='test')

    def issue(self): return self.service.issue(allocated_to='',source='local',operation=op(),actor='test')
    def attempt(self,invite=None,phone='13800000001'):
        return self.service.start(invitation_code=(invite or self.issue())['invitationCode'],phone=phone,consent=True,operation=op(),ip=op())
    def verified(self,invite=None,phone='13800000001'):
        a=self.attempt(invite,phone)
        g=self.service.verify(attempt_id=a['attemptId'],token=a['attemptToken'],code='246810')['grant']
        return a,g
    def finish(self,a,g,**kw):
        return self.service.complete(attempt_id=a['attemptId'],token=a['attemptToken'],grant=g,password=kw.pop('password','SyntheticPassword123'),consent=True,operation=kw.pop('operation',op()),**kw)
    def error(self,code,call):
        with self.assertRaises(RegistrationError) as c: call()
        self.assertEqual(c.exception.code,code)

    def test_phone_namespace_and_real_ownership(self):
        for p in ('13800000001','+86 138-0000-0001','008613800000001'):
            self.assertEqual(phone_target(p),'+8613800000001')
        for p in ('10000000001','family_alice','１３８０００００００１'):
            self.error('registrationPhoneInvalid',lambda:phone_target(p))
        a=self.attempt(); self.assertFalse(self.store._subjects)
        g=self.service.verify(attempt_id=a['attemptId'],token=a['attemptToken'],code='246810')['grant']
        self.assertFalse(self.store._subjects)
        result=self.finish(a,g); self.assertNotIn('auth',result)
        self.assertEqual(self.service.snapshot()['registered'],1)
        self.assertEqual(self.service.snapshot()['reserved'],0)
        self.assertEqual(len(self.store._identity_proofs),1)

    def test_cap_and_last_seat_race(self):
        self.service.configure(cap=1,paused=False,sms_budget=50,revision=1,operation=op(),actor='test')
        def issue(_):
            try: return self.issue()['id']
            except RegistrationError: return None
        with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(issue,range(8)))
        self.assertEqual(sum(x is not None for x in results),1)
        self.assertEqual(self.service.snapshot()['reserved'],1)

    def test_cap_unset_closed_and_pause_preserves_existing(self):
        a,g=self.verified(); result=self.finish(a,g); subject=result['user']['id']
        credential=self.store.get_password_credential(subject)
        self.service.configure(cap=10,paused=True,sms_budget=50,revision=1,operation=op(),actor='test')
        self.error('registrationPaused',self.issue)
        self.assertTrue(verify_password('SyntheticPassword123',credential))
        self.assertEqual(self.identity.subject_for_target(identity_type='phone',target='13800000001'),subject)
        self.assertEqual(self.service.snapshot()['registered'],1)

    def test_configuration_lowering_conflict_and_replay(self):
        self.issue(); key=op()
        self.error('registrationCapBelowOccupancy',lambda:self.service.configure(cap=0,paused=False,sms_budget=50,revision=1,operation=key,actor='test'))
        result=self.service.configure(cap=3,paused=False,sms_budget=50,revision=1,operation=key,actor='test')
        self.assertEqual(result,self.service.configure(cap=3,paused=False,sms_budget=50,revision=1,operation=key,actor='test'))
        self.error('registrationIdempotencyConflict',lambda:self.service.configure(cap=4,paused=False,sms_budget=50,revision=1,operation=key,actor='test'))

    def test_revoke_and_expire_once(self):
        invite=self.issue(); key=op()
        for _ in range(2): self.service.revoke(invitation_id=invite['id'],operation=key,actor='test')
        self.assertEqual(self.service.snapshot()['reserved'],0)
        invite=self.issue()
        with self.service.repo.transaction() as (s,now): s['codes'][invite['id']]['expiresAt']=now-1
        self.assertEqual(self.service.snapshot()['reserved'],0)
        self.assertEqual(self.service.snapshot()['reserved'],0)

    def test_consumed_code_cannot_delete_registered_account(self):
        invite=self.issue(); a,g=self.verified(invite); r=self.finish(a,g)
        self.error('registrationAlreadyRedeemed',lambda:self.service.revoke(invitation_id=invite['id'],operation=op(),actor='test'))
        self.assertIn(r['user']['id'],self.store._subjects)

    def test_code_wrong_five_times_durable(self):
        a=self.attempt()
        for n in range(5):
            self.error('registrationCodeInvalid',lambda:self.service.verify(attempt_id=a['attemptId'],token=a['attemptToken'],code='000000'))
            self.assertEqual(self.store._registration_state['attempts'][a['attemptId']]['errors'],n+1)
        self.error('registrationCodeUnavailable',lambda:self.service.verify(attempt_id=a['attemptId'],token=a['attemptToken'],code='246810'))

    def test_send_unknown_no_automatic_retry_and_no_plaintext(self):
        invite=self.issue(); key=op()
        with patch.object(self.identity.adapter,'deliver',side_effect=TimeoutError) as deliver:
            a=self.service.start(invitation_code=invite['invitationCode'],phone='13800000001',consent=True,operation=key,ip='test')
            again=self.service.start(invitation_code=invite['invitationCode'],phone='13800000001',consent=True,operation=key,ip='test')
            self.assertEqual(a,again); self.assertEqual(deliver.call_count,1)
        self.assertEqual(a['status'],'unknown')
        self.assertNotIn('246810',str(self.store._registration_state))
        self.assertNotIn('13800000001',str(self.store._registration_state))
        self.assertNotIn(invite['invitationCode'],str(self.store._registration_state))

    def test_budget_exhaustion_does_not_disable_valid_code(self):
        self.service.configure(cap=10,paused=False,sms_budget=1,revision=1,operation=op(),actor='test')
        a=self.attempt()
        self.error('registrationSmsBudgetExhausted',lambda:self.attempt(phone='13800000002'))
        grant=self.service.verify(attempt_id=a['attemptId'],token=a['attemptToken'],code='246810')['grant']
        self.assertEqual(self.finish(a,grant)['status'],'registered')

    def test_same_successful_operation_replay_and_conflict(self):
        a,g=self.verified(); key=op(); result=self.finish(a,g,operation=key)
        self.assertEqual(self.finish(a,g,operation=key),result)
        self.error('registrationIdempotencyConflict',lambda:self.finish(a,g,operation=key,password='ChangedPassword123'))
        self.assertEqual(self.service.result(attempt_id=a['attemptId'],token=a['attemptToken']),result)
        self.assertEqual(self.service.snapshot()['registered'],1)

    def test_wrong_token_and_grant_cannot_complete(self):
        a,g=self.verified()
        self.error('registrationAttemptUnavailable',lambda:self.service.result(attempt_id=a['attemptId'],token='wrong'))
        self.error('registrationGrantUnavailable',lambda:self.finish(a,'wrong'))
        self.assertEqual(self.service.snapshot()['registered'],0)

    def test_failure_rolls_back_entire_account_and_reservation(self):
        a,g=self.verified()
        with patch.object(self.store,'save_profile',side_effect=RuntimeError('injected')):
            with self.assertRaises(RuntimeError): self.finish(a,g)
        self.assertFalse(self.store._subjects); self.assertFalse(self.store._identity_bindings)
        self.assertFalse(self.store._password_credentials); self.assertFalse(self.store._identity_proofs)
        self.assertEqual(self.service.snapshot()['reserved'],1)
        self.assertEqual(self.finish(a,g)['status'],'registered')

    def test_one_invite_two_phones_only_one(self):
        invite=self.issue(); a,g=self.verified(invite)
        with self.service.repo.transaction() as (s,now): s['attempts'][a['attemptId']]['createdAt']-=61
        b,h=self.verified(invite,phone='13800000002')
        def finish(pair):
            try:return self.finish(*pair)['status']
            except RegistrationError:return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(finish,[(a,g),(b,h)]))
        self.assertEqual(sorted(results),['blocked','registered'])

    def test_two_invites_same_phone_preserves_existing_password(self):
        a,g=self.verified(); result=self.finish(a,g)
        with self.service.repo.transaction() as (s,now): s['attempts'][a['attemptId']]['createdAt']-=61
        b,h=self.verified(); self.assertEqual(self.finish(b,h,password='OtherPassword123')['status'],'existingAccount')
        self.assertEqual(self.service.snapshot()['registered'],1)
        self.assertEqual(self.service.snapshot()['reserved'],1)
        self.assertTrue(verify_password('SyntheticPassword123',self.store.get_password_credential(result['user']['id'])))

    def test_legacy_username_same_admission_and_case_rules(self):
        invite=self.issue()
        result=self.service.register_username(code=invite['invitationCode'],username='Family_Alice',password='SyntheticPassword123')
        self.assertEqual(self.identity.subject_for_target(identity_type='username',target='family_alice'),result['user']['id'])
        self.assertEqual(self.service.snapshot()['registered'],1)
        with self.assertRaises(ValueError):self.service.register_username(code=self.issue()['invitationCode'],username='13800000001',password='SyntheticPassword123')

    def test_unknown_otp_does_not_create_subject_but_existing_login_still_works(self):
        a,g=self.verified(); subject=self.finish(a,g)['user']['id']
        self.identity.allow_implicit_registration=False
        old=self.identity.create_challenge(identity_type='phone',target='13800000001',purpose='login')
        self.assertEqual(self.identity.verify_challenge(old['challenge']['challengeId'],'246810')['user']['id'],subject)
        new=self.identity.create_challenge(identity_type='phone',target='13800000002',purpose='login')
        with self.assertRaises(IdentityChallengeVerificationFailed):self.identity.verify_challenge(new['challenge']['challengeId'],'246810')
        self.assertEqual(len(self.store._subjects),1)


class TencentTests(unittest.TestCase):
    def adapter(self,handler):
        return TencentSMSAdapter(secret_id='test-id',secret_key='test-secret',region='ap-guangzhou',app_id='test-app',sign_name='测试签名',template_id='test-template',transport=httpx.MockTransport(handler))
    def send(self,adapter):return adapter.deliver(identity_type='phone',target='+8613800000001',purpose='register',challenge_id='opaque',verification_code='246810')
    def test_success_is_accepted_not_delivered(self):
        def handler(r):
            import json
            body=json.loads(r.content); self.assertEqual(body['PhoneNumberSet'],['+8613800000001']); self.assertEqual(body['TemplateParamSet'],['246810'])
            self.assertIn('TC3-HMAC-SHA256',r.headers['Authorization']); self.assertEqual(r.headers['X-TC-Version'],'2021-01-11')
            return httpx.Response(200,json={'Response':{'SendStatusSet':[{'PhoneNumber':'+8613800000001','Code':'Ok','SerialNo':'test'}]}})
        self.assertEqual(self.send(self.adapter(handler)).delivery_state,'accepted')
    def test_http200_business_failure(self):
        a=self.adapter(lambda r:httpx.Response(200,json={'Response':{'SendStatusSet':[{'PhoneNumber':'+8613800000001','Code':'FailedOperation.InsufficientBalanceInSmsPackage'}]}}))
        self.assertEqual(self.send(a).delivery_state,'undeliverable')
    def test_wrong_phone_and_timeout_are_unknown(self):
        a=self.adapter(lambda r:httpx.Response(200,json={'Response':{'SendStatusSet':[{'PhoneNumber':'+8613800000002','Code':'Ok'}]}}))
        self.assertEqual(self.send(a).delivery_state,'unknown')
        def timeout(r):raise httpx.ReadTimeout('test')
        self.assertEqual(self.send(self.adapter(timeout)).delivery_state,'unknown')

class RegistrationAPITests(unittest.TestCase):
    def setUp(self):
        from dataclasses import replace
        import app.main as main
        from fastapi.testclient import TestClient
        from app.services.test_account_admin_auth import encode_test_account_admin_password
        self.main=main; self.store=InMemoryStore()
        self.identity=IdentityBindingService(self.store,hmac_key='api-identity-test-'+'x'*40,hmac_key_version='v1',adapter=SyntheticIdentityChallengeAdapter('246810'),challenge_ttl_seconds=300,max_attempts=5,allow_implicit_registration=False)
        settings=replace(main.settings,registration_admission_enabled=True,registration_secret='api-registration-'+'r'*40,
            registration_local_prototype_enabled=True,registration_admin_origin='https://testserver',registration_admin_password_hash=encode_test_account_admin_password('LocalAdminPassword1234'),environment='test')
        for p in [patch.object(main,'store',self.store),patch.object(main,'settings',settings),patch.object(main,'_identity_binding_service',return_value=self.identity)]:
            p.start();self.addCleanup(p.stop)
        self.client=TestClient(main.app,base_url='https://testserver')
        self.addCleanup(self.client.close)

    def admin(self):
        r=self.client.post('/ops/registration/login',headers={'Origin':'https://testserver'},json={'password':'LocalAdminPassword1234'})
        self.assertEqual(r.status_code,200,r.text)
        return {'Origin':'https://testserver','X-CSRF-Token':r.json()['csrf']}

    def test_admin_denies_anonymous_cookie_confusion_and_csrf(self):
        self.assertEqual(self.client.get('/ops/registration/state').status_code,401)
        self.client.cookies.set('dj_test_account_admin','not-registration-admin')
        self.assertEqual(self.client.get('/ops/registration/state').status_code,401)
        r=self.client.post('/ops/registration/login',json={'password':'LocalAdminPassword1234'})
        self.assertEqual(r.status_code,403)
        headers=self.admin()
        self.assertEqual(self.client.post('/ops/registration/policy',headers={'Origin':'https://testserver'},json={}).status_code,403)
        self.assertEqual(self.client.get('/ops/registration/state').status_code,200)
        r=self.client.post('/ops/registration/logout',headers=headers)
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.get('/ops/registration/state').status_code,401)

    def test_registration_http_no_session_and_admin_config(self):
        headers=self.admin()
        r=self.client.post('/ops/registration/policy',headers=headers,json={'cap':5,'paused':False,'smsBudget':10,'revision':0,'operationId':op()})
        self.assertEqual(r.status_code,200,r.text)
        c=self.client.post('/ops/registration/invitations',headers=headers,json={'operationId':op(),'source':'api-test'}).json()
        body={'invitationCode':c['invitationCode'],'phone':'13800000001','legalConsent':True,'operationId':op()}
        a=self.client.post('/v2/auth/phone-registration/challenges',json=body)
        self.assertEqual(a.status_code,200,a.text)
        self.assertIn('no-store',a.headers.get('Cache-Control',''))
        a=a.json();base={'attemptId':a['attemptId'],'attemptToken':a['attemptToken']}
        self.assertFalse(self.store._subjects)
        v=self.client.post('/v2/auth/phone-registration/verify',json={**base,'code':'246810'})
        self.assertEqual(v.status_code,200,v.text)
        r=self.client.post('/v2/auth/phone-registration/complete',json={**base,'grant':v.json()['grant'],'legalConsent':True,'password':'SyntheticPassword123','operationId':op()})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['status'],'registered');self.assertNotIn('auth',r.json())
        state=self.client.get('/ops/registration/state').json();self.assertEqual(state['registered'],1)
        self.assertNotIn('password',str(state));self.assertNotIn('13800000001',str(state))
        self.assertEqual(self.client.post('/v2/auth/phone-registration/result',json=base).json(),r.json())
        # A valid ordinary account token is not an operator credential.
        from app.services.auth_sessions import AuthSessionService
        auth=AuthSessionService(self.store,access_ttl_seconds=300,refresh_ttl_seconds=3600).issue(r.json()['user']['id'])
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/ops/registration/state',headers={'Authorization':'Bearer '+auth['accessToken']}).status_code,401)

    def test_old_username_validation_is_a_client_error(self):
        from dataclasses import replace
        with patch.object(self.main,'settings',replace(self.main.settings,username_registration_enabled=True,password_authentication_enabled=True)):
            r=self.client.post('/v2/auth/username/register',json={'legalConsent':True,'username':'13800000001','password':'SyntheticPassword123','invitationCode':'A'*32})
        self.assertEqual(r.status_code,400,r.text)

    def test_durable_admin_login_throttle(self):
        for _ in range(5):
            self.assertEqual(self.client.post('/ops/registration/login',headers={'Origin':'https://testserver'},json={'password':'wrong'}).status_code,401)
        self.assertEqual(self.client.post('/ops/registration/login',headers={'Origin':'https://testserver'},json={'password':'LocalAdminPassword1234'}).status_code,429)

class BudgetTests(unittest.TestCase):
    def test_tencent_old_and_new_paths_share_budget_without_double_dispatch(self):
        from app.services.tencent_sms import BudgetedTencentSMSAdapter
        store=InMemoryStore(); adapter=SyntheticIdentityChallengeAdapter('246810')
        budgeted=BudgetedTencentSMSAdapter(adapter,store)
        with budgeted.repo.transaction() as (s,now):s['policy']['smsBudget']=2
        args=dict(identity_type='phone',target='+8613800000001',purpose='login',challenge_id='first',verification_code='246810')
        with patch.object(adapter,'deliver',wraps=adapter.deliver) as delivery:
            budgeted.deliver(**args);budgeted.deliver(**args)
            self.assertEqual(delivery.call_count,1)
            with budgeted.repo.transaction() as (s,now):
                s['policy']['smsUsed']+=1
                s['events']['sms-reserve:registration']={'type':'smsReserved','at':now}
            budgeted.deliver(**{**args,'challenge_id':'registration'})
            self.assertEqual(delivery.call_count,2)
            self.assertEqual(budgeted.deliver(**{**args,'challenge_id':'third'}).delivery_state,'undeliverable')
            self.assertEqual(delivery.call_count,2)
        self.assertEqual(store._registration_state['policy']['smsUsed'],2)

class AdmissionBoundaryTests(unittest.TestCase):
    setUp = RegistrationTests.setUp
    issue = RegistrationTests.issue
    attempt = RegistrationTests.attempt
    verified = RegistrationTests.verified
    finish = RegistrationTests.finish
    error = RegistrationTests.error

    def test_missing_cap_is_closed(self):
        with self.service.repo.transaction() as (s,now):s['policy']['cap']=None
        self.error('registrationPaused',self.issue)

    def test_claim_never_mints_new_code_and_sharing_does_not_charge_seats(self):
        a,g=self.verified(); owner=self.finish(a,g)['user']['id']
        self.error('registrationAllocationRequired',lambda:self.service.claim(owner))
        invite=self.service.issue(allocated_to=owner,source='family',operation=op(),actor='test')
        first=self.service.claim(owner);second=self.service.claim(owner)
        self.assertEqual(first['invitationCode'],invite['invitationCode']);self.assertEqual(first['id'],second['id'])
        self.assertEqual(self.service.snapshot()['reserved'],1)
        self.assertFalse(self.store._family_relationships)
        self.store._family_members[owner][0]['invitationStatus']='revoked'
        self.assertEqual(self.service.snapshot()['reserved'],0)
        self.assertEqual(self.service.snapshot()['registered'],1)

    def test_expired_grant_and_paused_completion_preserve_reservation(self):
        a,g=self.verified()
        with self.service.repo.transaction() as (s,now):s['attempts'][a['attemptId']]['grantExpiresAt']=now-1
        self.error('registrationGrantUnavailable',lambda:self.finish(a,g))
        self.assertEqual(self.service.snapshot()['reserved'],1)
        with self.service.repo.transaction() as (s,now):
            s['attempts'][a['attemptId']]['grantExpiresAt']=now+100;s['policy']['paused']=True
        self.error('registrationPaused',lambda:self.finish(a,g))
        self.assertEqual(self.service.snapshot()['registered'],0)

    def test_ordinary_identity_and_session_writers_cannot_bypass(self):
        self.assertTrue(self.store._registration_admission_active)
        with self.assertRaises(ValueError):self.store.upsert_user(phone='13800000001',nickname='bypass')
        with self.assertRaises(ValueError):self.store.save_auth_session({'userId':'unknown','sessionId':'bypass'})
        self.assertFalse(self.store._subjects)

    def test_resend_invalidates_old_grant_and_cannot_rebind_operation(self):
        a,g=self.verified(); invite=self.issue()
        with self.service.repo.transaction() as (s,now):s['attempts'][a['attemptId']]['createdAt']-=61
        b=self.attempt(invite)
        self.error('registrationGrantUnavailable',lambda:self.finish(a,g))
        key=op()
        with self.service.repo.transaction() as (s,now):s['attempts'][b['attemptId']]['createdAt']-=61
        c=self.service.start(invitation_code=invite['invitationCode'],phone='13800000001',consent=True,operation=key,ip='test')
        self.error('registrationIdempotencyConflict',lambda:self.service.start(invitation_code=invite['invitationCode'],phone='13800000002',consent=True,operation=key,ip='test'))

    def test_existing_subject_cannot_be_overwritten_even_if_suspended(self):
        a,g=self.verified(); subject=self.finish(a,g)['user']['id']
        self.store._subjects[subject]['status']='suspended'
        with self.service.repo.transaction() as (s,now):s['attempts'][a['attemptId']]['createdAt']-=61
        b,h=self.verified();self.assertEqual(self.finish(b,h)['status'],'existingAccount')
        self.assertEqual(self.store._subjects[subject]['status'],'suspended')

class LegacyClassificationTests(RegistrationTests):
    def seed(self):
        with self.service.repo.transaction() as (s,n):
            s['seats']={str(i):{'status':'occupied','source':'migration0131'} for i in range(70)}
            s['policy'].update(cap=None,paused=True,revision=0)
        return {str(i):('real' if i<2 else 'test' if i<61 else 'legacyReview') for i in range(70)}
    def test_70_compatible_subjects_count_two_real_without_deletion(self):
        m=self.seed();key=op()
        r=self.service.classify_legacy(classifications=m,revision=0,operation=key,actor='operator',reason='owner confirmed')
        self.assertEqual((r['registered'],r['testSubjects'],r['legacyReviewSubjects'],r['compatibleSubjects']),(2,59,9,70))
        self.assertEqual(r,self.service.classify_legacy(classifications=m,revision=0,operation=key,actor='operator',reason='owner confirmed'))
        self.service.configure(cap=50,paused=True,sms_budget=0,revision=1,operation=op(),actor='operator')
        invite=self.service.issue(allocated_to='',source='audit',operation=op(),actor='operator',allow_while_paused=True)
        self.assertEqual(self.service.snapshot()['available'],47)
        self.service.revoke(invitation_id=invite['id'],operation=op(),actor='operator')
        self.assertEqual(self.service.snapshot()['available'],48)
        self.assertEqual(len(self.store._registration_state['seats']),70)
    def test_manifest_cannot_drop_or_reclassify_new_account(self):
        m=self.seed();m.pop('0')
        self.error('registrationClassificationManifestMismatch',lambda:self.service.classify_legacy(classifications=m,revision=0,operation=op(),actor='operator',reason='x'))
        self.assertEqual(self.service.snapshot()['registered'],70)
        m=self.seed()
        with self.service.repo.transaction() as (s,n):s['seats']['0']['source']='newRegistration'
        self.error('registrationClassificationManifestMismatch',lambda:self.service.classify_legacy(classifications=m,revision=0,operation=op(),actor='operator',reason='x'))
    def test_new_account_defaults_real_and_last_seat_still_enforced(self):
        m=self.seed();self.service.classify_legacy(classifications=m,revision=0,operation=op(),actor='operator',reason='x')
        self.service.configure(cap=3,paused=False,sms_budget=10,revision=1,operation=op(),actor='operator')
        a,g=self.verified();self.finish(a,g)
        r=self.service.snapshot();self.assertEqual((r['registered'],r['compatibleSubjects']),(3,71))
        self.error('registrationCapacityReached',self.issue)
