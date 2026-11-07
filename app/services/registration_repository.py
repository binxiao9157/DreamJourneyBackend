"""Small-pilot admission ledger. Only enrollment takes the policy lock.

Postgres uses the existing request UoW: identity and ledger commit together.
No network calls or password hashing may run inside this transaction.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock

KINDS = ('codes', 'attempts', 'seats', 'operations', 'events', 'adminSessions', 'adminAttempts')


def empty_state():
    return {'policy': {'cap': None, 'paused': True, 'revision': 0, 'smsBudget': 0,
                       'smsUsed': 0}, **{k: {} for k in KINDS}}


class RegistrationRepository:
    def __init__(self, store):
        self.store = store
        self.postgres = hasattr(store, '_fetchone')
        store._registration_admission_active = True
        if not self.postgres and not hasattr(store, '_registration_lock'):
            store._registration_lock = RLock()
            store._registration_state = empty_state()

    @contextmanager
    def transaction(self):
        s = self.store
        if self.postgres:
            with s.request_unit_of_work(correlation_id='registration', command_id='registrationAdmission'):
                row = s._fetchone('SELECT payload, clock_timestamp() AS instant FROM registration_policy WHERE id=1 FOR UPDATE', ())
                if not row:
                    raise ValueError('registrationMigrationRequired')
                state = {'policy': deepcopy(row['payload']), **{k: {} for k in KINDS}}
                for record in s._fetchall('SELECT kind, id, payload FROM registration_records', ()):
                    state[record['kind']][record['id']] = deepcopy(record['payload'])
                before = deepcopy(state)
                yield state, row['instant'].timestamp()
                self.flush(state, before)
        else:
            # Fixed order; no legacy writer ever acquires registration after identity.
            with s._registration_lock, s._identity_lock, s._delegated_access_lock:
                attrs = ('_registration_state', '_subjects', '_identity_bindings',
                         '_identity_binding_ids_by_target', '_identity_proofs',
                         '_password_credentials', '_profiles', '_family_members')
                snapshots = {a: deepcopy(getattr(s, a)) for a in attrs if hasattr(s, a)}
                try:
                    yield s._registration_state, datetime.now(timezone.utc).timestamp()
                except BaseException:
                    for a, value in snapshots.items():
                        setattr(s, a, value)
                    raise

    def flush(self, state, before=None):
        if not self.postgres:
            return
        s = self.store
        if before is None or before['policy'] != state['policy']:
            s._fetchone('UPDATE registration_policy SET payload=%s WHERE id=1 RETURNING id', (state['policy'],), commit=True)
        for kind in KINDS:
            for key, value in state[kind].items():
                if before is None or before[kind].get(key) != value:
                    s._fetchone('''INSERT INTO registration_records (kind,id,payload) VALUES (%s,%s,%s)
                        ON CONFLICT (kind,id) DO UPDATE SET payload=EXCLUDED.payload RETURNING id''',
                        (kind, key, value), commit=True)

    def create_account(self, state, *, reference, subject, binding, credential, nickname, now, proof):
        s = self.store
        iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
        state['seats'][subject] = {'status': 'occupied', 'createdAt': now}
        if self.postgres:
            # The DB writer guard checks this seat before allowing a new identity.
            self.flush(state)
            s._fetchone("INSERT INTO subjects(id,status) VALUES (%s,'active') RETURNING id", (subject,), commit=True)
            s._fetchone('''INSERT INTO identity_bindings(id,subject_id,identity_type,target_hash_key_version,
                target_hash,provider_mode,status,verified_at) VALUES (%s,%s,%s,%s,%s,%s,'active',%s) RETURNING id''',
                (binding, subject, reference['identityType'], reference['targetHashKeyVersion'], reference['targetHash'],
                 proof['providerMode'] if proof else 'invitedUsernamePassword', iso), commit=True)
            if proof:
                s._fetchone("""INSERT INTO auth_challenges(id,identity_type,target_hash_key_version,target_hash,
                    code_hash,provider_mode,purpose,status,max_attempts,expires_at,consumed_at)
                    VALUES (%s,'phone',%s,%s,%s,%s,'register','consumed',5,%s,%s) RETURNING id""",
                    (proof['attempt'],reference['targetHashKeyVersion'],reference['targetHash'],proof['codeHash'],
                     proof['providerMode'],iso,iso),commit=True)
                s._fetchone("""INSERT INTO identity_proofs(id,challenge_id,binding_id,subject_id,provider_mode,verified_at)
                    VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (proof['id'],proof['attempt'],binding,subject,proof['providerMode'],iso),commit=True)
            s.save_password_credential(subject, credential)
        else:
            s._subjects[subject] = {'subjectId':subject, 'status':'active', 'createdAt':iso}
            s._identity_bindings[binding] = {'bindingId':binding, 'subjectId':subject, **reference,
                'status':'active', 'providerMode':proof['providerMode'] if proof else 'invitedUsernamePassword', 'verifiedAt':iso}
            s._identity_binding_ids_by_target[(reference['identityType'],reference['targetHashKeyVersion'],reference['targetHash'])] = binding
            s._password_credentials[subject] = {**credential, 'userId':subject, 'updatedAt':iso}
            if proof:
                s._identity_proofs[proof['id']] = {'proofReceiptId':proof['id'], 'bindingId':binding, 'subjectId':subject,
                    'purpose':'register', 'verifiedAt':iso}
        s.save_profile(subject, {'userId':subject, 'nickname':nickname})

    def mark_family_registered(self, code, subject, now):
        s = self.store
        iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
        if self.postgres:
            s._fetchall('''UPDATE family_members SET payload=payload || %s::jsonb
                WHERE payload->>'invitationCode'=%s RETURNING id''',
                ({'registrationSubjectId':subject,'registeredAt':iso},code))
        else:
            for members in s._family_members.values():
                for member in members:
                    if member.get('invitationCode') == code:
                        member.update(registrationSubjectId=subject,registeredAt=iso)
