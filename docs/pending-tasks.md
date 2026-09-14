# Pending Tasks

_Last updated: 2026-09-14_

| Task | Description | ETA | Priority | Tag |
|---|---|---|---|---|
| Fix architecture-audit gap for `resume_tailoring` | Add `user_id` to `JobMatchResponse` (matching's API), refactor `resume_tailoring` to call matching over HTTP instead of importing its repositories directly, then register `resume_tailoring` in the audit's `_COMPONENT_PACKAGES`. Approved, not started. | 2-3 hrs | High | architecture / tech-debt |
| Re-verify tailored-resume fix on real data | Switch to Gemini in Settings and regenerate the tailored resume for the job that previously failed on Groq; confirm work history and links now populate correctly. | 10 min (user action) | High | verification / tailored-resume |
| Decide: persist skill-breakdown on GET | `POST /jobs/{job_id}/tailored-resume` returns `base_matched_skills`/`base_missing_skills`/`tailored_matched_skills`/`tailored_missing_skills`, but a later `GET` of an already-generated doc doesn't. Decide whether to persist these on `tailored_resumes` so GET shows the same breakdown. | 1-2 hrs after decision | Medium | tailored-resume / backend |
| Write `docs/deployment/RUNBOOK.md` | Doesn't exist yet. Task 11 of `llm-provider-config` (documenting `LLM_CONFIG_ENCRYPTION_KEY`) is blocked on this. | 30 min | Low | docs / deployment |
| Editable system prompts per "skill" | Settings UI letting users view/override the default system prompts used by each LLM-driven capability (resume-tailoring, job-matching, etc.), calling each a "skill" in-app. | Needs spec first; ~1 day to build | Medium | feature / settings |
| Bulk outreach list view | Optional (off by default) list view showing person + short connection note for all pending outreach, instead of reviewing one at a time. | Needs spec first; ~1 day to build | Low | feature / outreach |
| Pay-as-you-go LLM billing | Operator-hosted LLM access as an alternative to BYOK — usage-based billing instead of requiring each user's own API key. | Needs spec first; multi-day (billing integration) | Low / future | feature / monetization |
| Per-job AI chat/session | A chat attached to each opportunity where the user can add ad-hoc corrections (e.g. "I also worked in Java") that trigger resume regeneration and re-matching for that job. | Needs spec first; 1-2 days to build | Medium | feature / resume-tailoring |
| Retry transient errors during LLM key validation | Saving an LLM key in Settings only makes one live validation call. A transient provider error (e.g. Gemini returning `503`, "model overloaded") gets reported as "This key was rejected by [provider]" — wording that implies the key itself is bad when it's really just a momentary server hiccup. Add a retry (1-2 attempts) for `retryable` errors before failing validation, and/or reword the failure message to distinguish "key invalid" from "provider temporarily unavailable, try again." | 1-2 hrs | Medium | bug / llm-provider-config |
