"""Restricted service-to-service registration commands; never an App credential."""
import hashlib, hmac, json, re, time
from fastapi import HTTPException, Request
from .phone_registration import RegistrationError
from .registration_routes import service

PATH='/internal/registration-admin/v1/command'
SCOPE='registration:manage'


def install_bridge(app, context):
    @app.post(PATH)
    async def command(request: Request):
        config=context()['settings']; key=config.registration_management_service_key or ''
        if len(key.encode())<32:
            raise HTTPException(503,detail={'code':'registrationManagementUnavailable'})
        raw=await request.body()
        if len(raw)>8192:raise HTTPException(413,detail={'code':'registrationManagementInvalid'})
        expected=hmac.new(key.encode(),b'POST\n'+PATH.encode()+b'\n'+raw,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,request.headers.get('x-registration-signature','')):
            raise HTTPException(403,detail={'code':'registrationManagementDenied'})
        try:
            body=json.loads(raw); actor=body['actor']; action=body['action']; data=body.get('data',{})
            if (type(body['timestamp']) is not int or abs(time.time()-body['timestamp'])>60
                or body['scope']!=SCOPE or not isinstance(actor,str)
                or not (1<=len(actor)<=160) or not actor.isprintable()
                or not re.fullmatch(r'[a-f0-9]{32}',body['requestId'])
                or action not in {'state','preview','execute','result'} or not isinstance(data,dict)):
                raise ValueError()
        except (ValueError,KeyError,TypeError):
            raise HTTPException(403,detail={'code':'registrationManagementDenied'}) from None
        # Run DB work on the sync thread pool, not the ASGI event loop.
        from starlette.concurrency import run_in_threadpool
        try:return await run_in_threadpool(dispatch,context(),actor,action,data)
        except RegistrationError as e:raise HTTPException(e.status,detail={'code':e.code}) from None


def dispatch(ctx,actor,action,data):
    p=service(ctx['store'],ctx['identity'](),ctx['settings'])
    if action=='state':return p.snapshot()
    cid=data.get('commandId','')
    if not isinstance(cid,str) or not re.fullmatch(r'[a-f0-9]{32}',cid):raise RegistrationError('registrationOperationInvalid')
    preview_key='admin-preview:'+cid
    if action=='preview':
        kind=data.get('kind'); values=data.get('values')
        if kind not in {'policy','issue','revoke'} or not isinstance(values,dict):raise RegistrationError('registrationOperationInvalid')
        snapshot=p.snapshot()
        if kind=='policy':
            if set(values)!={'cap','smsBudget','paused','revision'} or type(values['cap']) is not int or values['cap']<0 or type(values['smsBudget']) is not int or values['smsBudget']<0 or type(values['paused']) is not bool:
                raise RegistrationError('registrationPolicyInvalid')
            if values['revision']!=snapshot['revision']:raise RegistrationError('registrationPolicyConflict',409)
            if values['cap']<snapshot['registered']+snapshot['reserved']:raise RegistrationError('registrationCapBelowOccupancy',409)
            if values['smsBudget']<snapshot['smsUsed']:raise RegistrationError('registrationBudgetBelowUsed',409)
            after={k:values[k] for k in ('cap','smsBudget','paused')}
        elif kind=='issue':
            if set(values)!={'allocatedTo','source','revision'} or not isinstance(values['allocatedTo'],str) or len(values['allocatedTo'])>160 or not isinstance(values['source'],str) or len(values['source'])>120:
                raise RegistrationError('registrationOperationInvalid')
            if values['revision']!=snapshot['revision']:raise RegistrationError('registrationPolicyConflict',409)
            if snapshot['cap'] is None:raise RegistrationError('registrationPaused',409)
            if snapshot['available']<1:raise RegistrationError('registrationCapacityReached',409)
            with p.repo.transaction() as (state,now):
                if values['allocatedTo'] and values['allocatedTo'] not in state['seats']:raise RegistrationError('registrationRecipientUnknown')
            after={'reserveAdditional':1,'allocatedTo':values['allocatedTo'],'source':values['source'],'validDays':7}
        else:
            if set(values)!={'invitationId'}:raise RegistrationError('registrationOperationInvalid')
            item=next((i for i in snapshot['invitations'] if i['id']==values['invitationId']),None)
            if not item or item['status']!='reserved':raise RegistrationError('registrationPreviewChanged',409)
            after={'releaseReservation':1,'invitationId':values['invitationId']}
        fingerprint=p.digest(json.dumps([actor,kind,values],sort_keys=True))
        with p.repo.transaction() as (state,now):
            old=state['operations'].get(preview_key)
            if old:
                if old['fingerprint']!=fingerprint:raise RegistrationError('registrationIdempotencyConflict',409)
                return old['preview']
            preview={'commandId':cid,'hash':fingerprint,'kind':kind,'before':{k:snapshot[k] for k in ('cap','registered','reserved','available','revision','smsBudget','smsUsed','paused')},'after':after,'expiresAt':now+300}
            state['operations'][preview_key]={'actor':actor,'kind':kind,'values':values,'fingerprint':fingerprint,'preview':preview}
            state['events'][preview_key]={'type':'managementPreview','actor':actor,'at':now,'commandId':cid}
            return preview
    with p.repo.transaction() as (state,now):
        saved=state['operations'].get(preview_key)
        if not saved or saved['actor']!=actor:raise RegistrationError('registrationCommandUnavailable',404)
        applied=state['operations'].get(p._operation(cid))
        if action=='result' and not applied:return {'status':'notApplied'}
        if action=='execute' and data.get('hash')!=saved['fingerprint']:raise RegistrationError('registrationPreviewChanged',409)
        if not applied and saved['preview']['expiresAt']<=now:raise RegistrationError('registrationPreviewExpired',409)
        kind=saved['kind'];values=dict(saved['values'])
    if kind=='policy':result=p.configure(cap=values['cap'],paused=values['paused'],sms_budget=values['smsBudget'],revision=values['revision'],operation=cid,actor=actor)
    elif kind=='issue':result=p.issue(allocated_to=values['allocatedTo'],source=values['source'],operation=cid,actor=actor,expected_revision=values['revision'],allow_while_paused=True)
    else:result=p.revoke(invitation_id=values['invitationId'],operation=cid,actor=actor,expected_status='reserved')
    return {'status':'succeeded','commandId':cid,'kind':kind,'result':result}
