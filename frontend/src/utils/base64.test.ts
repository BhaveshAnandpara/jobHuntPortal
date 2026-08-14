/**
 * `fileToBase64` is the one place resume-upload's wire-format encoding
 * happens — see docs/frontend/api-mapping.md's wire-format note
 * (`CreateResumeRequest.file_content` is a JSON `bytes` field, decoded from
 * base64, not a `multipart/form-data` upload).
 */

import { describe, expect, it } from 'vitest'
import { fileToBase64 } from './base64'

describe('fileToBase64', () => {
  it('encodes simple ASCII text content as base64', async () => {
    const file = new File(['hello world'], 'resume.txt', { type: 'text/plain' })

    const encoded = await fileToBase64(file)

    expect(encoded).toBe(btoa('hello world'))
    expect(atob(encoded)).toBe('hello world')
  })

  it('encodes an empty file as an empty base64 string', async () => {
    const file = new File([], 'empty.txt')

    const encoded = await fileToBase64(file)

    expect(encoded).toBe('')
  })

  it('encodes arbitrary binary content byte-for-byte (not just UTF-8 text)', async () => {
    const bytes = new Uint8Array([0, 1, 2, 253, 254, 255])
    const file = new File([bytes], 'resume.pdf', { type: 'application/pdf' })

    const encoded = await fileToBase64(file)
    const decodedBinary = atob(encoded)
    const decodedBytes = Uint8Array.from(decodedBinary, (char) => char.charCodeAt(0))

    expect(Array.from(decodedBytes)).toEqual(Array.from(bytes))
  })

  it('produces a string with no data: URI prefix or other wrapping', async () => {
    const file = new File(['%PDF-1.4 fake pdf bytes'], 'resume.pdf', { type: 'application/pdf' })

    const encoded = await fileToBase64(file)

    expect(encoded.startsWith('data:')).toBe(false)
    expect(/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)).toBe(true)
  })
})
