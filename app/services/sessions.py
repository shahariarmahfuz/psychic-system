from datetime import datetime, timezone, timedelta
from sqlalchemy import text, select, func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import User, Slot, TunnelSession
from ..security import decrypt_secret, token_hash, token_prefix
from .audit import log

settings = get_settings()


def utcnow():
    return datetime.now(timezone.utc)


def find_user_by_token(db: Session, token: str) -> User | None:
    prefix = token_prefix(token)
    thash = token_hash(token)
    return db.scalar(select(User).where(User.token_prefix == prefix, User.token_hash == thash, User.status == 'active'))


def active_session_count(db: Session, user_id: str) -> int:
    return db.scalar(select(func.count()).select_from(TunnelSession).where(TunnelSession.user_id == user_id, TunnelSession.status == 'active')) or 0


def allocate_slot(db: Session, user: User, local_port: int | None = None) -> dict:
    if active_session_count(db, user.id) >= user.max_sessions:
        raise RuntimeError('max_sessions_reached')

    # PostgreSQL transaction-safe slot locking.
    row = db.execute(text('''
        SELECT id FROM slots
        WHERE status = 'free'
          AND server_addr IS NOT NULL
          AND server_port IS NOT NULL
        ORDER BY COALESCE(last_used_at, created_at) ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    ''')).first()

    if not row:
        raise RuntimeError('no_free_slot')

    slot = db.get(Slot, row[0])
    if not slot:
        raise RuntimeError('slot_missing')

    session = TunnelSession(
        user_id=user.id,
        slot_id=slot.id,
        client_local_port=local_port,
        proxy_name='nekotunnel-' + slot.id[:8],
        status='active',
        last_heartbeat_at=utcnow(),
    )
    db.add(session)
    db.flush()

    slot.status = 'busy'
    slot.current_session_id = session.id
    slot.last_used_at = utcnow()
    db.add(slot)
    log(db, 'session_started', user.name, f'session={session.id} slot={slot.project_name}')
    db.commit()

    return {
        'session_id': session.id,
        'server_addr': slot.server_addr,
        'server_port': slot.server_port,
        'frp_token': decrypt_secret(slot.frp_token_cipher),
        'remote_port': slot.remote_port,
        'proxy_name': session.proxy_name,
    }


def heartbeat(db: Session, user: User, session_id: str) -> bool:
    sess = db.get(TunnelSession, session_id)
    if not sess or sess.user_id != user.id or sess.status != 'active':
        return False
    sess.last_heartbeat_at = utcnow()
    db.add(sess)
    db.commit()
    return True


def disconnect(db: Session, user: User, session_id: str) -> bool:
    sess = db.get(TunnelSession, session_id)
    if not sess or sess.user_id != user.id or sess.status not in ('active', 'expired'):
        return False
    sess.status = 'closed'
    sess.ended_at = utcnow()
    slot = db.get(Slot, sess.slot_id)
    if slot and slot.current_session_id == sess.id:
        slot.current_session_id = None
        slot.status = 'free' if slot.server_addr and slot.server_port else 'tcp_pending'
        db.add(slot)
    db.add(sess)
    log(db, 'session_closed', user.name, f'session={session_id}')
    db.commit()
    return True


def cleanup_stale_sessions(db: Session) -> int:
    cutoff = utcnow() - timedelta(seconds=settings.session_ttl_seconds)
    sessions = db.scalars(select(TunnelSession).where(TunnelSession.status == 'active', TunnelSession.last_heartbeat_at < cutoff)).all()
    count = 0
    for sess in sessions:
        sess.status = 'expired'
        sess.ended_at = utcnow()
        slot = db.get(Slot, sess.slot_id)
        if slot and slot.current_session_id == sess.id:
            slot.current_session_id = None
            slot.status = 'free' if slot.server_addr and slot.server_port else 'tcp_pending'
            db.add(slot)
        db.add(sess)
        count += 1
    if count:
        log(db, 'stale_sessions_cleaned', 'system', f'count={count}')
        db.commit()
    return count
