import base64
from hashlib import sha256
from io import BytesIO
from uuid import uuid4
from unittest import TestCase
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
from fastapi.testclient import TestClient
from app import main as main
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyService, ReleasePolicyCommandGate


def photo(size=(256, 256), format='JPEG', metadata=False):
    image = Image.new('RGB', size, (10, 80, 160))
    out = BytesIO()
    exif = Image.Exif(); exif[270] = 'private location description'
    image.save(out, format=format, **({'exif': exif} if metadata else {}))
    return base64.b64encode(out.getvalue()).decode()


class ProfileAvatarTests(TestCase):
    def setUp(self):
        self.store = InMemoryStore()
        self.policy = ReleasePolicyService(shadow_mode=False, enforce_default_closed_stages=True, authenticated_owner_v4_enabled=True)
        for key, value in {'store': self.store, 'AUTH_ROUTE_MODE': 'enforce', 'AUTH_OWNERSHIP_MODE': 'enforce', 'AUTH_LEGACY_PHONE_LOGIN_ENABLED': True, 'RELEASE_POLICY_COMMAND_MODE': 'enforce', 'RELEASE_POLICY_SERVICE': self.policy, 'RELEASE_POLICY_COMMAND_GATE': ReleasePolicyCommandGate(self.policy)}.items():
            p = patch.object(main, key, value); p.start(); self.addCleanup(p.stop)
        self.client = TestClient(main.app)
        login = self.client.post('/auth/login', json={'phone': '13900001234', 'nickname': '头像测试'}).json()
        self.uid = login['user']['id']
        self.headers = {'Authorization': 'Bearer '+login['auth']['accessToken']}
        snapshot = self.client.get('/v2/release-policy',params={'audience':'owner','clientBuild':1,'feature':'profileSettings'},headers=self.headers).json()
        self.headers.update({'X-DreamJourney-Client-Build':'1','X-DreamJourney-Feature':'profileSettings','X-DreamJourney-Feature-Decision-Id':str(uuid4()),'X-DreamJourney-Feature-Allowed':'true','X-DreamJourney-Policy-Version':snapshot['policyVersion'],'X-DreamJourney-Policy-Revision':str(snapshot['policyRevision']),'X-DreamJourney-Account-Generation':sha256(login['auth']['sessionId'].encode()).hexdigest()[:24]})

    def upload(self, image=None, **extra):
        return self.client.post('/profile', headers=self.headers, json={'userId':self.uid,'operation':'updateAvatar','avatarJPEGBase64':photo() if image is None else image,**extra})

    def test_upload_read_preserves_profile_and_legacy_save_preserves_avatar(self):
        self.store.save_profile(self.uid, {'nickname':'保留昵称','gender':'女','region':'杭州'})
        response = self.upload(photo(metadata=True)); self.assertEqual(response.status_code,200,response.text)
        p = response.json()['profile']; avatar=p['avatar']; self.assertEqual(p['nickname'],'保留昵称')
        raw=base64.b64decode(avatar['imageBase64']); self.assertEqual(sha256(raw).hexdigest(),avatar['sha256'])
        with Image.open(BytesIO(raw)) as image:
            self.assertEqual(image.size,(256,256));self.assertFalse(image.getexif());self.assertNotIn('comment',image.info)
        saved=self.client.post('/profile',headers=self.headers,json={'userId':self.uid,'nickname':'新昵称'})
        self.assertEqual(saved.status_code,200,saved.text)
        read=self.client.get('/profile/'+self.uid,headers=self.headers)
        self.assertEqual(read.json()['profile']['avatar'],avatar);self.assertEqual(read.json()['profile']['nickname'],'新昵称')

    def test_first_avatar_without_prior_profile(self):
        r=self.upload();self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['profile']['userId'],self.uid)

    def test_invalid_images_leave_previous_value_unchanged(self):
        self.assertEqual(self.upload().status_code,200)
        previous=self.store.get_profile(self.uid)
        for data in ('not base64',photo((255,256)),photo(format='PNG'),base64.b64encode(b'not jpeg').decode(),'A'*180000,photo()[:-12]):
            with self.subTest(length=len(data)):
                self.assertIn(self.upload(data).status_code,(400,413,422))
                self.assertEqual(self.store.get_profile(self.uid),previous)

    def test_avatar_cannot_mutate_profile_fields_or_bypass_operation(self):
        self.assertEqual(self.upload(nickname='clobber').status_code,400)
        r=self.client.post('/profile',headers=self.headers,json={'userId':self.uid,'nickname':'test','avatar':{'imageBase64':'invalid'}})
        self.assertEqual(r.status_code,400)

    def test_anonymous_cross_account_and_emergency_denied(self):
        self.assertIn(self.client.post('/profile',json={'userId':self.uid,'operation':'updateAvatar','avatarJPEGBase64':photo()}).status_code,(401,403))
        self.assertEqual(self.upload(userId='someone-else').status_code,403)
        self.policy.emergency_disabled_features.add('profileSettings')
        self.assertEqual(self.upload().status_code,403)
        self.assertIsNone(self.store.get_profile(self.uid))

    def test_concurrent_avatar_and_profile_are_independent(self):
        avatar={'imageBase64':photo(),'sha256':'synthetic'}
        def update(i):
            if i%2:self.store.save_profile(self.uid,{'nickname':'姓名'})
            else:self.store.save_profile_avatar(self.uid,avatar)
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(update,range(80)))
        result=self.store.get_profile(self.uid);self.assertEqual(result['avatar'],avatar);self.assertEqual(result['nickname'],'姓名')

    def test_avatar_in_account_export_and_terminal_purge(self):
        from app.services.data_rights_module_inventory import build_module_owned_data_export
        import json
        response = self.upload(); self.assertEqual(response.status_code, 200)
        avatar = response.json()["profile"]["avatar"]
        export = build_module_owned_data_export(self.store, user_id=self.uid)
        self.assertIn(avatar["sha256"], json.dumps(export))
        self.assertIn(avatar["imageBase64"], json.dumps(export))
        self.store.soft_delete_user(self.uid, phone="13900001234", requested_at_iso="2026-01-01T00:00:00+00:00", deletion_request_id="synthetic-avatar-purge")
        self.store.purge_expired_deleted_users("2026-03-01T00:00:00+00:00")
        self.assertIsNone(self.store.get_profile(self.uid))
