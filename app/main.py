import asyncio
from datetime import datetime, timezone
from fastapi import FastAPI, Depends, Request, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db, init_db, SessionLocal
from .models import RailwayAccount, User, Slot, TunnelSession, AuditLog
from .security import encrypt_secret, mask_token, make_user_token, token_hash, token_prefix, is_admin_token
from .services.sessions import find_user_by_token, allocate_slot, heartbeat as hb, disconnect as disc, cleanup_stale_sessions
from .services.railway import create_slot, refresh_slot_tcp
from .services.audit import log

settings = get_settings()
app = FastAPI(title='NekoTunnel Central', version='0.1.0')
app.mount('/static', StaticFiles(directory='app/static'), name='static')
templates = Jinja2Templates(directory='app/templates')


class ConnectRequest(BaseModel):
    token: str
    local_port: int | None = None


class SessionRequest(BaseModel):
    token: str
    session_id: str


def admin_required(request: Request):
    cookie = request.cookies.get('admin_token')
    header = request.headers.get('x-admin-token')
    if not (is_admin_token(cookie) or is_admin_token(header)):
        raise HTTPException(status_code=401, detail='admin auth required')


def admin_redirect_required(request: Request):
    cookie = request.cookies.get('admin_token')
    if not is_admin_token(cookie):
        return False
    return True


@app.on_event('startup')
async def startup():
    init_db()
    asyncio.create_task(cleanup_loop())


async def cleanup_loop():
    while True:
        await asyncio.sleep(settings.cleanup_interval_seconds)
        try:
            db = SessionLocal()
            cleanup_stale_sessions(db)
            db.close()
        except Exception:
            pass


@app.get('/health')
def health(db: Session = Depends(get_db)):
    db.scalar(select(func.count()).select_from(User))
    return {'ok': True, 'time': datetime.now(timezone.utc).isoformat()}


@app.get('/', response_class=HTMLResponse)
def root(request: Request):
    if admin_redirect_required(request):
        return RedirectResponse('/admin', status_code=302)
    return templates.TemplateResponse('login.html', {'request': request, 'error': None})


@app.post('/admin/login')
def admin_login(request: Request, admin_token: str = Form(...)):
    if not is_admin_token(admin_token):
        return templates.TemplateResponse('login.html', {'request': request, 'error': 'Wrong admin token'}, status_code=401)
    resp = RedirectResponse('/admin', status_code=302)
    resp.set_cookie('admin_token', admin_token, httponly=True, samesite='lax', secure=True, max_age=60 * 60 * 24 * 7)
    return resp


@app.post('/admin/logout')
def admin_logout():
    resp = RedirectResponse('/', status_code=302)
    resp.delete_cookie('admin_token')
    return resp


@app.get('/admin', response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    admin_required(request)
    counts = {
        'accounts': db.scalar(select(func.count()).select_from(RailwayAccount)) or 0,
        'users': db.scalar(select(func.count()).select_from(User)) or 0,
        'free_slots': db.scalar(select(func.count()).select_from(Slot).where(Slot.status == 'free')) or 0,
        'busy_slots': db.scalar(select(func.count()).select_from(Slot).where(Slot.status == 'busy')) or 0,
        'pending_slots': db.scalar(select(func.count()).select_from(Slot).where(Slot.status == 'tcp_pending')) or 0,
        'active_sessions': db.scalar(select(func.count()).select_from(TunnelSession).where(TunnelSession.status == 'active')) or 0,
    }
    logs = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(20)).all()
    return templates.TemplateResponse('dashboard.html', {'request': request, 'counts': counts, 'logs': logs})


@app.get('/admin/accounts', response_class=HTMLResponse)
def accounts_page(request: Request, db: Session = Depends(get_db)):
    admin_required(request)
    accounts = db.scalars(select(RailwayAccount).order_by(RailwayAccount.created_at.desc())).all()
    return templates.TemplateResponse('accounts.html', {'request': request, 'accounts': accounts})


@app.post('/admin/accounts')
def add_account(request: Request, label: str = Form(...), workspace: str = Form(...), api_token: str = Form(...), db: Session = Depends(get_db)):
    admin_required(request)
    account = RailwayAccount(label=label.strip(), workspace=workspace.strip(), api_token_cipher=encrypt_secret(api_token.strip()), status='active')
    db.add(account)
    log(db, 'railway_account_added', 'admin', f'label={label} workspace={workspace}')
    db.commit()
    return RedirectResponse('/admin/accounts', status_code=302)


@app.post('/admin/accounts/{account_id}/disable')
def disable_account(account_id: str, request: Request, db: Session = Depends(get_db)):
    admin_required(request)
    account = db.get(RailwayAccount, account_id)
    if account:
        account.status = 'disabled'
        db.add(account)
        log(db, 'railway_account_disabled', 'admin', account.label)
        db.commit()
    return RedirectResponse('/admin/accounts', status_code=302)


@app.get('/admin/users', response_class=HTMLResponse)
def users_page(request: Request, db: Session = Depends(get_db)):
    admin_required(request)
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return templates.TemplateResponse('users.html', {'request': request, 'users': users, 'new_token': None})


@app.post('/admin/users')
def create_user(request: Request, name: str = Form(...), max_sessions: int = Form(1), db: Session = Depends(get_db)):
    admin_required(request)
    token = make_user_token()
    user = User(name=name.strip(), token_prefix=token_prefix(token), token_hash=token_hash(token), max_sessions=max_sessions, status='active')
    db.add(user)
    log(db, 'user_token_created', 'admin', f'user={name}')
    db.commit()
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return templates.TemplateResponse('users.html', {'request': request, 'users': users, 'new_token': token})


@app.post('/admin/users/{user_id}/disable')
def disable_user(user_id: str, request: Request, db: Session = Depends(get_db)):
    admin_required(request)
    user = db.get(User, user_id)
    if user:
        user.status = 'disabled'
        db.add(user)
        log(db, 'user_disabled', 'admin', user.name)
        db.commit()
    return RedirectResponse('/admin/users', status_code=302)


@app.get('/admin/slots', response_class=HTMLResponse)
def slots_page(request: Request, db: Session = Depends(get_db)):
    admin_required(request)
    accounts = db.scalars(select(RailwayAccount).where(RailwayAccount.status == 'active')).all()
    slots = db.scalars(select(Slot).order_by(Slot.created_at.desc())).all()
    return templates.TemplateResponse('slots.html', {'request': request, 'accounts': accounts, 'slots': slots})


@app.post('/admin/slots/manual')
def add_manual_slot(request: Request, project_name: str = Form(...), server_addr: str = Form(...), server_port: int = Form(...), frp_token: str = Form(...), service_name: str = Form('final'), db: Session = Depends(get_db)):
    admin_required(request)
    slot = Slot(project_name=project_name, service_name=service_name, server_addr=server_addr, server_port=server_port, frp_token_cipher=encrypt_secret(frp_token), status='free')
    db.add(slot)
    log(db, 'manual_slot_added', 'admin', f'{server_addr}:{server_port}')
    db.commit()
    return RedirectResponse('/admin/slots', status_code=302)


@app.post('/admin/slots/create')
def create_railway_slot(background: BackgroundTasks, request: Request, account_id: str = Form(...), count: int = Form(1), db: Session = Depends(get_db)):
    admin_required(request)
    account = db.get(RailwayAccount, account_id)
    if not account or account.status != 'active':
        raise HTTPException(400, 'Invalid account')
    count = max(1, min(count, 5))

    def job(account_id: str, count: int):
        db2 = SessionLocal()
        try:
            acct = db2.get(RailwayAccount, account_id)
            for _ in range(count):
                create_slot(db2, acct)
        finally:
            db2.close()

    background.add_task(job, account_id, count)
    return RedirectResponse('/admin/slots', status_code=302)


@app.post('/admin/slots/{slot_id}/refresh')
def refresh_slot(slot_id: str, request: Request, db: Session = Depends(get_db)):
    admin_required(request)
    slot = db.get(Slot, slot_id)
    if slot:
        refresh_slot_tcp(db, slot)
    return RedirectResponse('/admin/slots', status_code=302)


@app.post('/admin/slots/{slot_id}/free')
def force_free(slot_id: str, request: Request, db: Session = Depends(get_db)):
    admin_required(request)
    slot = db.get(Slot, slot_id)
    if slot:
        slot.status = 'free' if slot.server_addr and slot.server_port else 'tcp_pending'
        slot.current_session_id = None
        db.add(slot)
        log(db, 'slot_force_free', 'admin', slot.project_name)
        db.commit()
    return RedirectResponse('/admin/slots', status_code=302)


@app.get('/admin/sessions', response_class=HTMLResponse)
def sessions_page(request: Request, db: Session = Depends(get_db)):
    admin_required(request)
    sessions = db.scalars(select(TunnelSession).order_by(TunnelSession.started_at.desc()).limit(100)).all()
    return templates.TemplateResponse('sessions.html', {'request': request, 'sessions': sessions})


@app.post('/api/connect')
def api_connect(req: ConnectRequest, db: Session = Depends(get_db)):
    user = find_user_by_token(db, req.token)
    if not user:
        raise HTTPException(401, 'invalid_token')
    try:
        data = allocate_slot(db, user, req.local_port)
        return data
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.post('/api/heartbeat')
def api_heartbeat(req: SessionRequest, db: Session = Depends(get_db)):
    user = find_user_by_token(db, req.token)
    if not user:
        raise HTTPException(401, 'invalid_token')
    ok = hb(db, user, req.session_id)
    if not ok:
        raise HTTPException(404, 'session_not_found')
    return {'ok': True}


@app.post('/api/disconnect')
def api_disconnect(req: SessionRequest, db: Session = Depends(get_db)):
    user = find_user_by_token(db, req.token)
    if not user:
        raise HTTPException(401, 'invalid_token')
    ok = disc(db, user, req.session_id)
    if not ok:
        raise HTTPException(404, 'session_not_found')
    return {'ok': True}


@app.get('/client/nekotunnel', response_class=PlainTextResponse)
def get_client_script():
    with open('client/nekotunnel', 'r', encoding='utf-8') as f:
        return PlainTextResponse(f.read())
