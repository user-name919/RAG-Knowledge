# 请求认证与知识库授权。管理员 Key 来自环境变量；普通 Key 在 MySQL 中只保存摘要。
# 认证结果不代表可以查询任意知识库，每个业务接口仍需调用 authorize 校验范围。

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

# 关闭框架自动报错，统一由 authenticate 返回本服务的 401 消息。
header = APIKeyHeader(name='api-key', auto_error=False)


# 表示已认证调用方：管理员标志、写权限和允许访问的知识库列表。
# 管理员的空列表不表示无权限，authorize 会对管理员跳过范围限制。
@dataclass
class Principal:
    admin: bool
    can_write: bool
    knowledge_base_ids: List[str]


# 从 api-key 头识别调用方。缺少或无效凭据返回 401。
# 管理员使用固定时间比较；普通凭据先哈希，再查找未撤销的数据库记录。
def authenticate(key: Optional[str] = Security(header), db: Session = Depends(get_db)):
    if not key:
        raise HTTPException(401, 'api-key required')
    admin = get_settings().admin_api_key
    # 按 UTF-8 字节比较以兼容非 ASCII 输入；固定时间比较降低逐字符时序差异。
    if admin and secrets.compare_digest(key.encode(), admin.encode()):
        return Principal(True, True, [])
    # 普通 Key 必须同时满足摘要相等与 active=True；每个请求重新查询撤销状态。
    row = db.scalar(select(APIKey).where(APIKey.key_hash == hashlib.sha256(key.encode()).hexdigest(), APIKey.active.is_(True)))
    if row is None:
        raise HTTPException(401, 'Invalid api-key')
    return Principal(False, row.can_write, row.knowledge_base_ids)


# 仅允许管理员执行凭据管理和创建知识库等操作；身份有效但权限不足返回 403。
def require_admin(principal=Depends(authenticate)):
    if not principal.admin:
        raise HTTPException(403, 'Admin key required')
    return principal


# 检查写权限以及本次请求的全部知识库是否均在授权范围内。
# 使用子集判断，不能用“有一个库被授权”来放行跨库请求。
def authorize(principal, kb_ids, write=False):
    if write and not principal.can_write:
        raise HTTPException(403, 'Write access required')
    if not principal.admin and not set(kb_ids).issubset(principal.knowledge_base_ids):
        raise HTTPException(403, 'Knowledge base access denied')
