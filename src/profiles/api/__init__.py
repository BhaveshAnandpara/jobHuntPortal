"""Resume/Profile Service API router.

Owned endpoints (docs/architecture/api-contracts.md#resumeprofile-service):
    POST   /resumes
    GET    /resumes
    DELETE /resumes/{resume_id}
    GET    /profiles
    GET    /profiles/{profile_id}

Mounted into the app in api/main.py.
"""

from profiles.api.routes import router

__all__ = ["router"]
