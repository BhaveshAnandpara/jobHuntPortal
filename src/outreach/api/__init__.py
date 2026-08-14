"""Outreach Service API router.

Owned endpoints (docs/architecture/api-contracts.md#outreach-service):
    GET  /outreach
    GET  /outreach/{outreach_id}
    POST /outreach/{outreach_id}/approve
    POST /outreach/{outreach_id}/reject
    POST /outreach/{outreach_id}/edit

Mounted into the app in api/main.py.
"""

from outreach.api.routes import router

__all__ = ["router"]
