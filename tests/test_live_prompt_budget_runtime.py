"""In-process production route and ticket integration; no external provider."""
from dataclasses import replace
import copy
import json
import os
import unittest
from unittest.mock import patch
from starlette.requests import Request
from fastapi import HTTPException
import app.main as main
from app.core.config import Settings
from app.services.route_authentication import RequestPrincipal, PrincipalKind
import test_realtime_voice_proxy as proxy_tests
from test_formal_memory_conversation_snapshot import _ProjectionStore, _ready_projection


class LivePromptBudgetRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = proxy_tests.RealtimeVoiceSessionBrokerTests('runTest')
        self.fixture.setUp()
        self.store = self.fixture.store
        self.owner = self.fixture.user['id']
        self.projection = _ready_projection()
        self.projection['entries'] = [
            {'memoryVersionId': f'synthetic-{i}', 'memoryKind': 'knowledge',
             'content': {'statement': f'第{i}条合成测试事实：过去住在青山，现在搬到湖边。'}}
            for i in range(200)]
        self.before = copy.deepcopy(self.projection)
        self.store.owner_truth_memory_projection_repository = lambda: _ProjectionStore(self.projection)
        self.store._owner_truth_vaults[self.owner] = {
            'ownerSubjectId': self.owner, 'authorityEpoch': 7, 'status': 'active'}
        self.request = Request({'type': 'http', 'method': 'POST', 'path': '/voice/realtime-token', 'headers': []})
        self.request.state.auth_principal = RequestPrincipal(
            kind=PrincipalKind.USER, principal_id=self.owner, session_id=self.fixture.auth['sessionId'],
            token_family_id='test-family', session_version=1, audience='test', scopes=frozenset({'test'}))

    def test_route_passes_configured_byte_budget_and_ticket_consumes_exact_binding(self):
        settings = replace(self.fixture.settings, realtime_voice_system_role_max_bytes=3000)
        with patch.object(main, 'store', self.store), patch.object(main, 'settings', settings):
            response = main.realtime_token(self.request, {'purpose': 'echoLive', 'targetPersonaId': self.owner})
        body = json.loads(response.body)
        self.assertEqual(body['contractVersion'], 7)
        snapshot = body['sessionContext']['formalMemorySnapshot']
        self.assertLessEqual(snapshot['providerRoleByteCount'], 3000)
        self.assertGreater(snapshot['coverage']['omittedFactCount'], 0)
        self.assertGreater(len(snapshot['coreFacts']), 0)
        self.assertEqual(body['echoSession']['contextHash'], snapshot['contextHash'])
        self.assertEqual(body['sessionContext']['providerRoleText'], snapshot['providerRoleText'])
        lease = self.fixture.broker.consume(body['proxy']['sessionToken'])
        self.assertEqual(lease['contextHash'], snapshot['contextHash'])
        self.assertEqual(lease['providerContextHash'], snapshot['providerContextHash'])
        self.assertEqual(self.projection, self.before)

    def test_budget_selection_does_not_bypass_target_authorization(self):
        with patch.object(main, 'store', self.store), patch.object(main, 'settings', self.fixture.settings):
            with self.assertRaises(HTTPException) as result:
                main.realtime_token(self.request, {'purpose': 'echoLive', 'targetPersonaId': 'someone-else'})
        self.assertEqual(result.exception.status_code, 403)

    def test_environment_setting_is_wired(self):
        with patch.dict(os.environ, {'STORE_BACKEND': 'memory', 'REALTIME_VOICE_SYSTEM_ROLE_MAX_BYTES': '4096'}):
            self.assertEqual(Settings.from_env().realtime_voice_system_role_max_bytes, 4096)


if __name__ == '__main__':
    unittest.main()
