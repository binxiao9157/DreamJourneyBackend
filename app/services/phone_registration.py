"""Invite admission and phone enrollment. Never participates in Echo or memory reads."""
import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timezone
from cryptography.fernet import Fernet
from app.services.registration_repository import RegistrationRepository
from app.services.passwords import make_password_credential


class RegistrationError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def phone_target(value):
    value = re.sub(r'[\s\-()]', '', str(value or ''))
    if value.startswith('+86'): value = value[3:]
    elif value.startswith('0086'): value = value[4:]
    if not re.fullmatch(r'1[3-9][0-9]{9}', value):
        raise RegistrationError('registrationPhoneInvalid')
    return '+86' + value


class PhoneRegistrationService:
    def __init__(self, store, identity, *, secret, sms_adapter=None):
        if len(str(secret).encode()) < 32:
            raise RegistrationError('registrationConfigurationUnavailable', 503)
        self.store, self.identity = store, identity
        self.repo = RegistrationRepository(store)
        self.key = str(secret).encode()
        self.cipher = Fernet(base64.urlsafe_b64encode(hmac.digest(self.key,b'registration-encryption-v1','sha256')))
        self.adapter = sms_adapter or identity.adapter
        key_state = store.ensure_identity_hash_key_version(identity.hmac_key_version, identity._hmac_key_fingerprint)
        if key_state.get('outcome') != 'ready':
            raise RegistrationError('registrationIdentityKeyUnavailable',503)

    def digest(self, value):
        return hmac.new(self.key,str(value).encode(),hashlib.sha256).hexdigest()

    def _operation(self, value):
        if not re.fullmatch(r'[A-Za-z0-9_-]{20,96}', str(value or '')):
            raise RegistrationError('registrationOperationInvalid')
        return self.digest('operation:'+value)

    def _code_id(self, code):
        code = str(code or '').strip().upper()
        if not re.fullmatch(r'[A-Z0-9_-]{16,96}',code):
            raise RegistrationError('registrationInviteUnavailable')
        return self.digest('invite:'+code)

    def _expire(self, state, now):
        for key, code in state['codes'].items():
            if code['status']=='reserved' and code['expiresAt'] <= now:
                code['status']='expired'
                state['events']['expire:'+key]={'type':'expired','at':now}
            if code['status']=='reserved':
                raw=self.cipher.decrypt(code['encryptedCode'].encode()).decode()
                legacy=self.store.get_family_invitation(raw)
                if legacy and legacy.get('invitationStatus') in {'revoked','expired','cancelled'}:
                    code['status']='revoked'
                    state['events']['revoke:'+key]={'type':'familyInviteRevoked','at':now}

    def _stats(self, state, now):
        self._expire(state,now)
        occupied=[x for x in state['seats'].values() if x['status']=='occupied']
        r=sum(x.get('classification','real')=='real' for x in occupied)
        historical={k:sum(x.get('classification','real')==k for x in occupied) for k in ('test','legacyReview')}
        p=sum(x['status']=='reserved' for x in state['codes'].values())
        return {**state['policy'], 'registered':r, 'reserved':p,
                'compatibleSubjects':len(occupied),'testSubjects':historical['test'],'legacyReviewSubjects':historical['legacyReview'],
                'available':max(0,(state['policy']['cap'] or 0)-r-p)}

    def snapshot(self):
        with self.repo.transaction() as (s,now):
            self._import_legacy(s,now)
            return {**self._stats(s,now), 'invitations':[{'id':k, **{f:v.get(f) for f in
                ('status','expiresAt','allocatedTo','source','subjectId')}} for k,v in s['codes'].items()]}

    def _open(self, s):
        if s['policy']['paused'] or s['policy']['cap'] is None:
            raise RegistrationError('registrationPaused',409)

    def _invite(self, s, key, now):
        self._expire(s,now)
        item=s['codes'].get(key)
        if not item or item['status']!='reserved':
            raise RegistrationError('registrationInviteUnavailable',409)
        # Legacy family revocation remains effective during migration.
        raw=self.cipher.decrypt(item['encryptedCode'].encode()).decode()
        old=self.store.get_family_invitation(raw)
        if old and (old.get('invitationStatus')!='pending' or old.get('accessStatus')!='pending' or old.get('registrationSubjectId')):
            raise RegistrationError('registrationInviteUnavailable',409)
        return item

    def _replay(self,s,key,payload):
        fingerprint=self.digest(json.dumps(payload,sort_keys=True,separators=(',',':')))
        old=s['operations'].get(key)
        if old and old['fingerprint']!=fingerprint:
            raise RegistrationError('registrationIdempotencyConflict',409)
        return old, fingerprint

    def configure(self, *, cap, paused, sms_budget, revision, operation, actor):
        if type(cap) is not int or cap<0 or type(sms_budget) is not int or sms_budget<0 or type(paused) is not bool:
            raise RegistrationError('registrationPolicyInvalid')
        key=self._operation(operation)
        with self.repo.transaction() as (s,now):
            old,fp=self._replay(s,key,[cap,paused,sms_budget,revision,actor])
            if old: return old['result']
            self._import_legacy(s,now)
            stats=self._stats(s,now)
            if revision!=s['policy']['revision']: raise RegistrationError('registrationPolicyConflict',409)
            if cap<stats['registered']+stats['reserved']: raise RegistrationError('registrationCapBelowOccupancy',409)
            if sms_budget<s['policy']['smsUsed']: raise RegistrationError('registrationBudgetBelowUsed',409)
            s['policy'].update(cap=cap,paused=paused,smsBudget=sms_budget,revision=revision+1)
            result=self._stats(s,now)
            s['operations'][key]={'fingerprint':fp,'result':result}
            s['events'][key]={'type':'policyUpdated','actor':actor,'at':now,'revision':revision+1}
            return result

    def _import_legacy(self,s,now):
        if self.repo.postgres:
            rows=self.store._fetchall("SELECT payload FROM family_members WHERE payload->>'invitationKind'='accountRegistration'",())
            invites=[r['payload'] for r in rows]
        else:
            for subject in set(self.store._subjects) | set(self.store._users):
                s['seats'].setdefault(subject,{'status':'occupied','source':'migration'})
            invites=[v for rows in self.store._family_members.values() for v in rows]
        from app.services.username_registration import invitation_is_available
        for invite in invites:
            if invitation_is_available(invite,datetime.fromtimestamp(now,timezone.utc).isoformat()):
                code=invite['invitationCode']; key=self._code_id(code)
                s['codes'].setdefault(key,{'encryptedCode':self.cipher.encrypt(code.encode()).decode(),
                    'expiresAt':datetime.fromisoformat(invite['invitationExpiresAt'].replace('Z','+00:00')).timestamp(),
                    'status':'reserved','source':'migration','allocatedTo':invite.get('userId','')})

    def issue(self, *, allocated_to, source, operation, actor, expected_revision=None, allow_while_paused=False):
        key=self._operation(operation)
        with self.repo.transaction() as (s,now):
            old,fp=self._replay(s,key,[allocated_to,source,actor])
            if old:
                item=s['codes'][old['result']['id']]
                return {**old['result'],'invitationCode':self.cipher.decrypt(item['encryptedCode'].encode()).decode()}
            if expected_revision is not None and expected_revision!=s['policy']['revision']:
                raise RegistrationError('registrationPolicyConflict',409)
            # Only the authenticated management bridge may reserve codes while paused.
            if not allow_while_paused or s['policy']['cap'] is None:
                self._open(s)
            if self._stats(s,now)['available']<1: raise RegistrationError('registrationCapacityReached',409)
            if allocated_to and not s['seats'].get(allocated_to): raise RegistrationError('registrationRecipientUnknown')
            code=secrets.token_hex(16).upper(); cid=self._code_id(code)
            s['codes'][cid]={'encryptedCode':self.cipher.encrypt(code.encode()).decode(), 'status':'reserved',
                'expiresAt':now+7*86400,'allocatedTo':allocated_to,'source':str(source)[:120]}
            result={'id':cid,'expiresAt':now+7*86400}
            s['operations'][key]={'fingerprint':fp,'result':result}
            s['events'][key]={'type':'issued','actor':actor,'at':now,'invitation':cid}
            return {**result,'invitationCode':code}

    def revoke(self, *, invitation_id, operation, actor, expected_status=None):
        key=self._operation(operation)
        with self.repo.transaction() as (s,now):
            old,fp=self._replay(s,key,[invitation_id,actor])
            if old: return old['result']
            self._expire(s,now); item=s['codes'].get(invitation_id)
            if not item: raise RegistrationError('registrationInviteUnavailable',404)
            if item['status']=='redeemed': raise RegistrationError('registrationAlreadyRedeemed',409)
            if expected_status is not None and item['status']!=expected_status:
                raise RegistrationError('registrationPreviewChanged',409)
            if item['status']=='reserved': item['status']='revoked'
            result={'status':item['status']}
            s['operations'][key]={'fingerprint':fp,'result':result}
            s['events'][key]={'type':'revoked','actor':actor,'at':now,'invitation':invitation_id}
            return result

    def classify_legacy(self, *, classifications, revision, operation, actor, reason):
        """SSH deployment only. Keep every old seat; never expose via HTTP."""
        if not isinstance(classifications,dict) or not classifications or not reason or any(v not in {'real','test','legacyReview'} for v in classifications.values()):
            raise RegistrationError('registrationClassificationInvalid')
        key=self._operation(operation)
        with self.repo.transaction() as (s,now):
            old,fp=self._replay(s,key,[classifications,revision,actor,reason])
            if old:return old['result']
            if not s['policy']['paused']:raise RegistrationError('registrationClassificationRequiresPause',409)
            if revision!=s['policy']['revision']:raise RegistrationError('registrationPolicyConflict',409)
            if set(classifications)!=set(s['seats']) or any(x.get('source') not in {'migration','migration0131'} or x.get('classification') is not None for x in s['seats'].values()):
                raise RegistrationError('registrationClassificationManifestMismatch',409)
            before=self._stats(s,now)
            for subject,kind in classifications.items():s['seats'][subject]['classification']=kind
            s['policy']['revision']+=1
            result=self._stats(s,now)
            if result['cap'] is not None and result['registered']+result['reserved']>result['cap']:
                raise RegistrationError('registrationCapBelowOccupancy',409)
            s['operations'][key]={'fingerprint':fp,'result':result}
            s['events'][key]={'type':'legacyClassification','actor':actor,'reason':reason,'at':now,
                'manifestSHA256':hashlib.sha256(json.dumps(classifications,sort_keys=True).encode()).hexdigest(),
                'beforeRegistered':before['registered'],'afterRegistered':result['registered'],
                'testSubjects':result['testSubjects'],'legacyReviewSubjects':result['legacyReviewSubjects']}
            return result

    def claim(self, owner):
        with self.repo.transaction() as (s,now):
            self._open(s); self._expire(s,now)
            for item in s['codes'].values():
                if item['status']=='reserved' and item.get('allocatedTo')==owner:
                    code=self.cipher.decrypt(item['encryptedCode'].encode()).decode()
                    existing=self.store.get_family_invitation(code)
                    if existing: return existing
                    return self.store.add_family_member(owner,{'name':'受邀家人','relation':'家人',
                        'invitationKind':'accountRegistration','invitationCode':code,
                        'invitationExpiresAt':datetime.fromtimestamp(item['expiresAt'],timezone.utc).isoformat(),
                        'invitationStatus':'pending','accessStatus':'pending'})
            raise RegistrationError('registrationAllocationRequired',409)

    def start(self, *, invitation_code, phone, consent, operation, ip):
        if consent is not True: raise RegistrationError('legalConsentRequired')
        target=phone_target(phone); ref=self.identity.target_reference(identity_type='phone',target=target)
        cid=self._code_id(invitation_code); aid=self._operation(operation)
        if not (self.adapter.production_ready or (self.adapter.internal_verification_enabled and self.identity.environment in {'test','testing','local','development'})):
            raise RegistrationError('registrationSmsUnavailable',503)
        code=self.adapter.verification_code()
        with self.repo.transaction() as (s,now):
            old=s['attempts'].get(aid)
            fp=self.digest(json.dumps([cid,ref,consent],sort_keys=True))
            if old:
                if old['fingerprint']!=fp: raise RegistrationError('registrationIdempotencyConflict',409)
                return self._attempt_result(aid,old)
            self._open(s); self._invite(s,cid,now)
            iphash=self.digest('ip:'+ip)
            recent=[a for a in s['attempts'].values() if a['createdAt']>now-86400]
            for field,value in [('targetHash',ref['targetHash']),('invitation',cid),('ipHash',iphash)]:
                group=[a for a in recent if a.get(field)==value]
                hour=sum(a['createdAt']>now-3600 for a in group)
                if len(group)>= (30 if field=='ipHash' else 10) or hour >= (15 if field=='ipHash' else 5):
                    raise RegistrationError('registrationSmsRateLimited',429)
                if field!='ipHash' and any(a['createdAt']>now-60 for a in group):
                    raise RegistrationError('registrationSmsCooldown',429)
            if s['policy']['smsUsed']>=s['policy']['smsBudget']: raise RegistrationError('registrationSmsBudgetExhausted',429)
            # Commit before external I/O: crash/timeout means unknown, never automatic resend.
            s['policy']['smsUsed']+=1
            s['events']['sms-reserve:'+aid]={'type':'smsReserved','at':now}
            for a in s['attempts'].values():
                if a.get('targetHash')==ref['targetHash'] and a.get('status') not in {'registered','existingAccount'}:
                    a['status']='superseded'
            a={'fingerprint':fp,'invitation':cid,**ref,'ipHash':iphash,'createdAt':now,'expiresAt':now+300,
               'status':'unknown','errors':0,'codeHash':self.digest('otp:'+aid+':'+code),'providerMode':self.adapter.provider_mode}
            s['attempts'][aid]=a
        try:
            receipt=self.adapter.deliver(identity_type='phone',target=target,purpose='register',challenge_id=aid,verification_code=code)
            delivery=receipt.delivery_state
        except Exception:
            # Intentionally no retry and no provider exception body (could contain secrets).
            delivery='unknown'
        with self.repo.transaction() as (s,now):
            a=s['attempts'][aid]
            if a['status']=='unknown': a['status']=delivery
            return self._attempt_result(aid,a)

    def _attempt_result(self,aid,a):
        return {'attemptId':aid,'attemptToken':self.digest('attempt:'+aid),
            'status':a['status'],'expiresAt':a['expiresAt'],'retryAfterSeconds':60,'contractVersion':1}

    def _attempt(self,s,aid,token):
        if not hmac.compare_digest(self.digest('attempt:'+aid),str(token)) or aid not in s['attempts']:
            raise RegistrationError('registrationAttemptUnavailable',404)
        return s['attempts'][aid]

    def verify(self, *, attempt_id, token, code):
        # Return error after leaving transaction so wrong-code counts are durable.
        error=None; result=None
        with self.repo.transaction() as (s,now):
            a=self._attempt(s,attempt_id,token)
            if a['status']=='verified' and a['grantExpiresAt']>now:
                return {'grant':self.digest('grant:'+attempt_id),'status':'verified'}
            if a['status'] not in {'accepted','delivered','unknown'} or a['expiresAt']<=now or a['errors']>=5:
                raise RegistrationError('registrationCodeUnavailable',409)
            if not hmac.compare_digest(a['codeHash'],self.digest('otp:'+attempt_id+':'+str(code))):
                a['errors']+=1
                if a['errors']>=5: a['status']='locked'
                error=RegistrationError('registrationCodeInvalid')
            else:
                self._open(s); self._invite(s,a['invitation'],now)
                a.update(status='verified',verifiedAt=now,grantExpiresAt=now+300)
                result={'grant':self.digest('grant:'+attempt_id),'status':'verified'}
        if error: raise error
        return result

    def result(self, *, attempt_id, token):
        with self.repo.transaction() as (s,now):
            a=self._attempt(s,attempt_id,token)
            return a.get('result',{'status':a['status'],'contractVersion':1})

    def complete(self, *, attempt_id, token, grant, password, consent, operation):
        if consent is not True: raise RegistrationError('legalConsentRequired')
        if not 8<=len(password)<=128: raise RegistrationError('registrationPasswordInvalid')
        key=self._operation(operation)
        if not re.fullmatch(r'[0-9a-f]{64}', str(attempt_id)) or not hmac.compare_digest(self.digest('attempt:'+attempt_id),str(token)):
            raise RegistrationError('registrationAttemptUnavailable',404)
        # Costly hash outside global admission lock; final guards below remain atomic.
        credential=make_password_credential(password)
        with self.repo.transaction() as (s,now):
            a=self._attempt(s,attempt_id,token)
            old,fp=self._replay(s,key,[attempt_id,self.digest(password),consent])
            if old: return old['result']
            if not hmac.compare_digest(self.digest('grant:'+attempt_id),str(grant)) or a['status']!='verified' or a['grantExpiresAt']<=now:
                raise RegistrationError('registrationGrantUnavailable',409)
            self._open(s); item=self._invite(s,a['invitation'],now)
            ref={k:a[k] for k in ('identityType','targetHashKeyVersion','targetHash')}
            existing=self.store.get_identity_binding_by_target(identity_type='phone',target_hash_key_version=ref['targetHashKeyVersion'],target_hash=ref['targetHash'])
            if existing:
                result={'status':'existingAccount','contractVersion':1}
            else:
                subject='sub_'+secrets.token_urlsafe(24)
                proof={'id':'idp_'+secrets.token_urlsafe(24),'attempt':attempt_id,**a}
                self.repo.create_account(s,reference=ref,subject=subject,binding='idb_'+secrets.token_urlsafe(24),
                    credential=credential,nickname='寻梦环游用户',now=now,proof=proof)
                item.update(status='redeemed',subjectId=subject,redeemedAt=now)
                self.repo.mark_family_registered(self.cipher.decrypt(item['encryptedCode'].encode()).decode(),subject,now)
                result={'status':'registered','contractVersion':1,'user':{'id':subject,'nickname':'寻梦环游用户'}}
            a['status']=result['status']; a['result']=result
            s['operations'][key]={'fingerprint':fp,'result':result}
            s['events'][key]={'type':result['status'],'at':now,'invitation':a['invitation']}
            return result

    def register_username(self, *, code, username, password, nickname=''):
        if not 8<=len(password)<=128: raise RegistrationError('registrationPasswordInvalid')
        ref=self.identity.target_reference(identity_type='username',target=username)
        credential=make_password_credential(password)
        with self.repo.transaction() as (s,now):
            self._open(s); item=self._invite(s,self._code_id(code),now)
            if self.store.get_identity_binding_by_target(identity_type='username',target_hash_key_version=ref['targetHashKeyVersion'],target_hash=ref['targetHash']):
                raise RegistrationError('registrationUnavailable',409)
            subject='sub_'+secrets.token_urlsafe(24)
            self.repo.create_account(s,reference=ref,subject=subject,binding='idb_'+secrets.token_urlsafe(24),
                credential=credential,nickname=str(nickname or username)[:80],now=now,proof=None)
            item.update(status='redeemed',subjectId=subject,redeemedAt=now)
            self.repo.mark_family_registered(code.strip().upper(),subject,now)
            return {'status':'registered','contractVersion':2,'user':{'id':subject,'nickname':nickname or username}}
