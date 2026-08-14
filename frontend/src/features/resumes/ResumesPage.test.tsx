/**
 * `/resumes` behavior — see docs/frontend/routes.md#resumes--resume-management,
 * docs/frontend/user-flows.md#multiple-resumes-ux, and
 * docs/frontend/async-workflows.md#resume-parsing-progressive-disclosure.
 *
 * Covers: loading skeleton, empty state, upload success progressing
 * through the Uploaded -> Parsing -> Parsed badges via the existing
 * `useResumes` polling, upload failure (toast, no phantom row), delete
 * with confirmation, the safe upload-then-delete replace ordering (and
 * that a failed replacement upload leaves the original resume intact —
 * the most important test here), multiple resumes rendered at once, and a
 * profession-independent rendering check using a non-engineering profile
 * fixture.
 *
 * Owner: frontend-profile-agent.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { delay, http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { server } from '../../../tests/mocks/server'
import { API_BASE_URL } from '../../api/client'
import { ResumesPage } from './ResumesPage'
import { Toaster } from '../../components'
import { IdentityProvider } from '../../hooks/IdentityProvider'
import { setCurrentUserId } from '../../hooks/identity'

function renderResumesPage() {
  setCurrentUserId('user-1')
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <IdentityProvider>
        <Toaster />
        <ResumesPage />
      </IdentityProvider>
    </QueryClientProvider>,
  )
}

function makeFile(name = 'resume.pdf', content = '%PDF-1.4 fake') {
  return new File([content], name, { type: 'application/pdf' })
}

/**
 * Mimics real Chromium's `<input type="file">` semantics: `input.files`
 * returns the SAME live array-like reference on every access, and setting
 * `input.value = ''` truncates that reference in place. jsdom's default
 * behavior instead swaps in a brand new, distinct FileList object on
 * reset — which would let the Defect-1 FileList-lifecycle bug
 * (`ResumesPage.tsx`'s `handleUploadInputChange` reading
 * `event.target.files` AFTER `event.target.value = ''`) pass a naive test
 * undetected. Tests using this helper fail against the pre-fix handler and
 * pass against the fix (capturing `Array.from(event.target.files ?? [])`
 * before resetting `value`).
 */
function simulateLiveFileInput(input: HTMLInputElement, files: File[]) {
  const live: File[] = [...files]
  Object.defineProperty(input, 'files', {
    configurable: true,
    get: () => live,
  })
  Object.defineProperty(input, 'value', {
    configurable: true,
    get: () => (live.length ? `C:\\fakepath\\${live[0].name}` : ''),
    set: () => {
      live.length = 0
    },
  })
}

afterEach(() => {
  vi.useRealTimers()
})

describe('ResumesPage', () => {
  it('shows a loading skeleton before resumes arrive', async () => {
    server.use(
      http.get(`${API_BASE_URL}/resumes`, async () => {
        await delay(50)
        return HttpResponse.json([])
      }),
    )

    const { container } = renderResumesPage()

    expect(container.querySelector('.animate-pulse')).toBeTruthy()
    await waitFor(() => expect(screen.getByText('No resumes yet')).toBeInTheDocument())
  })

  it('shows an empty state with an upload action when there are no resumes', async () => {
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () => HttpResponse.json([])),
      http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.json([])),
    )

    renderResumesPage()

    expect(await screen.findByText('No resumes yet')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Upload resume' }).length).toBeGreaterThan(0)
  })

  it('displays multiple resumes simultaneously with distinct statuses', async () => {
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () =>
        HttpResponse.json([
          { id: 'resume-1', user_id: 'user-1', file_name: 'backend.pdf', status: 'PARSED', uploaded_at: '2026-01-01T00:00:00Z' },
          { id: 'resume-2', user_id: 'user-1', file_name: 'failed.pdf', status: 'PARSE_FAILED', uploaded_at: '2026-01-02T00:00:00Z' },
        ]),
      ),
      http.get(`${API_BASE_URL}/profiles`, () =>
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
          },
        ]),
      ),
    )

    renderResumesPage()

    expect(await screen.findByText('backend.pdf')).toBeInTheDocument()
    expect(screen.getByText('failed.pdf')).toBeInTheDocument()
    expect(screen.getByText('Parsed')).toBeInTheDocument()
    expect(screen.getByText('Parse failed')).toBeInTheDocument()
    // The failed resume has no derived profile — its row stays, but with no
    // profile summary, per state-machines.md's resume lifecycle.
    expect(screen.getByText(/could not be analyzed/i)).toBeInTheDocument()
  })

  it('progresses an uploaded resume through Uploaded -> Parsing -> Parsed via polling', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    let getCalls = 0
    const statusesByCall = ['UPLOADED', 'UPLOADED', 'PARSING', 'PARSED']
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () => {
        if (getCalls === 0) {
          getCalls += 1
          return HttpResponse.json([])
        }
        const status = statusesByCall[Math.min(getCalls, statusesByCall.length - 1)]
        getCalls += 1
        return HttpResponse.json([
          { id: 'resume-new', user_id: 'user-1', file_name: 'new.pdf', status, uploaded_at: '2026-01-03T00:00:00Z' },
        ])
      }),
      http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.json([])),
      http.post(`${API_BASE_URL}/resumes`, () =>
        HttpResponse.json(
          { id: 'resume-new', user_id: 'user-1', file_name: 'new.pdf', status: 'UPLOADED', uploaded_at: '2026-01-03T00:00:00Z' },
          { status: 202 },
        ),
      ),
    )

    renderResumesPage()
    await waitFor(() => expect(screen.getByText('No resumes yet')).toBeInTheDocument())

    const input = screen.getByLabelText('Upload resume files')
    fireEvent.change(input, { target: { files: [makeFile('new.pdf')] } })

    await waitFor(() => expect(screen.getByText('new.pdf')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('Uploaded')).toBeInTheDocument())

    await act(() => vi.advanceTimersByTimeAsync(2100))
    await waitFor(() => expect(screen.getByText('Parsing…')).toBeInTheDocument())

    await act(() => vi.advanceTimersByTimeAsync(2100))
    await waitFor(() => expect(screen.getByText('Parsed')).toBeInTheDocument())

    vi.useRealTimers()
  })

  it('Defect 1 regression: uploads the exact file chosen via the visible input even under a live, Chromium-like FileList', async () => {
    let uploadCalls = 0
    let uploadedFileName: string | null = null
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () => HttpResponse.json([])),
      http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.json([])),
      http.post(`${API_BASE_URL}/resumes`, async ({ request }) => {
        uploadCalls += 1
        const body = (await request.json()) as { file_name: string }
        uploadedFileName = body.file_name
        return HttpResponse.json(
          { id: 'resume-new', user_id: 'user-1', file_name: body.file_name, status: 'UPLOADED', uploaded_at: '2026-01-03T00:00:00Z' },
          { status: 202 },
        )
      }),
    )

    renderResumesPage()
    await waitFor(() => expect(screen.getByText('No resumes yet')).toBeInTheDocument())

    const input = screen.getByLabelText('Upload resume files') as HTMLInputElement
    simulateLiveFileInput(input, [makeFile('live-bug.pdf')])
    fireEvent.change(input)

    await waitFor(() => expect(uploadCalls).toBe(1))
    expect(uploadedFileName).toBe('live-bug.pdf')
  })

  it('Defect 1 regression: multi-file selection still uploads every selected file (input has `multiple`)', async () => {
    const uploadedFileNames: string[] = []
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () => HttpResponse.json([])),
      http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.json([])),
      http.post(`${API_BASE_URL}/resumes`, async ({ request }) => {
        const body = (await request.json()) as { file_name: string }
        uploadedFileNames.push(body.file_name)
        return HttpResponse.json(
          {
            id: `resume-${uploadedFileNames.length}`,
            user_id: 'user-1',
            file_name: body.file_name,
            status: 'UPLOADED',
            uploaded_at: '2026-01-03T00:00:00Z',
          },
          { status: 202 },
        )
      }),
    )

    renderResumesPage()
    await waitFor(() => expect(screen.getByText('No resumes yet')).toBeInTheDocument())

    const input = screen.getByLabelText('Upload resume files') as HTMLInputElement
    simulateLiveFileInput(input, [makeFile('one.pdf'), makeFile('two.pdf')])
    fireEvent.change(input)

    await waitFor(() => expect(uploadedFileNames.length).toBe(2))
    expect(uploadedFileNames.slice().sort()).toEqual(['one.pdf', 'two.pdf'])
  })

  it('Defect 3 regression: shows the derived profile once a tracked resume reaches PARSED, with no reload/navigation', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    let getCalls = 0
    let sawParsed = false
    const statusesByCall = ['UPLOADED', 'UPLOADED', 'PARSING', 'PARSED']
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () => {
        if (getCalls === 0) {
          getCalls += 1
          return HttpResponse.json([])
        }
        const status = statusesByCall[Math.min(getCalls, statusesByCall.length - 1)]
        getCalls += 1
        if (status === 'PARSED') sawParsed = true
        return HttpResponse.json([
          { id: 'resume-new', user_id: 'user-1', file_name: 'new.pdf', status, uploaded_at: '2026-01-03T00:00:00Z' },
        ])
      }),
      http.get(`${API_BASE_URL}/profiles`, () =>
        HttpResponse.json(
          sawParsed
            ? [
                {
                  profile_id: 'profile-1',
                  resume_id: 'resume-new',
                  user_id: 'user-1',
                  title: 'Data Analyst',
                  summary: 'Analyst summary',
                  skills: ['SQL'],
                  experience_years: 3,
                  seniority: 'Mid',
                  education: [],
                },
              ]
            : [],
        ),
      ),
      http.post(`${API_BASE_URL}/resumes`, () =>
        HttpResponse.json(
          { id: 'resume-new', user_id: 'user-1', file_name: 'new.pdf', status: 'UPLOADED', uploaded_at: '2026-01-03T00:00:00Z' },
          { status: 202 },
        ),
      ),
    )

    renderResumesPage()
    await waitFor(() => expect(screen.getByText('No resumes yet')).toBeInTheDocument())

    const input = screen.getByLabelText('Upload resume files')
    fireEvent.change(input, { target: { files: [makeFile('new.pdf')] } })

    await waitFor(() => expect(screen.getByText('new.pdf')).toBeInTheDocument())

    await act(() => vi.advanceTimersByTimeAsync(2100))
    await act(() => vi.advanceTimersByTimeAsync(2100))

    await waitFor(() => expect(screen.getByText('Parsed')).toBeInTheDocument())
    // Profile section appears purely from the polling-driven refetch — no
    // reload/navigation happens in this test.
    await waitFor(() => expect(screen.getByText('Data Analyst')).toBeInTheDocument())

    vi.useRealTimers()
  })

  it('shows a toast on upload failure and creates no phantom row', async () => {
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () =>
        HttpResponse.json([
          { id: 'resume-1', user_id: 'user-1', file_name: 'existing.pdf', status: 'PARSED', uploaded_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
      http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.json([])),
      http.post(`${API_BASE_URL}/resumes`, () =>
        HttpResponse.json({ detail: { code: 'VALIDATION_ERROR', message: 'Unsupported file type' } }, { status: 400 }),
      ),
    )

    renderResumesPage()
    expect(await screen.findByText('existing.pdf')).toBeInTheDocument()

    const input = screen.getByLabelText('Upload resume files')
    fireEvent.change(input, { target: { files: [makeFile('bad.exe')] } })

    expect(await screen.findByText(/bad\.exe.*failed to upload/i)).toBeInTheDocument()
    // No phantom row for the failed file — only the pre-existing resume renders.
    expect(screen.queryByText('bad.exe')).not.toBeInTheDocument()
    expect(screen.getByText('existing.pdf')).toBeInTheDocument()
  })

  it('deletes a resume after confirming via the dialog', async () => {
    let deleteCalled = false
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () => {
        return HttpResponse.json(
          deleteCalled
            ? []
            : [{ id: 'resume-1', user_id: 'user-1', file_name: 'existing.pdf', status: 'PARSED', uploaded_at: '2026-01-01T00:00:00Z' }],
        )
      }),
      http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.json([])),
      http.delete(`${API_BASE_URL}/resumes/:resumeId`, () => {
        deleteCalled = true
        return new HttpResponse(null, { status: 204 })
      }),
    )

    renderResumesPage()
    expect(await screen.findByText('existing.pdf')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete resume' })

    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(await screen.findByText('Deleted "existing.pdf".')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No resumes yet')).toBeInTheDocument())
  })

  it('does not delete a resume when the confirmation dialog is cancelled', async () => {
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () =>
        HttpResponse.json([
          { id: 'resume-1', user_id: 'user-1', file_name: 'existing.pdf', status: 'PARSED', uploaded_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
      http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.json([])),
    )

    renderResumesPage()
    expect(await screen.findByText('existing.pdf')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(await screen.findByRole('dialog', { name: 'Delete resume' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText('existing.pdf')).toBeInTheDocument()
  })

  it('Step 12 regression: closing the delete-confirmation dialog (Cancel) returns focus to the exact Delete button that opened it, even with multiple rows', async () => {
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () =>
        HttpResponse.json([
          { id: 'resume-1', user_id: 'user-1', file_name: 'first.pdf', status: 'PARSED', uploaded_at: '2026-01-01T00:00:00Z' },
          { id: 'resume-2', user_id: 'user-1', file_name: 'second.pdf', status: 'PARSED', uploaded_at: '2026-01-02T00:00:00Z' },
        ]),
      ),
      http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.json([])),
    )

    renderResumesPage()
    expect(await screen.findByText('second.pdf')).toBeInTheDocument()

    // Two rows, two Delete buttons sharing one Dialog instance — clicking
    // the SECOND row's button must return focus to that specific button,
    // not the first one, once the dialog closes.
    const secondRow = screen.getByText('second.pdf').closest('li') as HTMLElement
    const secondDeleteButton = within(secondRow).getByRole('button', { name: 'Delete' })

    await userEvent.click(secondDeleteButton)
    await screen.findByRole('dialog', { name: 'Delete resume' })

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(secondDeleteButton).toHaveFocus()
  })

  it('replace: uploads the new resume first, waits for it to reach PARSED via polling, and only then deletes the old one', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    const oldResume = { id: 'resume-old', user_id: 'user-1', file_name: 'old.pdf', status: 'PARSED', uploaded_at: '2026-01-01T00:00:00Z' }
    let getCalls = 0
    let deleteCalls = 0
    let uploadCalls = 0
    let deleted = false
    const statusesByCall = ['UPLOADED', 'UPLOADED', 'PARSING', 'PARSED']
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () => {
        const base = deleted ? [] : [oldResume]
        if (getCalls === 0) {
          getCalls += 1
          return HttpResponse.json(base)
        }
        const status = statusesByCall[Math.min(getCalls, statusesByCall.length - 1)]
        getCalls += 1
        return HttpResponse.json([
          ...base,
          { id: 'resume-new', user_id: 'user-1', file_name: 'new.pdf', status, uploaded_at: '2026-01-02T00:00:00Z' },
        ])
      }),
      http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.json([])),
      http.post(`${API_BASE_URL}/resumes`, () => {
        uploadCalls += 1
        return HttpResponse.json(
          { id: 'resume-new', user_id: 'user-1', file_name: 'new.pdf', status: 'UPLOADED', uploaded_at: '2026-01-02T00:00:00Z' },
          { status: 202 },
        )
      }),
      http.delete(`${API_BASE_URL}/resumes/:resumeId`, () => {
        deleteCalls += 1
        deleted = true
        return new HttpResponse(null, { status: 204 })
      }),
    )

    renderResumesPage()
    expect(await screen.findByText('old.pdf')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Replace' }))
    const replaceInput = screen.getByLabelText('Replacement resume file')
    fireEvent.change(replaceInput, { target: { files: [makeFile('new.pdf')] } })

    await waitFor(() => expect(uploadCalls).toBe(1))
    // Critical Defect-2 assertion: a 202 accept must NOT delete the old
    // resume yet — it's only "accepted for processing", not parsed.
    expect(deleteCalls).toBe(0)
    expect(screen.getByText('old.pdf')).toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(2100)) // -> PARSING
    expect(deleteCalls).toBe(0)

    await act(() => vi.advanceTimersByTimeAsync(2100)) // -> PARSED

    await waitFor(() => expect(deleteCalls).toBe(1))
    expect(await screen.findByText('Replaced "old.pdf".')).toBeInTheDocument()

    vi.useRealTimers()
  })

  it('replace safety: if the new resume reaches PARSE_FAILED, the old resume is never deleted', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })

    const oldResume = { id: 'resume-old', user_id: 'user-1', file_name: 'old.pdf', status: 'PARSED', uploaded_at: '2026-01-01T00:00:00Z' }
    let getCalls = 0
    let deleteCalls = 0
    let uploadCalls = 0
    const statusesByCall = ['UPLOADED', 'UPLOADED', 'PARSING', 'PARSE_FAILED']
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () => {
        if (getCalls === 0) {
          getCalls += 1
          return HttpResponse.json([oldResume])
        }
        const status = statusesByCall[Math.min(getCalls, statusesByCall.length - 1)]
        getCalls += 1
        return HttpResponse.json([
          oldResume,
          { id: 'resume-new', user_id: 'user-1', file_name: 'new.pdf', status, uploaded_at: '2026-01-02T00:00:00Z' },
        ])
      }),
      http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.json([])),
      http.post(`${API_BASE_URL}/resumes`, () => {
        uploadCalls += 1
        return HttpResponse.json(
          { id: 'resume-new', user_id: 'user-1', file_name: 'new.pdf', status: 'UPLOADED', uploaded_at: '2026-01-02T00:00:00Z' },
          { status: 202 },
        )
      }),
      http.delete(`${API_BASE_URL}/resumes/:resumeId`, () => {
        // Must never be hit in this scenario — the assertion at the end
        // double-checks the count, but registering the handler mirrors
        // what a real backend would expose.
        deleteCalls += 1
        return new HttpResponse(null, { status: 204 })
      }),
    )

    renderResumesPage()
    expect(await screen.findByText('old.pdf')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Replace' }))
    const replaceInput = screen.getByLabelText('Replacement resume file')
    fireEvent.change(replaceInput, { target: { files: [makeFile('new.pdf')] } })

    await waitFor(() => expect(uploadCalls).toBe(1))
    expect(deleteCalls).toBe(0)

    await act(() => vi.advanceTimersByTimeAsync(2100)) // -> PARSING
    await act(() => vi.advanceTimersByTimeAsync(2100)) // -> PARSE_FAILED

    // "was not removed" is unique to the replace-failure toast — the
    // per-row PARSE_FAILED message ("could not be analyzed...") also
    // renders for the new resume's own row, so a broader match would be
    // ambiguous.
    await waitFor(() => expect(screen.getByText(/was not removed/i)).toBeInTheDocument())
    // The critical assertion: DELETE must never fire, and the original
    // resume is still present, still shown as Parsed.
    expect(deleteCalls).toBe(0)
    expect(screen.getByText('old.pdf')).toBeInTheDocument()
    expect(screen.getByText('Parsed')).toBeInTheDocument()

    vi.useRealTimers()
  })

  it('replace safety: a failed upload never deletes the old resume, leaving it intact', async () => {
    let deleteCalls = 0
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () =>
        HttpResponse.json([
          { id: 'resume-old', user_id: 'user-1', file_name: 'old.pdf', status: 'PARSED', uploaded_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
      http.get(`${API_BASE_URL}/profiles`, () => HttpResponse.json([])),
      http.post(`${API_BASE_URL}/resumes`, () =>
        HttpResponse.json({ detail: { code: 'VALIDATION_ERROR', message: 'File too large' } }, { status: 400 }),
      ),
      http.delete(`${API_BASE_URL}/resumes/:resumeId`, () => {
        deleteCalls += 1
        return new HttpResponse(null, { status: 204 })
      }),
    )

    renderResumesPage()
    expect(await screen.findByText('old.pdf')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Replace' }))
    const replaceInput = screen.getByLabelText('Replacement resume file')
    fireEvent.change(replaceInput, { target: { files: [makeFile('new.pdf')] } })

    expect(await screen.findByText(/was not removed/i)).toBeInTheDocument()
    // The critical assertion: DELETE must never have been called, and the
    // original resume row is still present and rendered normally.
    expect(deleteCalls).toBe(0)
    expect(screen.getByText('old.pdf')).toBeInTheDocument()
    expect(screen.getByText('Parsed')).toBeInTheDocument()
  })

  it('renders a non-engineering profile with no hard-coded, profession-specific copy', async () => {
    server.use(
      http.get(`${API_BASE_URL}/resumes`, () =>
        HttpResponse.json([
          { id: 'resume-1', user_id: 'user-1', file_name: 'recruiter-resume.pdf', status: 'PARSED', uploaded_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
      http.get(`${API_BASE_URL}/profiles`, () =>
        HttpResponse.json([
          {
            profile_id: 'profile-1',
            resume_id: 'resume-1',
            user_id: 'user-1',
            title: 'Senior Technical Recruiter',
            summary: 'Talent acquisition specialist focused on full-cycle recruiting for engineering teams.',
            skills: ['Sourcing', 'ATS Administration', 'Stakeholder Management'],
            experience_years: 8,
            seniority: 'Senior',
            education: [],
          },
        ]),
      ),
    )

    renderResumesPage()

    expect(await screen.findByText('Senior Technical Recruiter')).toBeInTheDocument()
    expect(screen.getByText(/Talent acquisition specialist/)).toBeInTheDocument()
    expect(screen.getByText('Sourcing')).toBeInTheDocument()
    expect(screen.getByText('ATS Administration')).toBeInTheDocument()
    expect(screen.getByText('Stakeholder Management')).toBeInTheDocument()
    // No profession-specific hard-coded section labels anywhere on the page.
    expect(screen.queryByText(/coding skills/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/programming languages/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/tech stack/i)).not.toBeInTheDocument()
  })
})
