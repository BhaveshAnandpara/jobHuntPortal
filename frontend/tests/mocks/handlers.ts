/**
 * MSW request handlers, generated/kept in sync with the same OpenAPI
 * schema used for types — see
 * docs/frontend/testing-strategy.md#msw-handler-source. Each feature agent
 * adds its own handler array in this file's section for its service; none
 * edits another feature's handlers.
 *
 * This section (User/Resume/Profile/Jobs/Matching/Contacts/Outreach/
 * Tracking Service) is owned by frontend-api-agent — one baseline
 * "happy path" handler per endpoint in `src/api/*.ts`, matching real
 * response shapes from `src/api/generated/schema.d.ts`. Individual test
 * files override specific handlers via `server.use(...)` for error-path
 * scenarios (4xx/404/409/5xx/network failure) — this file only supplies
 * defaults so hook/function tests don't need to redeclare a happy path
 * every time.
 */

import { http, HttpResponse } from 'msw'
import { API_BASE_URL } from '../../src/api/client'

const base = API_BASE_URL

// ---------------------------------------------------------------------------
// User Service
// ---------------------------------------------------------------------------

function loginResponseFixture(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    access_token: 'test.jwt.token',
    token_type: 'bearer',
    user: { id: 'user-1', email: 'a@example.com', display_name: 'Ada', created_at: '2026-01-01T00:00:00Z' },
    ...overrides,
  }
}

export const userHandlers = [
  // Registration auto-logs-in — returns a LoginResponse (token + user), not
  // a bare UserResponse.
  http.post(`${base}/users`, () => HttpResponse.json(loginResponseFixture(), { status: 201 })),
  http.post(`${base}/auth/login`, () => HttpResponse.json(loginResponseFixture())),
  http.get(`${base}/users/me/preferences`, () =>
    HttpResponse.json({
      id: 'pref-1',
      user_id: 'user-1',
      target_roles: ['Backend Engineer'],
      target_locations: ['Remote'],
      remote_preference: 'REMOTE',
      excluded_companies: [],
      min_salary: null,
      salary_currency: null,
    }),
  ),
  http.put(`${base}/users/me/preferences`, () =>
    HttpResponse.json({
      id: 'pref-1',
      user_id: 'user-1',
      target_roles: ['Backend Engineer', 'Platform Engineer'],
      target_locations: ['Remote'],
      remote_preference: 'REMOTE',
      excluded_companies: [],
      min_salary: null,
      salary_currency: null,
    }),
  ),
]

// ---------------------------------------------------------------------------
// Resume/Profile Service
// ---------------------------------------------------------------------------

export const resumeHandlers = [
  http.get(`${base}/resumes`, () =>
    HttpResponse.json([
      {
        id: 'resume-1',
        user_id: 'user-1',
        file_name: 'resume.pdf',
        status: 'PARSED',
        uploaded_at: '2026-01-01T00:00:00Z',
      },
    ]),
  ),
  http.post(`${base}/resumes`, () =>
    HttpResponse.json(
      {
        id: 'resume-2',
        user_id: 'user-1',
        file_name: 'resume2.pdf',
        status: 'UPLOADED',
        uploaded_at: '2026-01-02T00:00:00Z',
      },
      { status: 202 },
    ),
  ),
  http.delete(`${base}/resumes/:resumeId`, () => new HttpResponse(null, { status: 204 })),
]

export const profileHandlers = [
  http.get(`${base}/profiles`, () =>
    HttpResponse.json([
      {
        profile_id: 'profile-1',
        resume_id: 'resume-1',
        user_id: 'user-1',
        title: 'Backend Engineer',
        summary: 'Experienced backend engineer',
        skills: ['Python', 'FastAPI'],
        experience_years: 5,
        seniority: 'Senior',
        education: [],
        status: 'ACTIVE',
      },
    ]),
  ),
  http.get(`${base}/profiles/:profileId`, () =>
    HttpResponse.json({
      profile_id: 'profile-1',
      resume_id: 'resume-1',
      user_id: 'user-1',
      title: 'Backend Engineer',
      summary: 'Experienced backend engineer',
      skills: ['Python', 'FastAPI'],
      experience_years: 5,
      seniority: 'Senior',
      education: [],
      status: 'ACTIVE',
    }),
  ),
]

// ---------------------------------------------------------------------------
// Job Ingestion Service
// ---------------------------------------------------------------------------

export const jobHandlers = [
  http.post(`${base}/jobs/ingest-url`, () =>
    HttpResponse.json(
      {
        id: 'job-1',
        user_id: 'user-1',
        company: 'Acme Robotics',
        title: 'Senior Backend Engineer',
        location: 'Remote',
        description: 'Own our platform services.',
        extracted_skills: ['Python'],
        experience_required: '5+ years',
        source_url: 'https://boards.example.com/jobs/1',
        processing_status: 'DISCOVERED',
        discovered_at: '2026-01-01T00:00:00Z',
      },
      { status: 202 },
    ),
  ),
  http.get(`${base}/jobs/:jobId`, () =>
    HttpResponse.json({
      id: 'job-1',
      user_id: 'user-1',
      company: 'Acme Robotics',
      title: 'Senior Backend Engineer',
      location: 'Remote',
      description: 'Own our platform services.',
      extracted_skills: ['Python'],
      experience_required: '5+ years',
      source_url: 'https://boards.example.com/jobs/1',
      processing_status: 'NORMALIZED',
      discovered_at: '2026-01-01T00:00:00Z',
    }),
  ),
]

// ---------------------------------------------------------------------------
// Job Matching Service
// ---------------------------------------------------------------------------

export const matchingHandlers = [
  http.get(`${base}/jobs/:jobId/matches`, () =>
    HttpResponse.json({
      job_match_id: 'match-1',
      selected_profile_id: 'profile-1',
      selected_resume_id: 'resume-1',
      match_score: 0.87,
      matched_skills: ['Python'],
      missing_skills: ['Kafka'],
      recommendation: 'SHORTLIST',
      profile_scores: [
        {
          profile_id: 'profile-1',
          resume_id: 'resume-1',
          score: 0.87,
          matched_skills: ['Python'],
          missing_skills: ['Kafka'],
        },
      ],
      matched_at: '2026-01-01T00:00:00Z',
    }),
  ),
]

// ---------------------------------------------------------------------------
// Contact Discovery Service
// ---------------------------------------------------------------------------

export const contactHandlers = [
  http.get(`${base}/jobs/:jobId/contacts`, () =>
    HttpResponse.json([
      {
        id: 'contact-1',
        full_name: 'Jane Doe',
        headline: 'Senior Engineer',
        company: 'Acme Robotics',
        contact_type: 'PRACTITIONER',
        profile_url: 'https://example.com/jane',
        relevance_score: 0.9,
        status: 'RANKED',
      },
    ]),
  ),
  http.post(`${base}/jobs/:jobId/contacts/search`, () =>
    HttpResponse.json({ job_id: 'job-1', requested_at: '2026-01-01T00:00:00Z' }, { status: 202 }),
  ),
]

// ---------------------------------------------------------------------------
// Outreach Service
// ---------------------------------------------------------------------------

function outreachFixture(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 'outreach-1',
    job_id: 'job-1',
    contact_id: 'contact-1',
    channel: 'EMAIL',
    draft_message: 'Hi Jane, ...',
    final_message: null,
    status: 'PENDING_APPROVAL',
    generated_at: '2026-01-01T00:00:00Z',
    decided_at: null,
    sent_at: null,
    ...overrides,
  }
}

export const outreachHandlers = [
  http.get(`${base}/outreach`, () => HttpResponse.json([outreachFixture()])),
  http.get(`${base}/outreach/:outreachId`, () => HttpResponse.json(outreachFixture())),
  http.post(`${base}/outreach/:outreachId/approve`, () =>
    HttpResponse.json(outreachFixture({ status: 'APPROVED', decided_at: '2026-01-02T00:00:00Z' })),
  ),
  http.post(`${base}/outreach/:outreachId/reject`, () =>
    HttpResponse.json(outreachFixture({ status: 'REJECTED', decided_at: '2026-01-02T00:00:00Z' })),
  ),
  http.post(`${base}/outreach/:outreachId/edit`, () =>
    HttpResponse.json(outreachFixture({ status: 'EDITED', final_message: 'Edited message' })),
  ),
]

// ---------------------------------------------------------------------------
// Tracking Service
// ---------------------------------------------------------------------------

function applicationFixture(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 'app-1',
    job_id: 'job-1',
    user_id: 'user-1',
    company: 'Acme Robotics',
    title: 'Senior Backend Engineer',
    status: 'SHORTLISTED',
    selected_resume_id: 'resume-1',
    match_score: 0.87,
    matched_skills: ['Python'],
    missing_skills: ['Kafka'],
    discovered_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export const trackingHandlers = [
  http.get(`${base}/applications`, () => HttpResponse.json([applicationFixture()])),
  http.get(`${base}/applications/:applicationId`, () => HttpResponse.json(applicationFixture())),
  http.patch(`${base}/applications/:applicationId/status`, () =>
    HttpResponse.json(applicationFixture({ status: 'APPLIED' })),
  ),
  http.get(`${base}/applications/:applicationId/history`, () =>
    HttpResponse.json([
      {
        id: 'hist-1',
        application_id: 'app-1',
        from_status: null,
        to_status: 'DISCOVERED',
        changed_at: '2026-01-01T00:00:00Z',
        triggered_by: 'system',
        source_event_type: 'JOB_DISCOVERED',
        correlation_id: null,
      },
    ]),
  ),
]

export const handlers = [
  ...userHandlers,
  ...resumeHandlers,
  ...profileHandlers,
  ...jobHandlers,
  ...matchingHandlers,
  ...contactHandlers,
  ...outreachHandlers,
  ...trackingHandlers,
]
