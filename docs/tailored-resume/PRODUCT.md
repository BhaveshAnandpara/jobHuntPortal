# Tailored Resume Generation: Product Specification

_Status: Proposed · 2026-09-12_

## Overview

Today, JobHunt picks the best of a user's *existing* resumes for each job (`JobMatch.selected_resume_id`) — it never creates new content. This feature adds the option to generate a **new, job-specific version** of a resume, in editable `.docx` format, when doing so would meaningfully improve that resume's fit for a particular job. "Meaningfully" is defined precisely: the tailored version must score at least 5 percentage points higher than the original on this app's existing match-scoring mechanism, or nothing gets created at all. This keeps the feature honest — it never manufactures a document just to have one, and it never spends the user's own LLM quota (per `docs/llm-provider-config/`) on jobs where their current resume is already a strong fit.

**Target audience:** Any user with at least one matched job and a configured LLM provider.
**Success measure:** For a job where a real improvement is possible, the user gets a genuinely better, immediately usable `.docx` resume they can edit and submit. For a job where their current resume is already strong, they get a clear "no improvement found" answer instead of a low-value document.

## User Experience Goals

1. **User-initiated, not automatic:** Generation happens only when the user clicks "Generate tailored resume" on a specific opportunity — never silently in the background, so it never surprises the user with LLM usage they didn't ask for.
2. **Honest gating:** If the tailored version doesn't clear the 5-point bar, the user is told plainly that their current resume is already well-suited — never handed a marginal or unhelpful document.
3. **Real, usable output:** The generated `.docx` has real contact information and real, job-specific work history with bullet points — not just a skills list — so it's something the user could actually submit with light editing.
4. **Editable, not locked in:** The file is a normal `.docx` the user opens, edits, and saves in their own Word/Google Docs — no proprietary format, no round-trip required back into JobHunt.
5. **No surprise cost:** Generation is gated behind the user's own configured LLM key (per `docs/llm-provider-config/`) — the same "add your key in Settings" pattern used everywhere else AI-powered actions are gated.

## Behavior & Invariants

### Data the Feature Needs (and Doesn't Have Yet)

- Today's `CandidateProfile` (built automatically whenever a resume is uploaded, per the existing resume-parsing pipeline) has skills, a summary, education, and short project blurbs — but **no contact info** (name/email/phone) and **no structured work history** (company/title/dates/bullet points). Both are extended into the same profile, captured by the same parsing step that already runs on upload.
- **Acceptance:** Uploading a new resume produces a `CandidateProfile` that includes contact info and a structured work-history list, in addition to everything captured today.
- Existing resumes uploaded before this ships are missing this data. The first time tailoring is requested against one of them, it's transparently re-parsed from its already-stored text — the user is never asked to re-upload.
- **Acceptance:** Requesting a tailored resume for a job whose winning profile predates this feature succeeds without the user taking any extra action.

### Generation & the 5-Point Gate

- Clicking "Generate tailored resume" on a matched opportunity produces tailored content, scores it using the exact same scoring mechanism used for normal job matching, and compares that score to the job's already-computed match score.
- **Acceptance:** If the tailored score is at least 5 percentage points higher, a downloadable `.docx` is produced and offered. If not, the user sees a specific message ("Your current resume is already well-suited for this job") and no document is created.
- The comparison always uses the same yardstick as the original match — never a different or looser scoring standard for the tailored version, which would make the 5-point promise meaningless.

### Document Content & Format

- The generated document is a clean, standard, ATS-friendly resume layout — contact header, summary, skills, work experience (with per-job bullet points), education, certifications — populated with content tailored to the specific job (emphasizing relevant skills/experience, addressing gaps identified during matching).
- **Acceptance:** The resulting `.docx` opens cleanly in Microsoft Word and Google Docs, is fully editable (normal text, not an image or locked template), and reads as a complete, usable resume — not a fragment.
- It does **not** attempt to visually replicate the user's originally uploaded resume's formatting (which may have come from a PDF, DOCX, or plain text file with no reliable layout to preserve) — it is a fresh, standardized layout every time.

### Gating on LLM Configuration

- Generation requires a validated LLM provider configured for that user (per `docs/llm-provider-config/`) — the same gate already applied to resume upload and job submission.
- **Acceptance:** Attempting to generate without a configured key shows the same "Add your AI provider key in Settings" message used elsewhere in the app, with a link there.

## Success Criteria

1. ✓ A tailored `.docx` is only ever created when it scores ≥5 points higher than the original on the same scoring mechanism used for regular matching
2. ✓ The generated document includes real contact info and real, job-specific work history — not just a reshuffled skills list
3. ✓ A user can request tailoring for a job whose resume was uploaded before this feature shipped, with no manual re-upload step
4. ✓ Users without a configured LLM key see the standard gating message, not a confusing failure
5. ✓ The document downloads successfully and opens correctly in common word processors

## Non-Goals & Out of Scope

- Automatic/background generation for every shortlisted job — this is on-demand only (see Product decision during design)
- Preserving the original uploaded resume's visual formatting/branding
- Any round-trip sync of user edits back into JobHunt — the downloaded file is the end of this feature's responsibility
- Multiple tailored variants per job (e.g. "generate 3 options") — one tailored attempt per request
- A separate resume-builder/editor inside JobHunt itself — this generates a starting document for the user's own external editor, not an in-app editing experience

## Risk Mitigation

### False Confidence from the Score Gate

- **Risk:** a 5-point improvement on this app's internal scoring model doesn't guarantee a real-world improvement in an actual ATS or human reviewer's eyes
- **Mitigation:** frame the feature's language around "matches this app's own analysis of the job" rather than promising real-world outcomes; this is already implicit in reusing the same scoring mechanism transparently

### LLM Hallucination in Work History

- **Risk:** the LLM could invent or distort details in the tailored work-history bullets beyond what the original resume/profile actually supports
- **Mitigation:** the tailoring prompt is constrained to rephrase/reorder/emphasize existing content (skills, projects, work history already captured in the profile), not invent new employers, titles, or achievements; the user reviews and edits the downloaded document before using it, which is exactly why it ships as an editable `.docx` rather than a final, submit-ready file

### Existing-Resume Backfill Cost

- **Risk:** re-parsing an old resume on first tailoring request adds latency and an extra LLM call the user didn't explicitly ask for
- **Mitigation:** this only happens once per resume (the enriched profile is then persisted and reused for future tailoring requests against the same resume), and it draws on the same per-user LLM key already required for the tailoring action itself — no separate cost surprise

## Rollout & Validation Plan

1. Ship the profile schema extension and re-parse path first — verify it doesn't disturb existing matching/scoring behavior for any current user
2. Ship the tailoring service and score-gate logic, with the API returning correct "no improvement" vs. "here's your document" responses
3. Ship the `.docx` generation and download endpoint — manually verify the output opens correctly in Word and Google Docs
4. Ship the frontend panel last, once the backend round-trip is verified via direct API calls
5. Manual end-to-end walkthrough: a resume genuinely weak for a job (expect a real tailored document) and a resume already strong for a job (expect "no improvement found")

## References

- Existing scoring mechanism to reuse unchanged: `src/workflows/langgraph/job_matching/scoring.py::score_profile_with_llm`
- Existing profile data model to extend: `src/shared/types/domain/candidate_profile.py`, `src/profiles/models.py`, `src/profiles/parsing.py`
- Existing resume storage pattern to mirror: `src/profiles/storage.py::LocalResumeStorage`
- Depends on: `docs/llm-provider-config/PRODUCT.md` (per-user LLM configuration and its gating pattern)
- Technical design: `docs/tailored-resume/TECH.md`
