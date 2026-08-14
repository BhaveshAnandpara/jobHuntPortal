"""Tracking Service API router.

Owned endpoints (docs/architecture/api-contracts.md#tracking-service):
    GET /applications
    GET /applications/{application_id}
    PATCH /applications/{application_id}/status
    GET /applications/{application_id}/history

Mounted into the app in api/main.py.
"""

from tracking.api.routes import router

__all__ = ["router"]
