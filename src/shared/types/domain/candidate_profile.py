"""CandidateProfile — the system's understanding of one professional
identity derived from a resume: skills, experience, target roles. A user
with multiple resumes has multiple profiles (AI Engineer, Java Backend,
etc.). This is the entity that keeps the platform profession-independent:
it has no field that assumes any specific domain. See
docs/architecture/domain-model.md#candidateprofile.

Ownership: Resume/Profile Service (Profile Intelligence). Modifiable by
Resume/Profile Service only.
"""

from datetime import datetime

from pydantic import BaseModel

from shared.types.dto import EducationEntry
from shared.types.enums import ProfileStatus
from shared.types.ids import ProfileId, ResumeId, UserId


class CandidateProfile(BaseModel):
    id: ProfileId
    user_id: UserId
    resume_id: ResumeId  # source resume
    title: str  # inferred label, e.g. "Java Backend Engineer"
    summary: str | None = None  # short inferred professional summary
    skills: list[str] = []  # may be empty list, never null
    experience_years: float | None = None  # total relevant experience
    # deliberately not an enum — seniority vocabulary varies by profession
    seniority: str | None = None
    education: list[EducationEntry] = []
    certifications: list[str] = []
    projects: list[str] = []  # short descriptions
    industries: list[str] = []  # inferred industry fit
    target_roles: list[str] = []  # roles this profile is suited for
    status: ProfileStatus
    generated_at: datetime
