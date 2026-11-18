import asyncio

_locks: dict[tuple[int, int], asyncio.Lock] = {}


def user_lock(user_id: int) -> asyncio.Lock:
    """One lock per user (per event loop): serialises that user's multi-update flows (photo albums, save)."""
    key = (id(asyncio.get_running_loop()), user_id)
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock
