# Claude Agent Definitions

These agent definitions are intended to work against the locked contracts under `docs/architecture/`.

## Recommended execution order

1. `architect-agent.md` — contract guardian
2. Parallel Wave 1:
   - `user-agent.md`
   - `profile-agent.md`
   - `job-agent.md`
   - `kafka-agent.md`
   - `llm-provider-agent.md`
   - `database-agent.md`
   - `external-integrations-agent.md`
3. `matching-agent.md`
4. Parallel Wave 2:
   - `contact-agent.md`
   - `tracker-agent.md`
5. `outreach-agent.md`
6. `integration-agent.md`

Architecture documents remain authoritative. Agents must not silently redefine cross-component contracts.

## Frontend agents

Defined once the backend MVP was validated end-to-end (Step 9) and the
frontend architecture was documented under `docs/frontend/` (Step 10) and
its two backend-blocking contract gaps closed (Step 10.5). The
`frontend/` repository skeleton these agents build on top of was created
in Step 11. `docs/frontend/agent-ownership.md` is authoritative for scope;
these files are each agent's actual briefing.

Recommended execution order:

1. Parallel foundation wave (no dependency on each other):
   - `frontend-shell-agent.md`
   - `frontend-design-agent.md`
   - `frontend-api-agent.md`
2. Parallel feature wave (each depends only on wave 1's output, not on
   each other — `frontend-opportunities-agent.md` additionally depends on
   `frontend-contacts-agent.md`'s finished `ContactsPanel`):
   - `frontend-profile-agent.md`
   - `frontend-contacts-agent.md`
   - `frontend-opportunities-agent.md`
   - `frontend-outreach-agent.md`
3. `frontend-integration-ui-agent.md` (runs last)

Frontend agents must not modify backend files (`src/`, `tests/`,
`docs/architecture/`) under any circumstance — a discovered backend gap is
documented, never silently patched from a frontend agent.
