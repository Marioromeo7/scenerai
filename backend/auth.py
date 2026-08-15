from datetime import datetime, timedelta, timezone
import bcrypt
from jose import jwt, JWTError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from config import settings
from database import get_db
from models import User
import logging

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer()

# Direct bcrypt, not passlib's CryptContext wrapper — passlib==1.7.4 (last
# release, effectively unmaintained) crashes against bcrypt>=4.0 with
# AttributeError: module 'bcrypt' has no attribute '__about__', which is why
# bcrypt was pinned <4.0.0 (INSPECTION_REPORT M-10), locking out security
# patches indefinitely. bcrypt's own hash format is unchanged across this
# boundary, so existing stored hashes verify fine with no rehash/migration.
def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

def create_token(user_id):
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode({"sub": user_id, "exp": exp}, settings.jwt_secret, algorithm=settings.jwt_algorithm)

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        if not user_id: raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if not user: raise HTTPException(status_code=401, detail="User not found")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """403, not 404 -- an admin endpoint's existence isn't a secret worth
    hiding, only its data is."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
