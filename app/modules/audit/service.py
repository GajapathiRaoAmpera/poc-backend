import uuid
from typing import Any

from pymongo.asynchronous.client_session import AsyncClientSession

from app.core.db import Db
from app.modules.audit.models import AuditLog


async def record_audit(
    db: Db,
    actor_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None,
    metadata: dict[str, Any] | None = None,
    session: AsyncClientSession | None = None,
) -> None:
    """Write an audit row (inside ``session``'s transaction when given).

    Never pass secrets, password hashes or tokens in ``metadata``.
    """
    log = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata,
    )
    await db.audit_logs.insert_one(log.to_doc(), session=session)
