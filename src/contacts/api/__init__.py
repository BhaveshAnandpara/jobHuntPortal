"""Contact Discovery Service API router.

Owned endpoints (docs/architecture/api-contracts.md#contact-discovery-service):
    GET  /jobs/{job_id}/contacts
    POST /jobs/{job_id}/contacts/search

Mounted into the app in api/main.py. Route handlers live in routes.py.
"""

from contacts.api.routes import router

__all__ = ["router"]
