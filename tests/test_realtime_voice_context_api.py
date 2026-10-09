"""Exercise actual HTTP/auth/snapshot/retrieval path with confirmed projection fixtures."""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import app.main as main
import test_owner_truth_context_authority_api as fixture_module
client = fixture_module.client
from app.services.realtime_voice_proxy import RealtimeVoiceSessionBroker
from app.services.realtime_voice_context import verify_context_grant


class ContextAPITests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.OwnerTruthContextAuthorityAPITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.old_settings = main.settings
        main.settings = replace(main.settings, store_backend='memory', realtime_voice_proxy_enabled=True,
            public_base_url='https://example.test', volcengine_app_id='test', volcengine_app_key='test',
            volcengine_app_token='test', volcengine_realtime_resource_id='volc.speech.dialog',
            owner_truth_live_context_update_enabled=True)
        self.addCleanup(setattr, main, 'settings', self.old_settings)
        self.owner, self.headers = fixture_module.OwnerTruthContextAuthorityAPITests._login('13800003311')
        fixture_module.OwnerTruthContextAuthorityAPITests._enable_authenticated_owner_v4(self.owner)
        fixture_module.OwnerTruthContextAuthorityAPITests._seed_confirmed_school_memory(self.owner)

    def issue(self, **extra):
        response = client.post('/voice/realtime-token', headers=self.headers, json={
            'userId': self.owner, 'supportsLiveContextUpdateV1': True, 'clientSessionId': 'live-api-test', **extra})
        self.assertEqual(response.status_code, 200, response.text)
        self.config = response.json()
        self.broker = RealtimeVoiceSessionBroker(main.settings, main.store)
        self.lease = self.broker.consume(self.config['proxy']['sessionToken'])
        self.assertIsNotNone(self.lease)
        return self.config

    def request(self, **extra):
        return client.post('/voice/realtime-context', headers=self.headers, json={
            'userId': self.owner, 'ticketId': self.lease['ticketId'], 'query': 'A 大学计算机毕业',
            'sequence': 1, 'previousHash': self.lease['providerContextHash'], **extra})

    def test_current_confirmed_facts_selected_and_signed_no_answer_provider(self):
        config = self.issue()
        self.assertEqual(config['contextUpdate']['version'], 1)
        self.assertNotIn(self.lease['contextUpdateKey'], json.dumps(config))
        response = self.request()
        self.assertEqual(response.status_code, 200, response.text)
        grant = response.json()
        claims = verify_context_grant(grant['envelope'], self.lease,
            previous_hash=self.lease['providerContextHash'], sequence=1, now=grant['expiresAtUnix']-1)
        self.assertIn('A 大学', claims['role'])
        self.assertIn('2016', claims['role'])
        self.assertIn('未检索到不表示', claims['role'])
        self.assertLessEqual(len(claims['role'].encode()), 8192)

    def test_gap_is_valid_empty_related_snapshot_not_all_old_facts(self):
        self.issue()
        response = self.request(query='养猫的颜色与品种')
        self.assertEqual(response.status_code, 200, response.text)
        grant = response.json()
        claims = verify_context_grant(grant['envelope'], self.lease,
            previous_hash=self.lease['providerContextHash'], sequence=1, now=grant['expiresAtUnix']-1)
        self.assertNotIn('A 大学', claims['role'])

    def test_other_owner_cannot_obtain_grant(self):
        self.issue()
        other, headers = fixture_module.OwnerTruthContextAuthorityAPITests._login('13800003312')
        response = client.post('/voice/realtime-context', headers=headers, json={
            'userId': other, 'ticketId': self.lease['ticketId'], 'query': '大学',
            'sequence': 1, 'previousHash': self.lease['providerContextHash']})
        self.assertEqual(response.status_code, 409, response.text)

    def test_closed_and_revoked_ticket_rejected(self):
        self.issue()
        self.broker.release(self.lease, reason='test')
        self.assertEqual(self.request().status_code, 409)

    def test_revision_change_rejected(self):
        self.issue()
        fixture_module.OwnerTruthContextAuthorityAPITests._seed_confirmed_memory(self.owner)
        self.assertEqual(self.request().status_code, 409)

    def test_optin_and_server_flag_are_both_required(self):
        for enabled, optin in ((False, True), (True, False)):
            main.settings = replace(main.settings, owner_truth_live_context_update_enabled=enabled)
            config = self.issue(supportsLiveContextUpdateV1=optin)
            self.assertNotIn('contextUpdate', config)
            self.assertNotIn('contextUpdateKey', self.lease)
            self.broker.release(self.lease, reason='test')

    def test_invalid_sequence_and_query_rejected(self):
        self.issue()
        for extra in ({'sequence': 129}, {'sequence': True}, {'query': 'x'*513}, {'previousHash': 'bad'}):
            self.assertEqual(self.request(**extra).status_code, 400)
