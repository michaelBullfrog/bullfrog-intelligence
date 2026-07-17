from fastapi import HTTPException
from .models import UserContext

ROLE_PERMISSIONS = {
    "AI-Administrators": {"tickets", "customers", "webex", "renewals", "documents", "reports"},
    "Executives": {"tickets", "customers", "webex", "renewals", "documents", "reports"},
    "Engineering": {"tickets", "customers", "webex", "documents", "reports"},
    "Sales": {"customers", "renewals", "documents", "reports"},
    "Finance": {"customers", "renewals", "reports"},
}

def require_permission(user: UserContext, permission: str) -> None:
    permissions: set[str] = set()
    for role in user.roles:
        permissions.update(ROLE_PERMISSIONS.get(role, set()))

    if permission not in permissions:
        raise HTTPException(
            status_code=403,
            detail=f"User does not have permission to access {permission}.",
        )
