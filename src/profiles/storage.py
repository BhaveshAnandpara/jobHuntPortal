"""Local filesystem storage for uploaded resume files.

No shared "file storage" abstraction exists in the architecture docs for
this concern — `docs/architecture/domain-model.md#resume` only types
`Resume.storage_uri` as `str` ("location of the original file"), and
`docs/architecture/component-contracts.md` doesn't specify a backend. This
is a Resume/Profile Service *internal* concern, not a new shared contract.
Implemented as a simple local filesystem helper, matching about_project.md's
local-first goal (no cloud storage dependency required at this stage).

`storage_uri` values produced here are plain local filesystem paths.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import UUID

_DEFAULT_STORAGE_DIR = Path("data") / "resumes"
_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _sanitize_file_name(file_name: str) -> str:
    """Strip any path components and replace characters that aren't safe
    in a filename, so a hostile `file_name` can't escape the storage
    directory or collide with OS-reserved characters.
    """
    name = Path(file_name).name
    name = _UNSAFE_CHARS_RE.sub("_", name)
    return name or "resume"


class LocalResumeStorage:
    """Writes/reads resume files under a configurable base directory.

    Defaults to `<cwd>/data/resumes`, overridable via the `RESUME_STORAGE_DIR`
    environment variable or an explicit `base_dir` (tests pass `tmp_path`).
    Each user gets their own subdirectory; each file is prefixed with its
    `resume_id` so two uploads with the same original file name never
    collide.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        if base_dir is not None:
            self.base_dir = Path(base_dir)
        else:
            env_dir = os.environ.get("RESUME_STORAGE_DIR")
            self.base_dir = Path(env_dir) if env_dir else _DEFAULT_STORAGE_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, user_id: UUID, resume_id: UUID, file_name: str, content: bytes) -> str:
        """Persist `content` and return the `storage_uri` to record on the
        `Resume` row.
        """
        user_dir = self.base_dir / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _sanitize_file_name(file_name)
        path = user_dir / f"{resume_id}_{safe_name}"
        path.write_bytes(content)
        return str(path)

    def read(self, storage_uri: str) -> bytes:
        """Read back the bytes previously stored at `storage_uri`."""
        return Path(storage_uri).read_bytes()

    def delete(self, storage_uri: str) -> None:
        """Best-effort removal; a missing file is not an error (soft-delete
        of the `Resume` row is the source of truth, not the file's
        presence).
        """
        path = Path(storage_uri)
        if path.exists():
            path.unlink()


__all__ = ["LocalResumeStorage"]
