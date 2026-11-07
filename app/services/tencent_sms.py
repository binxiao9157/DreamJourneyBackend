"""Tencent SendSms 2021-01-11; server-side TC3 signing, no implicit retries."""
import hashlib
import hmac
import json
import re
import time
from contextlib import nullcontext
from datetime import datetime, timezone
import httpx
from app.services.identity_bindings import (IdentityChallengeAdapter,
    IdentityChallengeConfigurationError, IdentityChallengeDeliveryReceipt)


class TencentSMSAdapter(IdentityChallengeAdapter):
    provider_mode = 'tencentSms'
    production_ready = True
    server_code_verification_enabled = True

    def __init__(self, *, secret_id, secret_key, region, app_id, sign_name, template_id,
                 timeout=10.0, transport=None, parameter_count=1):
        if not all((secret_id,secret_key,region,app_id,sign_name,template_id)) or parameter_count not in (1,2):
            raise IdentityChallengeConfigurationError('Tencent SMS configuration incomplete')
        self.secret_id,self.secret_key,self.region=secret_id,secret_key,region
        self.app_id,self.sign_name,self.template_id=app_id,sign_name,template_id
        self.timeout,self.transport,self.parameter_count=timeout,transport,parameter_count

    def deliver(self, *, identity_type, target, purpose, challenge_id, verification_code):
        # Identity bindings use country-code digits without '+' as their stable
        # hash input. Convert only the provider wire value, never the identity.
        if identity_type == 'phone' and re.fullmatch(r'[1-9][0-9]{6,14}', target):
            target = '+' + target
        if identity_type!='phone' or not target.startswith('+'):
            raise IdentityChallengeConfigurationError('Tencent SMS requires E164 phone')
        params=[verification_code] + (['5'] if self.parameter_count==2 else [])
        payload={'PhoneNumberSet':[target], 'SmsSdkAppId':self.app_id,'SignName':self.sign_name,
                 'TemplateId':self.template_id,'TemplateParamSet':params}
        body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        timestamp=int(time.time()); date=datetime.fromtimestamp(timestamp,timezone.utc).strftime('%Y-%m-%d')
        canonical='POST\n/\n\ncontent-type:application/json; charset=utf-8\nhost:sms.tencentcloudapi.com\n\ncontent-type;host\n'+hashlib.sha256(body).hexdigest()
        scope=date+'/sms/tc3_request'
        signing='TC3-HMAC-SHA256\n'+str(timestamp)+'\n'+scope+'\n'+hashlib.sha256(canonical.encode()).hexdigest()
        datekey=hmac.digest(('TC3'+self.secret_key).encode(),date.encode(),'sha256')
        servicekey=hmac.digest(datekey,b'sms','sha256')
        key=hmac.digest(servicekey,b'tc3_request','sha256')
        signature=hmac.new(key,signing.encode(),hashlib.sha256).hexdigest()
        headers={'Content-Type':'application/json; charset=utf-8','Host':'sms.tencentcloudapi.com',
            'X-TC-Action':'SendSms','X-TC-Version':'2021-01-11','X-TC-Region':self.region,
            'X-TC-Timestamp':str(timestamp),
            'Authorization':f'TC3-HMAC-SHA256 Credential={self.secret_id}/{scope}, SignedHeaders=content-type;host, Signature={signature}'}
        try:
            with httpx.Client(transport=self.transport,timeout=self.timeout,follow_redirects=False) as client:
                response=client.post('https://sms.tencentcloudapi.com/',content=body,headers=headers)
            if response.status_code!=200:
                return IdentityChallengeDeliveryReceipt('unknown','unsupported')
            data=response.json().get('Response',{})
            if data.get('Error'):
                return IdentityChallengeDeliveryReceipt('undeliverable','terminal')
            statuses=data.get('SendStatusSet',[])
            if len(statuses)!=1 or statuses[0].get('PhoneNumber')!=target:
                return IdentityChallengeDeliveryReceipt('unknown','unsupported')
            if statuses[0].get('Code')!='Ok':
                return IdentityChallengeDeliveryReceipt('undeliverable','terminal')
            return IdentityChallengeDeliveryReceipt('accepted','unsupported',statuses[0].get('SerialNo'))
        except (httpx.HTTPError,ValueError,TypeError,AttributeError):
            return IdentityChallengeDeliveryReceipt('unknown','unsupported')


class BudgetedTencentSMSAdapter(IdentityChallengeAdapter):
    """The same durable budget covers both registration and existing-account SMS.

    Each provider call is fenced BEFORE I/O; an interrupted/unknown call is never
    replayed automatically. A deliberately new challenge can reserve a new send.
    """
    provider_mode = 'tencentSms'
    production_ready = True
    server_code_verification_enabled = True

    def __init__(self, adapter, store):
        from app.services.registration_repository import RegistrationRepository
        self.adapter, self.repo = adapter, RegistrationRepository(store)

    def deliver(self, **kwargs):
        # Existing login/reset may already hold an HTTP transaction. The send
        # budget/fence is an external-effect receipt and must commit independently.
        detach=getattr(self.repo.store,'detached_background_context',None)
        with detach() if callable(detach) else nullcontext():
            reserved=self._reserve(kwargs['challenge_id'])
        if not reserved:
            return IdentityChallengeDeliveryReceipt('unknown','unsupported')
        if reserved=='exhausted':
            return IdentityChallengeDeliveryReceipt('undeliverable','terminal')
        return self.adapter.deliver(**kwargs)

    def _reserve(self,cid):
        with self.repo.transaction() as (state,now):
            key='sms-dispatch:'+cid
            if key in state['events']:
                return False
            reserve='sms-reserve:'+cid
            if reserve not in state['events']:
                if state['policy']['smsUsed']>=state['policy']['smsBudget']:
                    return 'exhausted'
                state['policy']['smsUsed']+=1
                state['events'][reserve]={'type':'smsReserved','at':now}
            state['events'][key]={'type':'smsDispatchStarted','at':now}
        return True
