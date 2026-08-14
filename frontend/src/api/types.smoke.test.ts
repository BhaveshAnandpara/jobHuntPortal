/**
 * Skeleton-step smoke test — proves the generated OpenAPI schema import
 * resolves and a value can be typed against it end to end (client.ts's
 * ApiError, and a plain object satisfying a generated response type). Not
 * a feature test — see docs/frontend/testing-strategy.md.
 */

import { describe, expect, it } from 'vitest'
import { ApiError, toApiError } from './client'
import type { JobMatchResponse, JobResponse } from './types'

describe('generated API types', () => {
  it('a plain object can satisfy a generated response type (JobResponse)', () => {
    const job: JobResponse = {
      id: 'job-1',
      user_id: 'user-1',
      company: 'Acme Robotics',
      title: 'Senior Mechanical Engineer',
      location: 'Remote',
      description: 'Own CAD models end to end.',
      extracted_skills: ['CAD'],
      experience_required: '5+ years',
      source_url: 'https://boards.example.com/jobs/1',
      processing_status: 'NORMALIZED',
      discovered_at: '2026-01-01T00:00:00Z',
    }

    expect(job.company).toBe('Acme Robotics')
  })

  it('JobMatchResponse carries profile_scores (Step 10.5 backend fix)', () => {
    const match: JobMatchResponse = {
      job_match_id: 'match-1',
      selected_profile_id: 'profile-1',
      selected_resume_id: 'resume-1',
      match_score: 0.9,
      matched_skills: [],
      missing_skills: [],
      recommendation: 'SHORTLIST',
      profile_scores: [
        {
          profile_id: 'profile-1',
          resume_id: 'resume-1',
          score: 0.9,
          matched_skills: [],
          missing_skills: [],
        },
      ],
      matched_at: '2026-01-01T00:00:00Z',
    }

    expect(match.profile_scores).toHaveLength(1)
  })

  it('toApiError normalizes an unknown thrown value', () => {
    const error = toApiError(new TypeError('boom'))
    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('UNKNOWN_ERROR')
  })
})
