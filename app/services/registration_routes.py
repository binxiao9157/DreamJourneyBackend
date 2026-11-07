"""Dedicated registration surface; no changes to session v2 or core Echo handlers."""
import secrets
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from app.services.phone_registration import PhoneRegistrationService, RegistrationError
from app.services.test_account_admin_auth import verify_test_account_admin_password

PREFIX='/v2/auth/phone-registration'
ADMIN='/ops/registration'
COOKIE='dj_registration_admin'


def service(store,identity,settings):
    if not settings.registration_admission_enabled:
        raise RegistrationError('registrationUnavailable',503)
    return PhoneRegistrationService(store,identity,secret=settings.registration_secret or '')


def install_routes(app, context):
    router=APIRouter()

    def svc():
        c=context()
        return service(c['store'],c['identity'](),c['settings'])

    def run(callback):
        try: return callback()
        except RegistrationError as e:
            raise HTTPException(e.status,detail={'code':e.code,'contractVersion':1}) from None

    def local_prototype():
        config=context()['settings']
        if not config.registration_local_prototype_enabled or config.environment not in {'test','testing','local'}:
            raise HTTPException(404, detail='Not found')

    def origin(request):
        expected=context()['settings'].registration_admin_origin
        if not expected or request.headers.get('origin')!=expected:
            raise RegistrationError('registrationAdminOriginRejected',403)

    def authorize(request, s, write=False):
        if write: origin(request)
        token=request.cookies.get(COOKIE,'')
        with s.repo.transaction() as (state,now):
            session=state['adminSessions'].get(s.digest('admin:'+token))
            if not session or session['expiresAt']<=now:
                raise RegistrationError('registrationAdminRequired',401)
            if write and not secrets.compare_digest(session['csrf'],request.headers.get('x-csrf-token','')):
                raise RegistrationError('registrationAdminCSRFRejected',403)
            return dict(session)

    @router.post(PREFIX+'/challenges')
    def start(request:Request,payload:dict):
        return run(lambda:svc().start(invitation_code=payload.get('invitationCode'),phone=payload.get('phone'),
            consent=payload.get('legalConsent'),operation=payload.get('operationId'),ip=request.client.host if request.client else 'unknown'))

    @router.post(PREFIX+'/verify')
    def verify(payload:dict):
        return run(lambda:svc().verify(attempt_id=str(payload.get('attemptId','')),token=str(payload.get('attemptToken','')),code=str(payload.get('code',''))))

    @router.post(PREFIX+'/complete')
    def complete(payload:dict):
        return run(lambda:svc().complete(attempt_id=str(payload.get('attemptId','')),token=str(payload.get('attemptToken','')),
            grant=str(payload.get('grant','')),password=str(payload.get('password','')),consent=payload.get('legalConsent'),operation=payload.get('operationId')))

    @router.post(PREFIX+'/result')
    def result(payload:dict):
        return run(lambda:svc().result(attempt_id=str(payload.get('attemptId','')),token=str(payload.get('attemptToken',''))))

    @router.get(ADMIN, response_class=HTMLResponse, dependencies=[Depends(local_prototype)])
    def page():
        response=HTMLResponse(Path(__file__).resolve().parents[1].joinpath('web/registration_admin.html').read_text())
        response.headers.update({'Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",'X-Content-Type-Options':'nosniff'})
        return response

    @router.post(ADMIN+'/login', dependencies=[Depends(local_prototype)])
    def login(request:Request,payload:dict):
        def action():
            origin(request); s=svc(); config=context()['settings']
            if not config.registration_admin_password_hash: raise RegistrationError('registrationAdminUnavailable',503)
            ip=s.digest('admin-ip:'+(request.client.host if request.client else 'unknown'))
            # Reserve an attempt before expensive password verification; cross-process durable.
            with s.repo.transaction() as (state,now):
                attempts=state['adminAttempts'].setdefault(ip,{'times':[]})
                attempts['times']=[t for t in attempts['times'] if t>now-900]
                if len(attempts['times'])>=5: raise RegistrationError('registrationAdminRateLimited',429)
                attempts['times'].append(now)
            if not verify_test_account_admin_password(str(payload.get('password','')),config.registration_admin_password_hash):
                raise RegistrationError('registrationAdminInvalid',401)
            token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
            with s.repo.transaction() as (state,now):
                state['adminSessions'][s.digest('admin:'+token)]={'csrf':csrf,'expiresAt':now+1800,'actor':'registration-admin'}
            response=JSONResponse({'csrf':csrf,'expiresIn':1800})
            response.set_cookie(COOKIE,token,max_age=1800,httponly=True,secure=True,samesite='strict',path=ADMIN)
            response.headers['Cache-Control']='no-store'
            return response
        return run(action)

    @router.post(ADMIN+'/logout', dependencies=[Depends(local_prototype)])
    def logout(request:Request):
        def action():
            s=svc(); authorize(request,s,True)
            with s.repo.transaction() as (state,now):
                state['adminSessions'][s.digest('admin:'+request.cookies.get(COOKIE,''))]['expiresAt']=now
            response=JSONResponse({'status':'loggedOut'}); response.delete_cookie(COOKIE,path=ADMIN)
            return response
        return run(action)

    @router.get(ADMIN+'/state', dependencies=[Depends(local_prototype)])
    def state(request:Request):
        def action():
            s=svc(); a=authorize(request,s)
            return {**s.snapshot(),'csrf':a['csrf']}
        return run(action)

    @router.post(ADMIN+'/policy', dependencies=[Depends(local_prototype)])
    def policy(request:Request,payload:dict):
        def action():
            s=svc(); a=authorize(request,s,True)
            return s.configure(cap=payload.get('cap'),paused=payload.get('paused'),sms_budget=payload.get('smsBudget'),
                revision=payload.get('revision'),operation=payload.get('operationId'),actor=a['actor'])
        return run(action)

    @router.post(ADMIN+'/invitations', dependencies=[Depends(local_prototype)])
    def issue(request:Request,payload:dict):
        def action():
            s=svc(); a=authorize(request,s,True)
            return s.issue(allocated_to=str(payload.get('allocatedTo','')),source=str(payload.get('source','')),
                operation=payload.get('operationId'),actor=a['actor'])
        return run(action)

    @router.post(ADMIN+'/revoke', dependencies=[Depends(local_prototype)])
    def revoke(request:Request,payload:dict):
        def action():
            s=svc(); a=authorize(request,s,True)
            return s.revoke(invitation_id=str(payload.get('invitationId','')),operation=payload.get('operationId'),actor=a['actor'])
        return run(action)

    app.include_router(router)
