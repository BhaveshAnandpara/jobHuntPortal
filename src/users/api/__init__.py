"""User Service API router.

Owned endpoints (docs/architecture/api-contracts.md#user-service):
    POST /users
    GET  /users/{user_id}/preferences
    PUT  /users/{user_id}/preferences

Mounted into the app in api/main.py.
"""

from users.api.routes import router

__all__ = ["router"]
