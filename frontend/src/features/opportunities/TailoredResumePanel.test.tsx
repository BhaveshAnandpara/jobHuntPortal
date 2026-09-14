/**
 * Component tests for `TailoredResumePanel` — docs/tailored-resume/TECH.md
 * Task 7's "component tests covering all four states", plus the three the
 * panel actually has beyond those four (stage-locked, already-generated from
 * a previous session, and a failed generate that isn't the gate).
 *
 * Follows `ContactsPanel.test.tsx`'s pattern: real MSW responses through the
 * real API client, a fresh `QueryClient` per render, nothing about the panel
 * itself mocked. `MemoryRouter` is present because the gated state renders a
 * `<Link to="/settings">`.
 *
 * Owner: frontend-opportunities-agent.
 */

import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { delay, http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../../tests/mocks/server'
import { API_BASE_URL } from '../../api/client'
import { createTestQueryClient } from '../../api/test-utils'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { setToken } from '../../hooks/identity'
import { mintTestToken } from '../../../tests/support/jwt'
import { TailoredResumePanel } from './TailoredResumePanel'

function renderPanel(props: { jobId?: string; isAvailable?: boolean } = {}) {
  const queryClient = createTestQueryClient()
  return render(
    <IdentityProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <TailoredResumePanel jobId={props.jobId ?? 'job-1'} isAvailable={props.isAvailable ?? true} />
        </MemoryRouter>
      </QueryClientProvider>
    </IdentityProvider>,
  )
}

/** `GET /jobs/:id/tailored-resume` answering "one already exists". */
function existingRecordHandler(overrides: Record<string, unknown> = {}) {
  return http.get(`${API_BASE_URL}/jobs/:jobId/tailored-resume`, () =>
    HttpResponse.json({
      id: 'tailored-1',
      base_score: 0.62,
      achieved_score: 0.81,
      created_at: '2026-03-01T09:00:00Z',
      ...overrides,
    }),
  )
}

/**
 * `POST /jobs/:id/tailored-resume` answering with a full skill breakdown.
 * Defaults describe an improved run whose tailoring genuinely moved one skill
 * from missing to matched while two others matched all along — the case the
 * set difference exists to tell apart.
 */
function generateHandler(overrides: Record<string, unknown> = {}) {
  return http.post(`${API_BASE_URL}/jobs/:jobId/tailored-resume`, () =>
    HttpResponse.json(
      {
        improved: true,
        base_score: 0.62,
        achieved_score: 0.81,
        id: 'tailored-1',
        message: null,
        base_matched_skills: ['Triage', 'Patient Assessment'],
        base_missing_skills: ['Ventilator Management', 'ACLS Certification'],
        tailored_matched_skills: ['Triage', 'Patient Assessment', 'Ventilator Management'],
        tailored_missing_skills: ['ACLS Certification'],
        ...overrides,
      },
      { status: overrides.improved === false ? 200 : 201 },
    ),
  )
}

/**
 * The skill chips under one section heading, in order. Reads the rendered
 * list rather than asserting on loose text so a skill appearing in the *other*
 * section can never satisfy the assertion by accident.
 */
function skillsUnder(label: string): string[] {
  const heading = screen.getByText(label)
  const list = heading.parentElement?.querySelector('ul')
  return Array.from(list?.querySelectorAll('li') ?? []).map((item) => item.textContent ?? '')
}

async function clickGenerate() {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: /generate tailored resume/i }))
}

/** The unset AI-provider config — a 404, this app's "not configured yet". */
function noLlmConfigHandler() {
  return http.get(`${API_BASE_URL}/users/me/llm-config`, () =>
    HttpResponse.json(
      { detail: { code: 'NOT_FOUND', message: 'no llm config' } },
      { status: 404 },
    ),
  )
}

beforeEach(() => {
  setToken(mintTestToken('user-1'))
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('TailoredResumePanel — before matching has run', () => {
  it('says there is nothing to tailor against, offers no button, and asks the API nothing', async () => {
    let requests = 0
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId/tailored-resume`, () => {
        requests += 1
        return new HttpResponse(null, { status: 404 })
      }),
    )

    const { container } = renderPanel({ isAvailable: false })

    expect(screen.getByText('Nothing to tailor against yet')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /generate tailored resume/i })).not.toBeInTheDocument()
    // A placeholder, not a skeleton: no request is in flight to justify one.
    expect(container.querySelectorAll('.animate-pulse')).toHaveLength(0)
    await waitFor(() => expect(requests).toBe(0))
  })
})

describe('TailoredResumePanel — no AI provider key', () => {
  it('renders the shared gated empty state pointing at Settings, with no Generate button', async () => {
    server.use(noLlmConfigHandler())
    renderPanel()

    expect(await screen.findByText('Add your AI provider key in Settings')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Add your AI provider key in Settings to generate a tailored resume for this job.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to Settings' })).toHaveAttribute('href', '/settings')
    expect(screen.queryByRole('button', { name: /generate tailored resume/i })).not.toBeInTheDocument()
  })

  it('renders the same gated state if the POST itself is rejected by the gate', async () => {
    // The readiness check fails open while it is loading or unavailable, so
    // the button can legitimately be clicked and then rejected server-side.
    server.use(
      http.post(`${API_BASE_URL}/jobs/:jobId/tailored-resume`, () =>
        HttpResponse.json(
          {
            detail: {
              code: 'LLM_NOT_CONFIGURED',
              message: 'Add your AI provider key in Settings to use this feature.',
            },
          },
          { status: 400 },
        ),
      ),
    )
    renderPanel()

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /generate tailored resume/i }))

    expect(await screen.findByText('Add your AI provider key in Settings')).toBeInTheDocument()
    // The gate is not a fault: it must not read as an error the user could retry.
    expect(screen.queryByRole('button', { name: /^retry$/i })).not.toBeInTheDocument()
  })
})

describe('TailoredResumePanel — idle', () => {
  it('explains what generation does, states the 5-point bar, and offers the button', async () => {
    renderPanel()

    expect(await screen.findByRole('button', { name: /generate tailored resume/i })).toBeEnabled()
    expect(
      screen.getByText(/only create a document if it would meaningfully improve your match score/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/at least 5 points/i)).toBeInTheDocument()
    // "None generated yet" is a 404 and must never surface as an error.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('TailoredResumePanel — generating', () => {
  it('says the work is slow and roughly how long, rather than spinning silently', async () => {
    server.use(
      http.post(`${API_BASE_URL}/jobs/:jobId/tailored-resume`, async () => {
        await delay('infinite')
        return HttpResponse.json({})
      }),
    )
    renderPanel()

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /generate tailored resume/i }))

    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent(/this can take up to a minute/i)
    expect(status).toHaveTextContent(/generating and scoring a tailored version/i)
    expect(screen.queryByRole('button', { name: /generate tailored resume/i })).not.toBeInTheDocument()
  })
})

describe('TailoredResumePanel — generated successfully', () => {
  it('shows the score it started from, the score it reached, and a download button', async () => {
    renderPanel()

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /generate tailored resume/i }))

    // Same percentage formatting MatchPanel uses (`formatScorePercent`).
    expect(await screen.findByText('81%')).toBeInTheDocument()
    expect(screen.getByText('62%')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /download tailored resume \(\.docx\)/i }),
    ).toBeInTheDocument()
  })

  it('hands the file to the browser when Download is clicked', async () => {
    const createObjectURL = vi.fn(() => 'blob:mock-url')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }))
    const clicked: HTMLAnchorElement[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this)
    })
    server.use(existingRecordHandler())
    renderPanel()

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /download tailored resume/i }))

    await waitFor(() => expect(clicked).toHaveLength(1))
    expect(createObjectURL).toHaveBeenCalledTimes(1)
    expect(clicked[0].download).toBe('tailored-resume-job-1.docx')
  })

  it('reports a failed download inline without losing the result', async () => {
    server.use(
      existingRecordHandler(),
      http.get(`${API_BASE_URL}/jobs/:jobId/tailored-resume/download`, () =>
        HttpResponse.json(
          {
            detail: {
              code: 'NOT_FOUND',
              message: 'the tailored resume document for job job-1 is no longer available',
            },
          },
          { status: 404 },
        ),
      ),
    )
    renderPanel()

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /download tailored resume/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'the tailored resume document for job job-1 is no longer available',
    )
    // The scores are still on screen — a failed download isn't a lost document.
    expect(screen.getByText('81%')).toBeInTheDocument()
  })
})

describe('TailoredResumePanel — no improvement', () => {
  it('says the current resume is already well-suited, shows the scores, and offers no retry', async () => {
    server.use(
      http.post(`${API_BASE_URL}/jobs/:jobId/tailored-resume`, () =>
        HttpResponse.json(
          {
            improved: false,
            base_score: 0.88,
            achieved_score: 0.89,
            id: null,
            message: 'Your current resume is already well-suited for this job.',
          },
          { status: 200 },
        ),
      ),
    )
    renderPanel()

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /generate tailored resume/i }))

    expect(
      await screen.findByText('Your current resume is already well-suited for this job.'),
    ).toBeInTheDocument()
    expect(screen.getByText(/scored 89% against this job's 88%/i)).toBeInTheDocument()

    // Nothing has changed about the job or the profile, so re-running would
    // spend the user's LLM quota to reach the same answer.
    expect(screen.queryByRole('button', { name: /generate tailored resume/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^retry$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /download/i })).not.toBeInTheDocument()
    // A legitimate outcome, not a failure.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('TailoredResumePanel — already generated in a previous session', () => {
  it('shows the scores and the download button on mount, without clicking Generate', async () => {
    server.use(existingRecordHandler())
    renderPanel()

    expect(await screen.findByText('81%')).toBeInTheDocument()
    expect(screen.getByText('62%')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /download tailored resume \(\.docx\)/i }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /generate tailored resume/i })).not.toBeInTheDocument()
  })

  it('still offers the download when the AI provider key has since been removed', async () => {
    // Only generation is gated server-side; retrieving an existing document
    // spends no quota, so the gate must not hide it.
    server.use(existingRecordHandler(), noLlmConfigHandler())
    renderPanel()

    expect(await screen.findByText('81%')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /download tailored resume/i })).toBeInTheDocument()
    expect(screen.queryByText('Add your AI provider key in Settings')).not.toBeInTheDocument()
  })

  it('shows a skeleton, not an empty state, while that lookup is in flight', () => {
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId/tailored-resume`, async () => {
        await delay('infinite')
        return HttpResponse.json({})
      }),
    )
    const { container } = renderPanel()

    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /generate tailored resume/i })).not.toBeInTheDocument()
  })
})

describe('TailoredResumePanel — generation errors', () => {
  it('surfaces the server message and retries the same call on demand', async () => {
    let attempts = 0
    server.use(
      http.post(`${API_BASE_URL}/jobs/:jobId/tailored-resume`, () => {
        attempts += 1
        if (attempts === 1) {
          return HttpResponse.json(
            {
              detail: {
                code: 'NOT_FOUND',
                message: 'job job-1 has not been matched yet',
              },
            },
            { status: 404 },
          )
        }
        return HttpResponse.json(
          { improved: true, base_score: 0.62, achieved_score: 0.81, id: 'tailored-1', message: null },
          { status: 201 },
        )
      }),
    )
    renderPanel()

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /generate tailored resume/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('job job-1 has not been matched yet')

    await user.click(screen.getByRole('button', { name: /retry/i }))

    expect(await screen.findByText('81%')).toBeInTheDocument()
    expect(attempts).toBe(2)
  })

  it('renders the skill breakdown, not a crash, when the response omits the four lists', async () => {
    // The fields are defaulted server-side, so a body without them is a real
    // shape (anything predating the breakdown). The panel must treat it as
    // "no breakdown to show" rather than dereferencing undefined.
    server.use(
      http.post(`${API_BASE_URL}/jobs/:jobId/tailored-resume`, () =>
        HttpResponse.json(
          { improved: true, base_score: 0.62, achieved_score: 0.81, id: 'tailored-1', message: null },
          { status: 201 },
        ),
      ),
    )
    renderPanel()
    await clickGenerate()

    expect(await screen.findByText('81%')).toBeInTheDocument()
    expect(screen.queryByText('Skills tailoring emphasized')).not.toBeInTheDocument()
    expect(screen.queryByText('Still missing')).not.toBeInTheDocument()
  })

  it('keeps the Generate button usable when the "already generated?" lookup fails outright', async () => {
    server.use(
      http.get(`${API_BASE_URL}/jobs/:jobId/tailored-resume`, () =>
        HttpResponse.json(
          { detail: { code: 'UNKNOWN_ERROR', message: 'Tailoring service unavailable.' } },
          { status: 500 },
        ),
      ),
    )
    renderPanel()

    // A failed lookup is reported, but it must not block the primary action:
    // a 5xx on "does one exist?" says nothing about whether one can be made.
    expect(await screen.findByRole('alert')).toHaveTextContent('Tailoring service unavailable.')
    expect(screen.getByRole('button', { name: /generate tailored resume/i })).toBeEnabled()
  })
})

describe('TailoredResumePanel — skill breakdown', () => {
  it('shows what tailoring emphasized and what is still missing on an improved run', async () => {
    server.use(generateHandler())
    renderPanel()
    await clickGenerate()

    expect(await screen.findByText('Skills tailoring emphasized')).toBeInTheDocument()
    expect(skillsUnder('Skills tailoring emphasized')).toEqual(['Ventilator Management'])
    expect(skillsUnder('Still missing')).toEqual(['ACLS Certification'])

    // Added alongside the score comparison, not in place of it.
    expect(screen.getByText('81%')).toBeInTheDocument()
    expect(screen.getByText('62%')).toBeInTheDocument()
  })

  it('excludes skills that already matched before tailoring from "emphasized"', async () => {
    // `Triage` and `Patient Assessment` are in both base and tailored matched
    // lists: they were already being credited, so tailoring did not emphasize
    // them and claiming otherwise would overstate what the run did.
    server.use(generateHandler())
    renderPanel()
    await clickGenerate()

    await screen.findByText('Skills tailoring emphasized')
    expect(skillsUnder('Skills tailoring emphasized')).not.toContain('Triage')
    expect(skillsUnder('Skills tailoring emphasized')).not.toContain('Patient Assessment')
    // Nothing else on the panel renders the base matched list, so a
    // still-credited skill should be absent from the page entirely.
    expect(screen.queryByText('Triage')).not.toBeInTheDocument()
    expect(screen.queryByText('Patient Assessment')).not.toBeInTheDocument()
  })

  it('shows both sections on a no-improvement run too', async () => {
    // Nothing was generated, but the run still measured what matches and what
    // does not — the most actionable thing this outcome has to offer.
    server.use(
      generateHandler({
        improved: false,
        base_score: 0.88,
        achieved_score: 0.89,
        id: null,
        message: 'Your current resume is already well-suited for this job.',
        base_matched_skills: ['Budget Forecasting', 'Stakeholder Reporting'],
        base_missing_skills: ['IFRS Reporting', 'SAP S/4HANA'],
        tailored_matched_skills: ['Budget Forecasting', 'Stakeholder Reporting', 'IFRS Reporting'],
        tailored_missing_skills: ['SAP S/4HANA'],
      }),
    )
    renderPanel()
    await clickGenerate()

    expect(
      await screen.findByText('Your current resume is already well-suited for this job.'),
    ).toBeInTheDocument()
    expect(skillsUnder('Skills tailoring emphasized')).toEqual(['IFRS Reporting'])
    expect(skillsUnder('Still missing')).toEqual(['SAP S/4HANA'])

    // The existing copy for this outcome is untouched.
    expect(screen.getByText(/scored 89% against this job's 88%/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /download/i })).not.toBeInTheDocument()
  })

  it('omits "Still missing" entirely when nothing is missing', async () => {
    server.use(
      generateHandler({
        base_matched_skills: ['Kiln Operation'],
        tailored_matched_skills: ['Kiln Operation', 'Glaze Chemistry'],
        tailored_missing_skills: [],
      }),
    )
    renderPanel()
    await clickGenerate()

    expect(await screen.findByText('Skills tailoring emphasized')).toBeInTheDocument()
    expect(skillsUnder('Skills tailoring emphasized')).toEqual(['Glaze Chemistry'])
    // A fully-matched profile must not get a hollow heading over nothing.
    expect(screen.queryByText('Still missing')).not.toBeInTheDocument()
  })

  it('omits "Skills tailoring emphasized" when tailoring moved nothing new into matched', async () => {
    server.use(
      generateHandler({
        base_matched_skills: ['Kiln Operation', 'Glaze Chemistry'],
        tailored_matched_skills: ['Kiln Operation', 'Glaze Chemistry'],
        tailored_missing_skills: ['Mould Making'],
      }),
    )
    renderPanel()
    await clickGenerate()

    expect(await screen.findByText('Still missing')).toBeInTheDocument()
    expect(skillsUnder('Still missing')).toEqual(['Mould Making'])
    expect(screen.queryByText('Skills tailoring emphasized')).not.toBeInTheDocument()
  })

  it('renders neither heading when both lists come back empty', async () => {
    server.use(
      generateHandler({
        base_matched_skills: ['Kiln Operation'],
        tailored_matched_skills: ['Kiln Operation'],
        tailored_missing_skills: [],
      }),
    )
    renderPanel()
    await clickGenerate()

    expect(await screen.findByText('81%')).toBeInTheDocument()
    expect(screen.queryByText('Skills tailoring emphasized')).not.toBeInTheDocument()
    expect(screen.queryByText('Still missing')).not.toBeInTheDocument()
  })

  it('shows no breakdown for a document recovered from a previous session', async () => {
    // `GET` returns the stored record — scores and a timestamp, no skill
    // lists — so there is nothing to show rather than something to invent.
    server.use(existingRecordHandler())
    renderPanel()

    expect(await screen.findByText('81%')).toBeInTheDocument()
    expect(screen.queryByText('Skills tailoring emphasized')).not.toBeInTheDocument()
    expect(screen.queryByText('Still missing')).not.toBeInTheDocument()
  })
})
