"""User Service API routers.

Owned endpoints (docs/architecture/api-contracts.md#user-service):
    POST /users               (router)
    GET  /users/me/preferences  (router)
    PUT  /users/me/preferences  (router)
    POST /auth/login          (auth_router)

Mounted into the app in api/main.py.
"""

from users.api.auth_routes import router as auth_router
from users.api.routes import router

__all__ = ["auth_router", "router"]
