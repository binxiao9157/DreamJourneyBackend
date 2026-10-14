#!/usr/bin/env python3
"""Only synthetic isolated localhost databases; never uses configured DATABASE_URL."""
import os, sys, unittest, uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['STORE_BACKEND']='memory'
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.postgres_store import PostgresStore
from tests.test_username_family_flow import UsernameFamilyFlowTests
import app.main as main

ADMIN=os.environ['LOCAL_FAMILY_ADMIN_DSN']
PARAMS=conninfo_to_dict(ADMIN)
assert PARAMS.get('host') in {'127.0.0.1','localhost','::1'}, 'localhost only'
assert PARAMS.get('port')=='55467', 'dedicated synthetic test port only'

class PostgresFamilyTests(UsernameFamilyFlowTests):
    def setUp(self):
        self.db='dj_family_'+uuid.uuid4().hex[:12]
        with psycopg.connect(ADMIN,autocommit=True) as c:
            c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.db)))
        self.dsn=make_conninfo(**{**PARAMS,'dbname':self.db})
        migration=PostgresMigrator(dsn=self.dsn,migrations_dir=default_migrations_dir(),build_id='family-local')
        migration.apply(); self.assertEqual(migration.verify()['status'],'ready')
        self.pg=PostgresStore(dsn=self.dsn,pool_min_size=1,pool_max_size=6,pool_timeout_seconds=5)
        self.pg.open_pool(wait=True)
        self.store_patch=patch('tests.test_delegated_access_api.InMemoryStore',return_value=self.pg)
        self.store_patch.start()
        super().setUp()

    def tearDown(self):
        super().tearDown(); self.store_patch.stop(); self.pg.close_pool()
        # Keep synthetic databases for evidence until the dedicated cluster is stopped.

    def test_pg_failed_profile_write_rolls_back_registration(self):
        m=self.registration_invite()
        with patch.object(main.store,'save_profile',side_effect=RuntimeError('synthetic write failure')):
            with self.assertRaises(Exception): self.register(m)
        self.assertIsNone(self.identity.subject_for_target(identity_type='username',target='family_alice'))
        self.assertNotIn('registrationSubjectId',main.store.get_family_invitation(m['invitationCode']))
        self.assertEqual(self.register(m).status_code,200)

    def test_pg_pool_reopen_preserves_username_and_single_use(self):
        m=self.registration_invite(); self.assertEqual(self.register(m).status_code,200)
        first=self.username_login().json()['user']['id']
        self.pg.close_pool()
        self.pg=PostgresStore(dsn=self.dsn,pool_min_size=1,pool_max_size=6,pool_timeout_seconds=5)
        self.pg.open_pool(wait=True)
        main.store=self.pg
        # Reconstruct service dependencies as a new process would.
        self.identity.store=self.pg
        self.identity.auth_session_service=main._auth_session_service()
        from app.services.password_authentication import PasswordAuthenticationService
        refreshed=patch.object(main, '_password_authentication_service', return_value=PasswordAuthenticationService(
            self.pg,identity_binding_service=self.identity,auth_session_service=main._auth_session_service(),
            token_hmac_key='username-test-key-'+'x'*40))
        refreshed.start(); self.addCleanup(refreshed.stop)
        self.assertEqual(self.username_login().json()['user']['id'],first)
        self.assertEqual(self.register(m,name='family_bob').status_code,400)

names=['test_registration_login_accept_and_explicit_one_way_grant',
       'test_concurrent_single_invite_has_one_account',
       'test_username_unique_case_insensitive_and_failed_attempt_preserves_invite',
       'test_existing_username_invite_is_bound_and_does_not_grant',
       'test_v2_accept_binds_real_subject_and_survives_refresh_and_repeat',
       'test_pg_failed_profile_write_rolls_back_registration',
       'test_pg_pool_reopen_preserves_username_and_single_use',
       'test_owner_can_cancel_unused_registration_invite_without_deleting_accounts',
       'test_active_live_lease_revoked_and_regrant_cannot_revive']
if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(PostgresFamilyTests(n) for n in names))
    sys.exit(0 if result.wasSuccessful() else 1)
