"""Tests for bcrypt password hashing."""

from __future__ import annotations

from infrastructure.auth.passwords import hash_password, verify_password


def test_hash_password_produces_a_different_hash_each_call() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second  # fresh random salt per call


def test_verify_password_accepts_the_matching_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_password_rejects_a_wrong_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("wrong password", hashed) is False
