"""Tests for `resume_tailoring.storage.LocalTailoredResumeStorage` —
docs/tailored-resume/TECH.md Task 5.

Mirrors what `profiles.storage.LocalResumeStorage` guarantees, since this
class is a deliberate mirror of it: exact-bytes round-trip, per-user
subdirectory layout, best-effort delete.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from resume_tailoring.storage import LocalTailoredResumeStorage

# A real (tiny) binary payload — a .docx is a ZIP, so the bytes stored here
# are never text, and a storage layer that quietly decoded/re-encoded them
# would corrupt every generated document.
DOCX_BYTES = b"PK\x03\x04\x14\x00\x00\x00\x08\x00tailored\x00\xff\xfe\x00binary"


def test_save_then_read_round_trips_exact_bytes(tmp_path: Path) -> None:
    storage = LocalTailoredResumeStorage(base_dir=tmp_path)
    user_id, record_id, job_id = uuid4(), uuid4(), uuid4()

    storage_uri = storage.save(user_id, record_id, job_id, DOCX_BYTES)

    assert storage.read(storage_uri) == DOCX_BYTES


def test_save_uses_the_documented_path_convention(tmp_path: Path) -> None:
    """`<base>/<user_id>/<record_id>_<job_id>.docx`."""
    storage = LocalTailoredResumeStorage(base_dir=tmp_path)
    user_id, record_id, job_id = uuid4(), uuid4(), uuid4()

    storage_uri = storage.save(user_id, record_id, job_id, DOCX_BYTES)

    path = Path(storage_uri)
    assert path.parent == tmp_path / str(user_id)
    assert path.name == f"{record_id}_{job_id}.docx"
    assert path.is_file()


def test_two_jobs_for_one_user_are_stored_side_by_side(tmp_path: Path) -> None:
    storage = LocalTailoredResumeStorage(base_dir=tmp_path)
    user_id = uuid4()

    first = storage.save(user_id, uuid4(), uuid4(), b"first")
    second = storage.save(user_id, uuid4(), uuid4(), b"second")

    assert first != second
    assert storage.read(first) == b"first"
    assert storage.read(second) == b"second"


def test_regenerating_for_the_same_job_does_not_overwrite(tmp_path: Path) -> None:
    """A new `tailored_resumes` row means a new `record_id`, so the earlier
    document stays readable for the row that still points at it.
    """
    storage = LocalTailoredResumeStorage(base_dir=tmp_path)
    user_id, job_id = uuid4(), uuid4()

    original = storage.save(user_id, uuid4(), job_id, b"original")
    regenerated = storage.save(user_id, uuid4(), job_id, b"regenerated")

    assert original != regenerated
    assert storage.read(original) == b"original"
    assert storage.read(regenerated) == b"regenerated"


def test_different_users_get_separate_directories(tmp_path: Path) -> None:
    storage = LocalTailoredResumeStorage(base_dir=tmp_path)
    first_user, second_user = uuid4(), uuid4()

    first = Path(storage.save(first_user, uuid4(), uuid4(), DOCX_BYTES))
    second = Path(storage.save(second_user, uuid4(), uuid4(), DOCX_BYTES))

    assert first.parent != second.parent
    assert first.parent.name == str(first_user)
    assert second.parent.name == str(second_user)


def test_delete_removes_the_file_and_read_then_raises(tmp_path: Path) -> None:
    """Same error behavior as `LocalResumeStorage.read` — `Path.read_bytes`
    on a missing file raises `FileNotFoundError` rather than returning
    empty bytes.
    """
    storage = LocalTailoredResumeStorage(base_dir=tmp_path)
    storage_uri = storage.save(uuid4(), uuid4(), uuid4(), DOCX_BYTES)

    storage.delete(storage_uri)

    assert not Path(storage_uri).exists()
    with pytest.raises(FileNotFoundError):
        storage.read(storage_uri)


def test_delete_is_idempotent_for_a_missing_file(tmp_path: Path) -> None:
    """Best-effort removal — the row is the source of truth, not the file's
    presence (same contract as `LocalResumeStorage.delete`).
    """
    storage = LocalTailoredResumeStorage(base_dir=tmp_path)
    storage_uri = storage.save(uuid4(), uuid4(), uuid4(), DOCX_BYTES)

    storage.delete(storage_uri)
    storage.delete(storage_uri)  # must not raise

    assert not Path(storage_uri).exists()


def test_base_dir_is_created_eagerly(tmp_path: Path) -> None:
    base = tmp_path / "not" / "yet" / "there"

    LocalTailoredResumeStorage(base_dir=base)

    assert base.is_dir()


def test_env_var_configures_the_base_directory(tmp_path: Path, monkeypatch) -> None:
    """`TAILORED_RESUME_STORAGE_DIR`, following `RESUME_STORAGE_DIR`'s
    naming convention.
    """
    configured = tmp_path / "from-env"
    monkeypatch.setenv("TAILORED_RESUME_STORAGE_DIR", str(configured))

    storage = LocalTailoredResumeStorage()

    assert storage.base_dir == configured
    user_id = uuid4()
    storage_uri = storage.save(user_id, uuid4(), uuid4(), DOCX_BYTES)
    assert Path(storage_uri).parent == configured / str(user_id)


def test_explicit_base_dir_wins_over_the_env_var(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TAILORED_RESUME_STORAGE_DIR", str(tmp_path / "from-env"))
    explicit = tmp_path / "explicit"

    storage = LocalTailoredResumeStorage(base_dir=explicit)

    assert storage.base_dir == explicit


def test_default_base_dir_is_a_sibling_of_the_uploaded_resume_dir(monkeypatch) -> None:
    """Defaults to `data/tailored_resumes`, next to `LocalResumeStorage`'s
    `data/resumes`. Asserted without instantiating, so the test never
    creates a directory in the repo root.
    """
    from resume_tailoring import storage as storage_module

    monkeypatch.delenv("TAILORED_RESUME_STORAGE_DIR", raising=False)
    assert storage_module._DEFAULT_STORAGE_DIR == Path("data") / "tailored_resumes"
