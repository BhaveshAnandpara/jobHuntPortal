"""API integration: validate every implemented endpoint against
docs/architecture/api-contracts.md's current text using the shared
TestClient, focusing on endpoints/status-codes/error-shapes not already
exercised incidentally by the other integration test files.

See the task brief's section 11.
"""

from __future__ import annotations

from uuid import uuid4

from tests.integration.conftest import IntegrationHarness


def test_user_service_endpoints(harness: IntegrationHarness) -> None:
    created = harness.client.post(
        "/users", json={"email": f"{uuid4()}@example.com", "display_name": "Ada Lovelace"}
    )
    assert created.status_code == 201
    user = created.json()

    # No UserPreferences row exists until PUT creates one (users/service.py's
    # get_preferences raises NOT_FOUND rather than assuming a default row —
    # api-contracts.md documents 404 as a valid GET status for this reason).
    before_put = harness.client.get(f"/users/{user['id']}/preferences")
    assert before_put.status_code == 404
    assert before_put.json()["detail"]["code"] == "NOT_FOUND"

    updated = harness.client.put(
        f"/users/{user['id']}/preferences",
        json={
            "target_roles": ["Mechanical Design Engineer"],
            "target_locations": ["Remote"],
            "remote_preference": "REMOTE",
            "excluded_companies": [],
            "min_salary": None,
            "salary_currency": None,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["target_roles"] == ["Mechanical Design Engineer"]
    assert updated.json()["remote_preference"] == "REMOTE"

    after_put = harness.client.get(f"/users/{user['id']}/preferences")
    assert after_put.status_code == 200
    assert after_put.json()["user_id"] == user["id"]

    missing = harness.client.get(f"/users/{uuid4()}/preferences")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "NOT_FOUND"


def test_resume_profile_endpoints_error_shapes(harness: IntegrationHarness) -> None:
    user = harness.create_user()

    resumes = harness.client.get("/resumes", params={"user_id": user["id"]})
    assert resumes.status_code == 200
    assert resumes.json() == []

    delete_missing = harness.client.delete(f"/resumes/{uuid4()}")
    assert delete_missing.status_code == 404
    assert delete_missing.json()["detail"]["code"] == "NOT_FOUND"

    profile_missing = harness.client.get(f"/profiles/{uuid4()}")
    assert profile_missing.status_code == 404
    assert profile_missing.json()["detail"]["code"] == "NOT_FOUND"


def test_job_ingestion_get_job_404(harness: IntegrationHarness) -> None:
    response = harness.client.get(f"/jobs/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


def test_job_matching_matches_endpoint_404(harness: IntegrationHarness) -> None:
    response = harness.client.get(f"/jobs/{uuid4()}/matches")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"


def test_contact_discovery_endpoints(harness: IntegrationHarness) -> None:
    job_id = str(uuid4())
    user = harness.create_user()

    # Empty list rather than 404 for an unknown/not-yet-searched job — see
    # contacts/api/routes.py's own documented gap-resolution note.
    listed = harness.client.get(f"/jobs/{job_id}/contacts")
    assert listed.status_code == 200
    assert listed.json() == []

    triggered = harness.client.post(
        f"/jobs/{job_id}/contacts/search",
        json={
            "user_id": user["id"],
            "company": "Acme Robotics",
            "title": "Senior Mechanical Design Engineer",
            "location": "Remote",
        },
    )
    assert triggered.status_code == 202
    body = triggered.json()
    assert body["job_id"] == job_id

    from infrastructure.kafka.topics import Topic
    from tests.integration.conftest import log_envelopes

    requested = log_envelopes(harness.broker, Topic.CONTACTS_REQUESTED)
    assert len(requested) == 1
    assert requested[0].payload.company == "Acme Robotics"

    invalid = harness.client.post(f"/jobs/{job_id}/contacts/search", json={"user_id": user["id"]})
    assert invalid.status_code == 422  # missing required company/title


def test_outreach_endpoints_error_shapes(harness: IntegrationHarness) -> None:
    user = harness.create_user()
    listed = harness.client.get("/outreach", params={"user_id": user["id"]})
    assert listed.status_code == 200
    assert listed.json() == []

    missing = harness.client.get(f"/outreach/{uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "NOT_FOUND"

    approve_missing = harness.client.post(
        f"/outreach/{uuid4()}/approve", json={"final_message": None}
    )
    assert approve_missing.status_code == 404

    reject_missing = harness.client.post(f"/outreach/{uuid4()}/reject")
    assert reject_missing.status_code == 404

    edit_missing = harness.client.post(
        f"/outreach/{uuid4()}/edit", json={"message": "hello"}
    )
    assert edit_missing.status_code == 404


def test_tracking_endpoints_error_shapes(harness: IntegrationHarness) -> None:
    missing = harness.client.get(f"/applications/{uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "NOT_FOUND"

    missing_history = harness.client.get(f"/applications/{uuid4()}/history")
    assert missing_history.status_code == 404

    missing_patch = harness.client.patch(
        f"/applications/{uuid4()}/status",
        json={"new_status": "APPLIED", "applied_date": None, "notes": None},
    )
    assert missing_patch.status_code == 404


def test_tracking_manual_status_transition_rejects_invalid_edges(
    harness: IntegrationHarness,
) -> None:
    """DISCOVERED -> INTERVIEW skips required matching — must be rejected,
    per state-machines.md's explicit invalid-transition example.
    """
    import asyncio

    import tracking.consumers as tracking_consumers
    from infrastructure.kafka.serialization import build_envelope
    from infrastructure.kafka.topics import Topic
    from shared.types.ids import UserId
    from tests.matching.conftest import make_normalized_job

    user_id = UserId(uuid4())
    job = make_normalized_job(user_id)
    envelope = build_envelope(Topic.JOBS_DISCOVERED, job, producer="job-ingestion-service")

    async def _seed() -> None:
        await tracking_consumers._handle_job_discovered_async(envelope)

    asyncio.run(_seed())

    applications = harness.client.get(
        "/applications", params={"user_id": str(user_id)}
    ).json()
    application_id = applications[0]["id"]

    response = harness.client.patch(
        f"/applications/{application_id}/status",
        json={"new_status": "INTERVIEW", "applied_date": None, "notes": None},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"
