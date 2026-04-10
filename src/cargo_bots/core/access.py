from __future__ import annotations


def has_admin_access(user_id: int | None, admin_ids: list[int]) -> bool:
    if user_id is None:
        return False
    if not admin_ids:
        return True
    return user_id in admin_ids
