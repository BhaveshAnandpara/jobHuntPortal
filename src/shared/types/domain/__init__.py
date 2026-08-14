"""Canonical domain entities. See docs/architecture/domain-model.md.

Every field on every entity here is the *only* representation of that
concept — components do not define their own shape for any of these. One
module per entity; import from the submodule or from this package directly.
"""

from shared.types.domain.application import Application
from shared.types.domain.application_history import ApplicationHistory
from shared.types.domain.candidate_profile import CandidateProfile
from shared.types.domain.contact import Contact
from shared.types.domain.contact_score import ContactScore
from shared.types.domain.job import Job
from shared.types.domain.job_match import JobMatch
from shared.types.domain.job_source import JobSource
from shared.types.domain.outreach import Outreach
from shared.types.domain.resume import Resume
from shared.types.domain.user import User
from shared.types.domain.user_preferences import UserPreferences
from shared.types.domain.workflow_execution import WorkflowExecution

__all__ = [
    "User",
    "UserPreferences",
    "Resume",
    "CandidateProfile",
    "Job",
    "JobSource",
    "JobMatch",
    "Contact",
    "ContactScore",
    "Outreach",
    "Application",
    "ApplicationHistory",
    "WorkflowExecution",
]
