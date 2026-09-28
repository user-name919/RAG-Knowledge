from dataclasses import dataclass
import hashlib
import secrets
from typing import List, Optional
from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session
from .config import get_settings
from .db import get_db
from .models import APIKey

header = APIKeyHeader(name='api-key', auto_error=False)


@dataclass
class Principal:
    admin: bool
    can_write: bool
    knowledge_base_ids: List[str]


def authenticate(key: Optional[str] = Security(header), db: Session = Depends(get_db)):
    if not key:
        raise HTTPException(401, 'api-key required')
    admin = get_settings().admin_api_key
    if admin and secrets.compare_digest(key.encode(), admin.encode()):
        return Principal(True, True, [])
    row = db.scalar(select(APIKey).where(APIKey.key_hash == hashlib.sha256(key.encode()).hexdigest(), APIKey.active.is_(True)))
    if row is None:
        raise HTTPException(401, 'Invalid api-key')
    return Principal(False, row.can_write, row.knowledge_base_ids)


def require_admin(principal=Depends(authenticate)):
    if not principal.admin:
        raise HTTPException(403, 'Admin key required')
    return principal


def authorize(principal, kb_ids, write=False):
    if write and not principal.can_write:
        raise HTTPException(403, 'Write access required')
    if not principal.admin and not set(kb_ids).issubset(principal.knowledge_base_ids):
        raise HTTPException(403, 'Knowledge base access denied')
