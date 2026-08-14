/**
 * Shared E2E fixture constants — kept in sync BY HAND with
 * `e2e/backend_server.py`'s Python-side constants of the same name/value.
 * These must match EXACTLY (character-for-character for resume texts, and
 * as literal strings for URLs/companies/titles) since the backend script's
 * scripted fakes key off these exact values.
 *
 * See e2e/backend_server.py's module docstring for the full rationale
 * (three professions, AND-pair matching-LLM markers, etc).
 */

export const PROFILE_A_TITLE = 'Senior Backend Engineer (Python/FastAPI)'
export const PROFILE_B_TITLE = 'Senior Mechanical Design Engineer (CAD/SolidWorks)'
export const PROFILE_C_TITLE = 'HR Business Partner (Talent Acquisition)'

export const RESUME_A_NAME = 'Alex Morgan'
export const RESUME_B_NAME = 'Jordan Rivera'
export const RESUME_C_NAME = 'Taylor Chen'

// Absolute paths to the fixture resume files Playwright uploads via
// setInputFiles — see ./fixtures/*.txt, whose contents must match the
// RESUME_*_TEXT constants in e2e/backend_server.py exactly.
export const RESUME_A_FILE = 'tests/e2e/fixtures/resume-a-software.txt'
export const RESUME_B_FILE = 'tests/e2e/fixtures/resume-b-mechanical.txt'
export const RESUME_C_FILE = 'tests/e2e/fixtures/resume-c-hr.txt'
export const RESUME_FAIL_FILE = 'tests/e2e/fixtures/resume-fail-trigger.txt'

export const JOB1_URL = 'https://boards.example.com/jobs/e2e-mech-design-1'
export const JOB1_COMPANY = 'Acme Robotics'
export const JOB1_TITLE = 'Mechanical Design Engineer II - Robotics Platform'

export const JOB2_URL = 'https://boards.example.com/jobs/e2e-backend-swe-1'
export const JOB2_COMPANY = 'Initech Systems'
export const JOB2_TITLE = 'Backend Software Engineer - Core Services'

export const JOB3_URL = 'https://boards.example.com/jobs/e2e-hr-bp-1'
export const JOB3_COMPANY = 'Globex People Ops'
export const JOB3_TITLE = 'HR Business Partner - Global People Team'

export const JOB4_URL = 'https://boards.example.com/jobs/e2e-empty-contacts'
export const JOB4_COMPANY = 'Quiet Startup Co'
export const JOB4_TITLE = 'Mechanical Design Engineer - Quiet Startup'

export const JOB5_URL = 'https://boards.example.com/jobs/e2e-contact-search-fail'
export const JOB5_COMPANY = 'Search Fail Inc'
export const JOB5_TITLE = 'Mechanical Design Engineer - Search Fail'

// A syntactically well-formed http(s) URL never registered in the
// backend's fake page fetcher -> always yields JOB_FETCH_FAILED (empty
// page content -> INVALID_JOB_URL) with no special-casing needed.
export const JOB_UNKNOWN_URL = 'https://boards.example.com/jobs/e2e-never-registered'
// Registered to always raise a page-fetch error (JOB_FETCH_FAILED).
export const JOB_FETCH_FAIL_URL = 'https://boards.example.com/jobs/e2e-fetch-fail'
// Not a well-formed URL at all -> INVALID_JOB_URL from validate_url, before
// any fetch is attempted.
export const INVALID_URL_INPUT = 'not a valid url'

export const CONTACT_MECH_NAME = 'Sam Lee'
export const CONTACT_SWE_NAME = 'Riley Chen'
export const CONTACT_HR_NAME = 'Morgan Blake'

export const EMPTY_CONTACTS_COMPANY = 'Quiet Startup Co'
export const CONTACT_SEARCH_FAIL_COMPANY = 'Search Fail Inc'

/** Builds a unique-per-test-run email so parallel spec files never collide
 * on the same user. */
export function uniqueEmail(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`
}
