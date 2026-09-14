# Tailored Resume Generation: Technical Specification

_Status: Proposed · 2026-09-12_

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│  Frontend: Opportunity Detail page                                     │
│  frontend/src/features/opportunities/OpportunityDetailPage.tsx         │
│  New TailoredResumePanel: Generate button → loading → result/no-op     │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ POST /jobs/{job_id}/tailored-resume (JWT)
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│  New component: src/resume_tailoring/                                  │
│  1. Load JobMatch (match_score, missing/matched_skills, resume_id)     │
│  2. Load CandidateProfile; re-parse from raw_text if work_history/     │
│     contact are missing (existing resumes predating this feature)      │
│  3. get_user_llm_client(user_id)  — from docs/llm-provider-config/     │
│  4. LLM call #1: generate tailored content (ResumeProfile-shaped)      │
│  5. LLM call #2: score_profile_with_llm(tailored) — SAME function      │
│     used by normal job matching, unmodified                            │
│  6. if tailored_score - match_score < 0.05 → return "no improvement"   │
│     else → build .docx, persist, return summary                        │
└───────────────────────────┬──────────────────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────────────────────┐
│  tailored_resumes table (new)         + LocalTailoredResumeStorage     │
│  id | user_id | job_id | source_resume_id | storage_uri |              │
│  base_score | achieved_score | created_at                              │
│  (mirrors src/profiles/storage.py::LocalResumeStorage exactly)         │
└────────────────────────────────────────────────────────────────────────┘
                            ▲
                            │ GET /jobs/{job_id}/tailored-resume/download
                            │ (first file-download endpoint in the app)
```

**Why a new component, not a node in the job-matching graph.** `JobMatchingState` (`src/workflows/langgraph/job_matching/state.py`) is explicitly documented as a locked contract owned by Job Matching Service — "no other component may depend on this state's shape." The already-published `jobs.shortlisted` event and the persisted `JobMatch` row carry everything this feature needs (`match_score`, `matched_skills`, `missing_skills`, `selected_profile_id`/`selected_resume_id`) without reaching into that graph's internals. This also matches the codebase's existing convention of one component per Kafka-topic/domain boundary (`matching/`, `contacts/`, `outreach/`, `tracking/` are all structured identically).

**Why on-demand, not a Kafka consumer.** Confirmed by design decision: generation is triggered by a user clicking a button, not automatically when a job is shortlisted. So this ships as a synchronous FastAPI route, not a new `EventConsumer` — simpler, and keeps the user in control of their own LLM spend.

## Implementation Tasks

### Agent Assignment Summary

| Task | Suggested Agent | Why |
|---|---|---|
| 1. Extend `CandidateProfile` schema + extraction prompt | ecc:python-reviewer | Touches shared domain types + the existing parsing LLM contract |
| 2. Transparent re-parse-on-demand path | ecc:python-reviewer | Correctness-critical fallback for existing data |
| 3. `resume_tailoring` service: generation + score gate | ecc:python-reviewer | Core logic, reuses `score_profile_with_llm` unmodified |
| 4. `.docx` generation | general-purpose → ecc:python-reviewer | New code, no existing precedent to follow |
| 5. `tailored_resumes` table + storage | ecc:database-reviewer | Schema + file storage, mirrors `LocalResumeStorage` |
| 6. API routes (generate/get/download) | ecc:fastapi-reviewer | New endpoints, first file-download in the app |
| 7. Frontend panel | ecc:react-reviewer | New panel on an already-redesigned page (T8) |
| 8. Test suite | ecc:test-coverage | Cross-cutting |

---

### Task 1: Extend `CandidateProfile` with Contact Info and Work History

**Objective:** Capture what the app doesn't today — contact info and structured, per-job work history — in the same parsing pass that already runs on resume upload.

**Acceptance Criteria:**
- [ ] `ContactInfo` (`full_name`, `email`, `phone: str | None`, `location: str | None`) and `WorkExperienceEntry` (`company`, `title`, `start_date`, `end_date: str | None` — `None`/"Present" for current role, `bullets: list[str]`) added to `src/shared/types/domain/candidate_profile.py` and the `ResumeProfile` DTO in `src/shared/types/dto.py`
- [ ] `contact: ContactInfo | None` and `work_history: list[WorkExperienceEntry]` (default `[]`) added to both types
- [ ] Matching JSON columns added to `CandidateProfileRecord` in `src/profiles/models.py`, same pattern as existing `skills`/`education` JSON columns
- [ ] `ExtractedResumeProfile` (the LLM structured-output schema, `src/profiles/parsing.py`) extended with the same two fields; the extraction prompt updated to instruct the LLM to pull real contact details and a real per-job work history with bullet points from the resume text — still one LLM call, no new call added to the upload flow
- [ ] `scripts/bootstrap_db.py` re-run against a fresh DB creates the new columns cleanly; a manual `ALTER TABLE candidate_profiles ADD COLUMN ...` step is documented for existing local/deployed databases (same caveat as the LLM-config spec's Task 2 — `create_all` does not alter existing tables)
- [ ] Unit test: parsing a sample resume with clear contact info and job history populates both new fields correctly; a resume with no discoverable contact info leaves `contact` as `None` rather than fabricating one

**Dependencies:** None
**Estimated effort:** 1.5 hours

**Applied migration (existing databases).** Confirmed empirically on the local
dev Postgres: `python scripts/bootstrap_db.py` printed
`Created/verified 13 table(s)` but added no columns, because
`Base.metadata.create_all` skips tables that already exist. `\d
candidate_profiles` showed neither new column afterwards. Run this once
against any database that already has a `candidate_profiles` table (fresh
databases get the columns from `create_all` and need nothing):

```sql
ALTER TABLE candidate_profiles
  ADD COLUMN contact json,
  ADD COLUMN work_history json NOT NULL DEFAULT '[]'::json;
ALTER TABLE candidate_profiles ALTER COLUMN work_history DROP DEFAULT;
```

`json` (not `jsonb`) matches what SQLAlchemy's dialect-neutral `JSON` type
emits for the existing `skills`/`education` columns. The `DEFAULT` backfills
pre-existing rows so the `NOT NULL` can be added; dropping it afterwards
leaves the column identical to what `create_all` produces on a fresh
database.

---

### Task 2: Transparent Re-Parse for Existing Resumes

**Objective:** A `CandidateProfile` created before this feature shipped (missing `work_history`/`contact`) gets backfilled automatically the first time tailoring is requested against it — no forced re-upload.

**Acceptance Criteria:**
- [ ] Before generation proceeds, check whether the winning `CandidateProfile` has `work_history`/`contact` populated
- [ ] If missing, re-run `extract_profile_fields` (Task 1's extended version) against the resume's stored `raw_text` (or original file via `LocalResumeStorage.read()` if `raw_text` is insufficient), update the existing `CandidateProfile` row in place (not a new row — same resume, enriched data)
- [ ] This re-parse happens at most once per resume — the enriched result is persisted, so a second tailoring request against the same resume does not re-parse
- [ ] Test: a `CandidateProfile` fixture missing the new fields, when tailoring is requested, ends up with them populated before generation proceeds, and a second request does not trigger a second re-parse (assert the extraction function is called once)

**Dependencies:** Task 1
**Estimated effort:** 45 minutes

---

### Task 3: Resume Tailoring Service — Generation and Score Gate

**Objective:** The core logic: generate tailored content, score it identically to normal matching, and only proceed past the 5-point gate.

**Acceptance Criteria:**
- [ ] New module `src/resume_tailoring/service.py` (or equivalent), following this codebase's per-component structure
- [ ] `get_user_llm_client(user_id)` used to obtain the LLM client (from `docs/llm-provider-config/TECH.md` Task 5) — if that feature hasn't shipped yet, fall back to the current global `LLMClient()` behind a clearly marked TODO, so this feature isn't blocked on the other one's exact merge order
- [ ] LLM call #1 (new prompt): given the job (`NormalizedJob`), the enriched `ResumeProfile`, and the job's `missing_skills`/`matched_skills` from its `JobMatch` row, produce tailored content shaped as a `ResumeProfile`-compatible structure — explicitly constrained (in the prompt) to rephrase/reorder/emphasize existing profile content, never invent new employers, titles, or unsubstantiated achievements
- [ ] LLM call #2: pass the tailored content through `score_profile_with_llm` (`src/workflows/langgraph/job_matching/scoring.py`) **completely unmodified** — this is what makes the comparison apples-to-apples
- [ ] Gate: `tailored_score - job_match.match_score >= 0.05` → proceed to Task 4/5; otherwise return a "no significant improvement" result and persist nothing
- [ ] Unit tests: gate arithmetic at exactly 0.05 (should pass), just under (should not), with mocked scoring calls; prompt-construction test confirming missing/matched skills from the real `JobMatch` are included

**Dependencies:** Tasks 1, 2, and (loosely) `docs/llm-provider-config/` Task 5
**Estimated effort:** 2 hours

---

### Task 4: `.docx` Document Generation

**Objective:** Build a real, clean, editable resume document from the tailored content — genuinely new code, since nothing in this codebase writes `.docx` files today (only reads them, in `src/profiles/parsing.py`).

**Acceptance Criteria:**
- [ ] New module (e.g. `src/resume_tailoring/document.py`) using `python-docx`'s `Document()`, `add_heading`, `add_paragraph`, and basic styling to produce: a contact header, summary, skills section, work experience section (per entry: company/title/dates, then bullet points), education, certifications
- [ ] Output is a standard, ATS-friendly layout — no tables-as-layout tricks, no text-as-image, no headers/footers that ATS parsers commonly mishandle
- [ ] Test: generate a document from a fixture tailored profile, re-open it with `python-docx`'s reader (`Document(path)`) and assert the expected sections/paragraph text are present — a round-trip test, not just "it didn't crash"
- [ ] Manual check: the generated file opens correctly in Microsoft Word and Google Docs (not just python-docx's own reader) at least once during implementation

**Dependencies:** Task 3 (needs the tailored content shape to render)
**Estimated effort:** 2 hours (styling/layout iteration takes longer than the mechanical `python-docx` calls)

---

### Task 5: `tailored_resumes` Table and Storage

**Objective:** Persist generated documents so they can be re-downloaded without regenerating.

**Acceptance Criteria:**
- [ ] New table `tailored_resumes`: `id` (PK), `user_id`, `job_id`, `source_resume_id`, `storage_uri`, `base_score`, `achieved_score`, `created_at`
- [ ] `LocalTailoredResumeStorage` mirroring `src/profiles/storage.py::LocalResumeStorage`'s `save`/`read`/`delete` shape, writing under a sibling directory (e.g. `data/tailored_resumes/<user_id>/<id>_<job_id>.docx`, overridable via an env var following the `RESUME_STORAGE_DIR` naming convention)
- [ ] One row per successful generation; a job with no row yet means tailoring hasn't been (successfully) attempted, or the last attempt didn't clear the gate (worth logging which, even without a persisted row, for support/debugging)
- [ ] `scripts/bootstrap_db.py` creates the new table cleanly on a fresh DB (no ALTER TABLE concern here — it's a brand-new table)

**Dependencies:** Task 3
**Estimated effort:** 1 hour

---

### Task 6: API Routes

**Objective:** Expose generation, status, and download to the frontend.

**Acceptance Criteria:**
- [ ] `POST /jobs/{job_id}/tailored-resume` — `CurrentUserIdDependency` auth; checks LLM config gate first (reusing `LLM_NOT_CONFIGURED` from `docs/llm-provider-config/`); runs Task 3's flow; returns either `{achieved_score, base_score, id}` (201) or a `{improved: false, message}` body (200) — never a bare error for the legitimate "no improvement" outcome
- [ ] `GET /jobs/{job_id}/tailored-resume` — returns the existing record's metadata if one exists, 404 otherwise (same "unset is not an error" convention used elsewhere in this app)
- [ ] `GET /jobs/{job_id}/tailored-resume/download` — streams the file with correct `Content-Type` (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`) and `Content-Disposition: attachment`; this is the first file-download endpoint in the app, so build the streaming helper generically enough to be reused for an eventual original-resume download without over-engineering it now
- [ ] API tests: generate above threshold → 201, record persisted, file downloadable; generate below threshold → 200 with `improved: false`, nothing persisted; no LLM config → the existing gating error; download for a nonexistent record → 404

**Dependencies:** Tasks 3, 5
**Estimated effort:** 1.5 hours

---

### Task 7: Frontend — Tailored Resume Panel

**Objective:** Surface the feature on the Opportunity Detail page.

**Acceptance Criteria:**
- [ ] New `TailoredResumePanel` composed into `OpportunityDetailPage.tsx` (alongside the panels from the earlier redesign — Job Info, Match, Contacts, Outreach Summary, Lifecycle Timeline)
- [ ] States: no LLM config (gated, links to Settings — reusing the pattern from `docs/llm-provider-config/TECH.md` Task 10); idle (Generate button); loading (this call is slower than most — two LLM calls plus document generation, so the loading state should say so, not just spin silently); success (shows `achieved_score` vs `base_score`, a Download button); no-improvement (clear message, no button to "retry" without new information since nothing about the job or profile has changed)
- [ ] `frontend/src/api/tailoredResume.ts` + TanStack Query hooks, following this app's established per-feature API-client convention
- [ ] Component tests covering all four states

**Dependencies:** Task 6
**Estimated effort:** 1.5 hours

---

### Task 8: Test Suite

**Objective:** Verify the feature end-to-end per the Product Spec's Rollout & Validation Plan.

**Acceptance Criteria:**
- [ ] All unit/API tests from Tasks 1–7
- [ ] One integration test covering the full flow: enriched profile → generate → score → gate → document → download, with the LLM calls mocked to return both an "improved" and a "not improved" case
- [ ] Full existing backend and frontend test suites still pass

**Dependencies:** All prior tasks
**Estimated effort:** 1.5 hours

---

## Verification Checklist

- [ ] A resume genuinely weak for a job produces a real, downloadable, well-formed `.docx`
- [ ] A resume already strong for a job produces "no improvement found" and no file
- [ ] An existing (pre-feature) resume works without a forced re-upload
- [ ] No LLM config configured → standard gating message
- [ ] Generated document opens correctly in Word and Google Docs
- [ ] Full test suites pass

## Timeline Estimate

- **Total effort:** ~12 hours across 8 tasks
- **Critical path:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 (largely sequential — each task's output is the next task's input)
- **Can start in parallel with `docs/llm-provider-config/`'s later tasks:** Task 1 here has no dependency on that feature; only Task 3's `get_user_llm_client` call needs it to have landed (or a temporary global-client fallback, as noted)

## Rollback Plan

1. **Before any tailored resume has been generated:** the feature can be disabled by hiding the frontend panel and/or feature-flagging the API routes — the schema extension (Task 1) is additive and harmless to leave in place either way
2. **After documents exist:** `tailored_resumes` rows and files are independent of the generation logic — disabling generation doesn't affect already-downloaded documents (which live on the user's own machine by then anyway)
3. **If the profile schema extension causes unexpected issues:** the new columns are additive (nullable/defaulted) and don't change any existing read path's behavior — existing matching/scoring logic never reads `contact`/`work_history`, so this is low-risk to leave in place even if generation itself is rolled back

## References & Links

- Product spec: `docs/tailored-resume/PRODUCT.md`
- Scoring mechanism reused unmodified: `src/workflows/langgraph/job_matching/scoring.py`
- Profile/resume patterns to follow: `src/profiles/models.py`, `parsing.py`, `storage.py`
- Job match data already available: `src/matching/models.py::JobMatchRecord`
- Depends on: `docs/llm-provider-config/TECH.md` (Task 5's `get_user_llm_client`, Task 7's gating pattern)
