from sqlalchemy.orm import Session
from ..models import AuditLog


def log(db: Session, action: str, actor: str = 'system', details: str | None = None) -> None:
    db.add(AuditLog(action=action, actor=actor, details=details))
