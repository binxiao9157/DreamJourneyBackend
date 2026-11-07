import hashlib,hmac,json,time,uuid,unittest
from dataclasses import replace
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
import test_phone_registration as fixtures
from app.services.registration_admin_bridge import PATH,dispatch

KEY='isolated-admin-bridge-'+'k'*40
class BridgeTests(unittest.TestCase):
    setUp=fixtures.RegistrationTests.setUp
    def setUp(self):
        fixtures.RegistrationTests.setUp(self)
        import app.main as main
        self.main=main
        self.settings=replace(main.settings,environment='test',registration_admission_enabled=True,registration_secret='registration-test-'+'x'*40,registration_management_service_key=KEY)
        # Reuse fixture's established encryption / identity version exactly.
        self.settings=replace(self.settings,registration_secret=self.service.key.decode())
        self.context={'store':self.store,'identity':lambda:self.identity,'settings':self.settings}
    def call(self,action,data=None,actor='operator'):
        return dispatch(self.context,actor,action,data or {})
    def preview(self,kind='issue',values=None,actor='operator'):
        return self.call('preview',{'commandId':uuid.uuid4().hex,'kind':kind,'values':values or {'allocatedTo':'','source':'synthetic','revision':self.service.snapshot()['revision']}},actor)
    def execute(self,p,actor='operator'):
        return self.call('execute',{'commandId':p['commandId'],'hash':p['hash']},actor)
    def assertCode(self,code,fn):
        with self.assertRaises(Exception) as caught:fn()
        self.assertEqual(caught.exception.code,code)
    def test_preview_has_no_side_effect_and_same_id_replay(self):
        p=self.preview();self.assertEqual(self.service.snapshot()['reserved'],0)
        first=self.execute(p);self.assertEqual(first,self.execute(p));self.assertEqual(self.service.snapshot()['reserved'],1)
        self.assertEqual(self.call('result',{'commandId':p['commandId']}),first)
    def test_preview_expiry_then_applied_replay_after_expiry(self):
        p=self.preview()
        with self.service.repo.transaction() as (s,n):s['operations']['admin-preview:'+p['commandId']]['preview']['expiresAt']=n-1
        self.assertCode('registrationPreviewExpired',lambda:self.execute(p))
        p=self.preview();r=self.execute(p)
        with self.service.repo.transaction() as (s,n):s['operations']['admin-preview:'+p['commandId']]['preview']['expiresAt']=n-1
        self.assertEqual(r,self.execute(p))
    def test_other_actor_and_changed_preview_denied(self):
        p=self.preview();self.assertCode('registrationCommandUnavailable',lambda:self.execute(p,'other'))
        self.assertCode('registrationPreviewChanged',lambda:self.call('execute',{'commandId':p['commandId'],'hash':'0'*64}))
    def test_policy_change_invalidates_issue_preview(self):
        p=self.preview();s=self.service.snapshot();self.service.configure(cap=s['cap'],paused=False,sms_budget=s['smsBudget'],revision=s['revision'],operation=uuid.uuid4().hex,actor='other')
        self.assertCode('registrationPolicyConflict',lambda:self.execute(p));self.assertEqual(self.service.snapshot()['reserved'],0)
    def test_last_seat_two_previews_concurrent(self):
        s=self.service.snapshot();self.service.configure(cap=1,paused=False,sms_budget=10,revision=s['revision'],operation=uuid.uuid4().hex,actor='operator')
        ps=[self.preview(),self.preview()]
        def run(p):
            try:return self.execute(p)['status']
            except Exception as e:return e.code
        with ThreadPoolExecutor(2) as pool:r=list(pool.map(run,ps))
        self.assertEqual(sorted(r),sorted(['succeeded','registrationCapacityReached']))
    def test_revoke_preview_state_change_protection(self):
        inv=self.execute(self.preview())['result']
        p=self.preview('revoke',{'invitationId':inv['id']})
        self.service.revoke(invitation_id=inv['id'],operation=uuid.uuid4().hex,actor='other')
        self.assertCode('registrationPreviewChanged',lambda:self.execute(p))
    def test_signed_http_boundary_and_disabled_prototype(self):
        def signed(body,key=KEY):
            raw=json.dumps(body,separators=(',',':')).encode();return raw,{'X-Registration-Signature':hmac.new(key.encode(),b'POST\n'+PATH.encode()+b'\n'+raw,hashlib.sha256).hexdigest(),'Content-Type':'application/json'}
        body={'actor':'operator','scope':'registration:manage','action':'state','data':{},'timestamp':int(time.time()),'requestId':uuid.uuid4().hex}
        with patch.object(self.main,'AUTH_ROUTE_MODE','enforce'),patch.object(self.main,'store',self.store),patch.object(self.main,'settings',self.settings),patch.object(self.main,'_identity_binding_service',lambda:self.identity),TestClient(self.main.app) as c:
            for change,key in [({},'wrong'),({'scope':'memory:write'},KEY),({'timestamp':0},KEY),({'action':'deleteAccount'},KEY),({'actor':'bad\nactor'},KEY)]:
                raw,headers=signed({**body,**change},key);self.assertEqual(c.post(PATH,content=raw,headers=headers).status_code,403)
            body['actor']='运维管理员'
            raw,headers=signed(body);r=c.post(PATH,content=raw,headers=headers);self.assertEqual(r.status_code,200,r.text);self.assertIn('no-store',r.headers['cache-control'])
            self.assertEqual(c.get('/ops/registration').status_code,404)
            with patch.object(self.main,'settings',replace(self.settings,registration_management_service_key=None)):
                self.assertEqual(c.post(PATH,content=raw,headers=headers).status_code,503)
            with patch.object(self.main,'settings',replace(self.settings,environment='production',registration_local_prototype_enabled=True)):
                self.assertEqual(c.get('/ops/registration').status_code,404)
            self.assertEqual(c.get('/profile/other',headers=headers).status_code,401)

    def test_paused_admin_reserves_but_registration_and_app_issuance_stay_closed(self):
        s=self.service.snapshot()
        self.service.configure(cap=1,paused=True,sms_budget=0,revision=s['revision'],operation=uuid.uuid4().hex,actor='operator')
        p=self.preview();first=self.execute(p)
        self.assertEqual(first,self.execute(p))
        self.assertTrue(self.service.snapshot()['paused'])
        self.assertEqual(self.service.snapshot()['reserved'],1)
        self.assertCode('registrationCapacityReached',lambda:self.preview())
        self.assertCode('registrationPaused',lambda:self.service.issue(allocated_to='',source='app',operation=uuid.uuid4().hex,actor='app'))
        with patch.object(self.service.adapter,'deliver') as send:
            self.assertCode('registrationPaused',lambda:self.service.start(invitation_code=first['result']['invitationCode'],phone='13800000001',consent=True,operation=uuid.uuid4().hex,ip='isolated'))
            send.assert_not_called()
        r=self.execute(self.preview('revoke',{'invitationId':first['result']['id']}))
        self.assertEqual(r['result']['status'],'revoked')
        self.assertEqual(self.service.snapshot()['reserved'],0)
        self.assertTrue(self.service.snapshot()['paused'])
    def test_paused_admin_no_unconfigured_cap_bypass(self):
        with self.service.repo.transaction() as (s,n):s['policy']['cap']=None;s['policy']['paused']=True
        self.assertCode('registrationPaused',lambda:self.preview())
