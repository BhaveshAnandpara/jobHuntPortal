"""Local filesystem storage for generated tailored resume documents.

Mirrors `profiles/storage.py::LocalResumeStorage` deliberately and exactly
(docs/tailored-resume/TECH.md Task 5): same `save`/`read`/`delete` surface,
same per-user subdirectory layout, same "`storage_uri` values produced here
are plain local filesystem paths" contract, same env-var-or-explicit-
`base_dir` configuration. Uploaded resumes and generated ones are two
directories under `data/`, not two different storage abstractions — so
replacing either with an object store later is the same piece of work.

Kept separate from `LocalResumeStorage` rather than parameterized onto it
because the two live in different components: `profiles/storage.py` is
Resume/Profile Service-internal (see its module docstring), and
docs/architecture/ownership.md forbids reaching into another component's
internals. The duplication is ~20 lines of `pathlib` and buys both
components the freedom to change backend independently.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

_DEFAULT_STORAGE_DIR = Path("data") / "tailored_resumes"
DOCX_SUFFIX = ".docx"


class LocalTailoredResumeStorage:
    """Writes/reads generated `.docx` files under a configurable base
    directory.

    Defaults to `<cwd>/data/tailored_resumes` — a sibling of
    `LocalResumeStorage`'s `<cwd>/data/resumes` — overridable via the
    `TAILORED_RESUME_STORAGE_DIR` environment variable (following
    `RESUME_STORAGE_DIR`'s naming convention) or an explicit `base_dir`
    (tests pass `tmp_path`).

    Each user gets their own subdirectory; each file is named
    `<record_id>_<job_id>.docx`, so a regenerated document for the same job
    never overwrites the one an earlier `tailored_resumes` row points at.

    No filename sanitization step is needed here, unlike
    `LocalResumeStorage`: every path component is a UUID this component
    generates, never user-supplied text, so nothing hostile can reach the
    path. The `UUID` parameter types are what enforce that.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        if base_dir is not None:
            self.base_dir = Path(base_dir)
        else:
            env_dir = os.environ.get("TAILORED_RESUME_STORAGE_DIR")
            self.base_dir = Path(env_dir) if env_dir else _DEFAULT_STORAGE_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, user_id: UUID, record_id: UUID, job_id: UUID, content: bytes) -> str:
        """Persist `content` and return the `storage_uri` to record on the
        `tailored_resumes` row.
        """
        user_dir = self.base_dir / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        path = user_dir / f"{record_id}_{job_id}{DOCX_SUFFIX}"
        path.write_bytes(content)
        return str(path)

    def read(self, storage_uri: str) -> bytes:
        """Read back the bytes previously stored at `storage_uri`.

        Raises `FileNotFoundError` when nothing is stored there — same as
        `LocalResumeStorage.read`. A missing file means the row and the disk
        have diverged, which the download route should surface rather than
        paper over with empty bytes.
        """
        return Path(storage_uri).read_bytes()

    def delete(self, storage_uri: str) -> None:
        """Best-effort removal; a missing file is not an error — same
        contract as `LocalResumeStorage.delete`, where the row is the source
        of truth, not the file's presence.
        """
        path = Path(storage_uri)
        if path.exists():
            path.unlink()


__all__ = ["LocalTailoredResumeStorage"]
