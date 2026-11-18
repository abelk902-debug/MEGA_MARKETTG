from __future__ import annotations

from database.models import AuditLog


def log_action(session, actor_id: int, action: str, target_type: str | None = None,
               target_id: str | int | None = None, details: str | None = None) -> None:
    """Adds an audit row to the current transaction (caller commits)."""
    session.add(AuditLog(actor_id=actor_id, action=action, target_type=target_type,
                         target_id=None if target_id is None else str(target_id), details=details))
