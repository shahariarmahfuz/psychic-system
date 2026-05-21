import base64
import hashlib
import secrets
from cryptography.fernet import Fernet, InvalidToken
from .config import get_settings

settings = get_settings()


def _fernet_key() -> bytes:
    raw = settings.encryption_key.encode('utf-8')
    try:
        # Accept a valid Fernet key if the user provided one.
        Fernet(raw)
        return raw
    except Exception:
        digest = hashlib.sha256(raw).digest()
        return base64.urlsafe_b64encode(digest)


fernet = Fernet(_fernet_key())


def encrypt_secret(value: str) -> str:
    return fernet.encrypt(value.encode('utf-8')).decode('utf-8')


def decrypt_secret(cipher: str) -> str:
    try:
        return fernet.decrypt(cipher.encode('utf-8')).decode('utf-8')
    except InvalidToken as exc:
        raise RuntimeError('Cannot decrypt stored secret. Check ENCRYPTION_KEY.') from exc


def make_user_token() -> str:
    return 'ntk_' + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    pepper = settings.app_secret.encode('utf-8')
    return hashlib.sha256(pepper + token.encode('utf-8')).hexdigest()


def token_prefix(token: str) -> str:
    return token[:12]


def mask_token(token: str | None) -> str:
    if not token:
        return ''
    if len(token) <= 12:
        return token[:4] + '...'
    return token[:8] + '...' + token[-4:]


def is_admin_token(value: str | None) -> bool:
    return bool(value) and secrets.compare_digest(value, settings.admin_token)
