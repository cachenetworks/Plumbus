from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.models import Role
from app.security.security import assert_can_assign_role, random_token, token_hash


def actor(role: Role):
    return SimpleNamespace(role=role)


def test_random_tokens_are_non_predictable_and_hash_only():
    first = random_token(32)
    second = random_token(32)
    assert first != second
    assert len(first) >= 32
    assert token_hash(first) != first
    assert token_hash(first) == token_hash(first)
    assert token_hash(first) != token_hash(second)


def test_admin_cannot_assign_admin_or_superadmin():
    with pytest.raises(HTTPException) as admin_error:
        assert_can_assign_role(actor(Role.ADMIN), Role.ADMIN)
    assert admin_error.value.status_code == 403

    with pytest.raises(HTTPException) as super_error:
        assert_can_assign_role(actor(Role.ADMIN), Role.SUPERADMIN)
    assert super_error.value.status_code == 403


def test_admin_can_assign_support_and_member():
    assert_can_assign_role(actor(Role.ADMIN), Role.SUPPORT)
    assert_can_assign_role(actor(Role.ADMIN), Role.MEMBER)


def test_superadmin_can_assign_all_roles():
    for role in Role:
        assert_can_assign_role(actor(Role.SUPERADMIN), role)
