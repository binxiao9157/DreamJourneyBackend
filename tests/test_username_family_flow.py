from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import app.main as main
from tests.test_family_onboarding_v2 import FamilyOnboardingV2Tests
from app.services.password_authentication import PasswordAuthenticationService
from app.services.family_echo_access import authorize_family_echo, binding_is_current
from app.services.delegated_access import AccessGrantCommand, RevokeAccessGrantCommand

class UsernameFamilyFlowTests(FamilyOnboardingV2Tests):
    def setUp(self):
        super().setUp()
        for p in [patch.object(main, 'settings', replace(main.settings, username_registration_enabled=True)),
                  patch.object(main, '_password_authentication_service', return_value=PasswordAuthenticationService(
                      main.store, identity_binding_service=self.identity, auth_session_service=main._auth_session_service(),
                      token_hmac_key='username-test-key-'+'x'*40))]:
            p.start(); self.addCleanup(p.stop)

    def registration_invite(self):
        r = self.client.post('/family/invite', headers=self.headers(self.owner), json={
            'userId': self.owner['user']['id'], 'invitationKind':'accountRegistration', 'name':'受邀家人', 'relation':'家人'})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['member']

    def register(self, invitation, name='family_alice', **extra):
        return self.client.post('/v2/auth/username/register', json={
            'invitationCode':invitation['invitationCode'], 'username':name,
            'password':'SyntheticOnly-123!', 'legalConsent':True, **extra})

    def username_login(self, name='family_alice', password='SyntheticOnly-123!'):
        return self.client.post('/v2/auth/password/login', json={
            'identityType':'username', 'target':name, 'password':password})

    def test_registration_login_accept_and_explicit_one_way_grant(self):
        m=self.registration_invite(); registered=self.register(m)
        self.assertEqual(registered.status_code,200,registered.text)
        login=self.username_login('FAMILY_ALICE'); self.assertEqual(login.status_code,200,login.text)
        body=login.json(); viewer=body['user']['id']; owner=self.owner['user']['id']
        denied=self.client.post('/family/invitations/'+m['invitationCode']+'/accept', headers=self.headers(self.stranger), json={})
        self.assertEqual(denied.status_code,403)
        accepted=self.client.post('/family/invitations/'+m['invitationCode']+'/accept', headers=self.headers(body), json={})
        self.assertEqual(accepted.status_code,200,accepted.text)
        member=accepted.json()['member']; self.assertEqual(member['memberSubjectId'],viewer)
        with self.assertRaises(ValueError):
            authorize_family_echo(main.store,viewer=viewer,owner=owner,member=m['id'])
        grant=main._delegated_access_service().grant_access(AccessGrantCommand(
            grantorSubjectId=owner,granteeSubjectId=viewer,relationshipId=member['relationshipId'],
            purpose='family.persona',resourceType='familyMember',resourceId=m['id'],operations=['read']))
        binding=authorize_family_echo(main.store,viewer=viewer,owner=owner,member=m['id'])
        with self.assertRaises(ValueError):
            authorize_family_echo(main.store,viewer=owner,owner=viewer,member=m['id'])
        listing=self.client.get('/family/members/'+viewer,headers=self.headers(body)).json()['members']
        self.assertEqual(len(listing),1); self.assertEqual(listing[0]['digitalHumanId'],owner)
        self.assertNotIn('invitationCode',listing[0]); self.assertNotIn('phone',listing[0])
        main._delegated_access_service().revoke_access(RevokeAccessGrantCommand(grantorSubjectId=owner,
            grantId=grant['id'],expectedVersion=grant['rowVersion']))
        self.assertFalse(binding_is_current(main.store,binding))
        self.assertEqual(self.client.get('/family/members/'+viewer,headers=self.headers(body)).json()['members'],[])

    def test_invitation_cannot_be_reused_or_register_without_consent(self):
        m=self.registration_invite()
        self.assertEqual(self.register(m,legalConsent=False).status_code,400)
        self.assertEqual(self.register(m).status_code,200)
        self.assertEqual(self.register(m,name='family_other').status_code,400)
        self.assertEqual(self.username_login(password='incorrect').status_code,401)

    def test_username_unique_case_insensitive_and_failed_attempt_preserves_invite(self):
        a=self.registration_invite(); b=self.registration_invite()
        self.assertEqual(self.register(a).status_code,200)
        self.assertEqual(self.register(b,name='FAMILY_ALICE').status_code,400)
        self.assertEqual(self.register(b,name='family_bob').status_code,200)

    def test_invalid_expired_or_revoked_invitation(self):
        m=self.registration_invite()
        stored=main.store._family_members[self.owner['user']['id']][-1]
        stored['invitationExpiresAt']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        self.assertEqual(self.register(m).status_code,400)
        stored['invitationExpiresAt']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        stored['invitationStatus']='revoked'
        self.assertEqual(self.register(m).status_code,400)
        self.assertEqual(self.register({'invitationCode':'UNKNOWN-CODE-123456789'}).status_code,400)

    def test_concurrent_single_invite_has_one_account(self):
        m=self.registration_invite()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda name:self.register(m,name=name).status_code,['family_alice','family_bob']))
        self.assertEqual(sorted(results),[200,400])

    def test_username_cannot_use_sms_challenge(self):
        r=self.client.post('/v2/auth/challenges',json={'identityType':'username','target':'family_alice','purpose':'login'})
        self.assertEqual(r.status_code,400)

    def granted_family(self):
        invite=self.registration_invite()
        self.assertEqual(self.register(invite).status_code,200)
        viewer=self.username_login().json()
        accepted=self.client.post('/family/invitations/'+invite['invitationCode']+'/accept',
            headers=self.headers(viewer),json={}).json()['member']
        grant=main._delegated_access_service().grant_access(AccessGrantCommand(
            grantorSubjectId=self.owner['user']['id'],granteeSubjectId=viewer['user']['id'],
            relationshipId=accepted['relationshipId'],purpose='family.persona',
            resourceType='familyMember',resourceId=invite['id'],operations=['read']))
        return viewer,accepted,grant

    def seed_formal_memory(self):
        from tests.test_owner_truth_context_authority import OwnerTruthContextAuthorityTests
        from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
        from app.domain.owner_truth.contracts import MemoryKind, EpistemicStatus
        fixture=OwnerTruthContextAuthorityTests()
        fixture.store=main.store
        fixture.owner_id=self.owner['user']['id']
        fixture.context=OwnerTruthCommandContext(vault_id=fixture.owner_id,
            owner_subject_id=fixture.owner_id,actor_subject_id=fixture.owner_id)
        fixture._activate_memory(kind=MemoryKind.EXPERIENCE,content={'summary':'我把紫色风铃挂在阳台，周末清洁。'})
        fixture._activate_memory(kind=MemoryKind.KNOWLEDGE,content={'claim':'INFERRED_SECRET不可用于回答'},
            epistemic_status=EpistemicStatus.INFERRED)
        fixture._rebuild_projection()
        return fixture

    def test_formal_read_excludes_raw_and_inferred_and_denies_unrelated_owner(self):
        viewer,member,grant=self.granted_family(); self.seed_formal_memory()
        payload={'userId':viewer['user']['id'],'personaScope':'family','digitalHumanId':self.owner['user']['id'],
            'viewerFamilyMemberID':member['id'],'query':'紫色风铃放在哪里','intent':'echo_chat'}
        r=self.client.post('/context/build',headers=self.headers(viewer),json=payload)
        self.assertEqual(r.status_code,200,r.text)
        import json
        serialized=json.dumps(r.json(),ensure_ascii=False)
        self.assertIn('紫色风铃',serialized)
        self.assertNotIn('由 Owner 明确提交的个人回忆原文',serialized)
        self.assertNotIn('INFERRED_SECRET',serialized)
        self.assertNotIn('selectedContext',r.json())
        for mutation in [{'digitalHumanId':self.stranger['user']['id']},{'viewerFamilyMemberID':'wrong'},
                         {'personaScope':'personal'}]:
            denied=self.client.post('/context/build',headers=self.headers(viewer),json={**payload,**mutation})
            self.assertIn(denied.status_code,[403,409],denied.text)
        denied=self.client.get('/family/members/'+self.owner['user']['id'],headers=self.headers(viewer))
        self.assertEqual(denied.status_code,403)

    def test_revocation_during_text_generation_discards_answer(self):
        viewer,member,grant=self.granted_family(); self.seed_formal_memory()
        def generated(**kwargs):
            self.assertIn('紫色风铃',kwargs['generation_context'])
            main._delegated_access_service().revoke_access(RevokeAccessGrantCommand(
                grantorSubjectId=self.owner['user']['id'],grantId=grant['id'],expectedVersion=grant['rowVersion']))
            return '紫色风铃在阳台。'
        with patch.object(main.DeepSeekEchoAnswerProxy,'request_answer',side_effect=generated) as provider:
            r=self.client.post('/echo/answers',headers=self.headers(viewer),json={
                'userId':viewer['user']['id'],'personaScope':'family','digitalHumanId':self.owner['user']['id'],
                'viewerFamilyMemberID':member['id'],'query':'紫色风铃放在哪里','intent':'echo_chat'})
        self.assertEqual(provider.call_count,1,r.text)
        self.assertEqual(r.status_code,403,r.text)
        self.assertNotIn('紫色风铃在阳台',r.text)

    def test_active_live_lease_revoked_and_regrant_cannot_revive(self):
        from app.services.realtime_voice_proxy import RealtimeVoiceSessionBroker
        from app.core.config import Settings
        viewer,member,grant=self.granted_family()
        owner=self.owner['user']['id']; vid=viewer['user']['id']
        binding=authorize_family_echo(main.store,viewer=vid,owner=owner,member=member['id'])
        broker=RealtimeVoiceSessionBroker(Settings(store_backend='memory',realtime_voice_proxy_enabled=True,
            public_base_url='https://synthetic.invalid',volcengine_app_id='synthetic',
            volcengine_app_key='synthetic',volcengine_app_token='synthetic'),main.store)
        config=broker.issue_runtime_config(user_id=vid,auth_session_id=viewer['auth']['sessionId'],
            persona_scope='family',target_persona_id=owner,session_context={'familyAuthorization':binding})
        lease=broker.consume(config['proxy']['sessionToken'])
        self.assertTrue(broker.is_lease_authorized(lease))
        main._delegated_access_service().revoke_access(RevokeAccessGrantCommand(
            grantorSubjectId=owner,grantId=grant['id'],expectedVersion=grant['rowVersion']))
        self.assertFalse(broker.is_lease_authorized(lease))
        new=main._delegated_access_service().grant_access(AccessGrantCommand(grantorSubjectId=owner,
            granteeSubjectId=vid,relationshipId=member['relationshipId'],purpose='family.persona',
            resourceType='familyMember',resourceId=member['id'],operations=['read']))
        self.assertNotEqual(new['id'],grant['id'])
        self.assertFalse(binding_is_current(main.store,binding))
        self.assertFalse(broker.is_lease_authorized(lease))

    def test_registration_profile_failure_rolls_back_every_identity_record(self):
        m=self.registration_invite(); before=set(main.store._subjects)
        with patch.object(main.store,'save_profile',side_effect=RuntimeError('synthetic write failure')):
            with self.assertRaises(Exception): self.register(m)
        self.assertEqual(set(main.store._subjects),before)
        self.assertIsNone(self.identity.subject_for_target(identity_type='username',target='family_alice'))
        self.assertNotIn('registrationSubjectId',main.store.get_family_invitation(m['invitationCode']))
        self.assertEqual(self.register(m).status_code,200)

    def test_existing_username_invite_is_bound_and_does_not_grant(self):
        m=self.registration_invite(); self.assertEqual(self.register(m).status_code,200)
        viewer=self.username_login().json()
        r=self.client.post('/family/invite',headers=self.headers(self.stranger),json={
            'userId':self.stranger['user']['id'],'invitationKind':'username','username':'FAMILY_ALICE','name':'家人'})
        self.assertEqual(r.status_code,200,r.text)
        code=r.json()['member']['invitationCode']
        self.assertEqual(self.client.post('/family/invitations/'+code+'/accept',headers=self.headers(self.owner),json={}).status_code,403)
        r=self.client.post('/family/invitations/'+code+'/accept',headers=self.headers(viewer),json={})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['member']['memberSubjectId'],viewer['user']['id'])
        self.assertEqual(r.json()['member']['accessGrants'],[])

    def test_owner_can_cancel_unused_registration_invite_without_deleting_accounts(self):
        m=self.registration_invite()
        r=self.client.post('/family/relationships/'+m['relationshipId']+'/terminate',
            headers=self.headers(self.owner),json={'commandId':'cancel-invite-test',
                'expectedEpoch':m['relationshipEpoch'],'secondConfirmation':True})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.register(m).status_code,400)
        self.assertEqual(self.client.get('/family/members/'+self.owner['user']['id'],
            headers=self.headers(self.owner)).status_code,200)

    def test_family_live_uses_target_snapshot_and_rejects_revoked_ticket(self):
        from app.services.realtime_voice_proxy import RealtimeVoiceSessionBroker
        viewer,member,grant=self.granted_family(); self.seed_formal_memory()
        config=replace(main.settings,realtime_voice_proxy_enabled=True,public_base_url='https://synthetic.invalid',
            volcengine_app_id='synthetic',volcengine_app_key='synthetic',volcengine_app_token='synthetic')
        payload={'userId':viewer['user']['id'],'purpose':'echoLive','personaScope':'family',
            'targetPersonaId':self.owner['user']['id'],'viewerFamilyMemberID':member['id']}
        with patch.object(main,'settings',config):
            r=self.client.post('/voice/realtime-token',headers=self.headers(viewer),json=payload)
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['status'],'ready',r.text)
            record=next(iter(main.store._realtime_voice_session_tickets.values()))
            self.assertEqual(record['targetPersonaId'],self.owner['user']['id'])
            self.assertEqual(record['familyAuthorization']['viewerSubjectId'],viewer['user']['id'])
            # Ticket binds the exact server-selected formal snapshot; source text is never included.
            self.assertEqual(record['familyAuthorization']['grantId'],grant['id'])
            self.assertEqual(record['userId'],viewer['user']['id'])
            broker=RealtimeVoiceSessionBroker(config,main.store)
            lease=broker.consume(r.json()['proxy']['sessionToken'])
            self.assertTrue(broker.is_lease_authorized(lease))
            main._delegated_access_service().revoke_access(RevokeAccessGrantCommand(
                grantorSubjectId=self.owner['user']['id'],grantId=grant['id'],expectedVersion=grant['rowVersion']))
            self.assertFalse(broker.is_lease_authorized(lease))
            denied=self.client.post('/voice/realtime-token',headers=self.headers(viewer),json=payload)
            self.assertEqual(denied.status_code,403,denied.text)

    def test_disabled_registration_and_missing_key_do_not_create_account(self):
        from app.services.identity_bindings import IdentityChallengeConfigurationError
        m=self.registration_invite()
        with patch.object(main,'settings',replace(main.settings,username_registration_enabled=False)):
            self.assertEqual(self.register(m).status_code,503)
        with patch.object(main,'_identity_binding_service',side_effect=IdentityChallengeConfigurationError('missing')):
            self.assertEqual(self.register(m).status_code,503)
        self.assertNotIn('registrationSubjectId',main.store.get_family_invitation(m['invitationCode']))
