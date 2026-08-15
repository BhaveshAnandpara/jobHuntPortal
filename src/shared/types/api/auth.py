"""Authentication API request/response contracts.

Owned by User Service (the only component that issues tokens) — see
`users/api/auth_routes.py` (`POST /auth/login`) and `users/api/routes.py`
(`POST /users`, which returns `LoginResponse` too, so registration
auto-logs-in rather than requiring a second login call).
"""

from pydantic import BaseModel

from shared.types.api.users import UserResponse


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


__all__ = ["LoginRequest", "LoginResponse"]
