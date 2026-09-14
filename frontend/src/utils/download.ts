/**
 * Handing an already-fetched file to the browser's own download machinery.
 *
 * Why this exists at all: every download route in this backend is
 * authenticated with a Bearer token, and a plain `<a href="/…/download">`
 * cannot carry one — the browser would send an unauthenticated request and
 * get a 401. So the bytes are fetched through `apiClient.file()` (which
 * does carry the header, and normalizes errors like every other call), and
 * this turns the resulting `Blob` into a save prompt.
 *
 * The `createObjectURL` + synthetic `<a download>` click is the standard
 * way to do that; there is no API for "save these bytes" that doesn't go
 * through an anchor. The object URL is revoked on the next tick rather than
 * immediately after `click()`, because some browsers abort a download whose
 * source URL is revoked in the same task.
 *
 * Counterpart to `utils/base64.ts` on the upload side — this file is the
 * one place a download is triggered, so no feature reimplements it.
 */

export function saveFileToDisk(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  anchor.rel = 'noopener'
  // Appended before clicking: a detached anchor's click is ignored in some
  // browsers (notably Firefox).
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
}
