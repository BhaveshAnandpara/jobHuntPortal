/**
 * `CreateResumeRequest.file_content` is a JSON `bytes` field (Pydantic
 * decodes it from a base64 string), not a `multipart/form-data` upload —
 * see docs/frontend/api-mapping.md#resumeprofile-service. This is the one
 * place that encoding happens; `src/api/resumes.ts` calls this, feature
 * code never re-implements it.
 */
export async function fileToBase64(file: File): Promise<string> {
  const buffer = await file.arrayBuffer()
  let binary = ''
  const bytes = new Uint8Array(buffer)
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary)
}
