"""Job Matching Service API router.

Owned endpoints (docs/architecture/api-contracts.md#job-matching-service),
both read-only:
    GET /jobs                    -- NOT implemented, see api/routes.py
    GET /jobs/{job_id}           -- NOT implemented, see api/routes.py
    GET /jobs/{job_id}/matches   -- implemented

Mounted into the app in api/main.py.
"""

from matching.api.routes import router

__all__ = ["router"]
