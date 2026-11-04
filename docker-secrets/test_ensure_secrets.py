import pytest

import ensure_secrets as subject

WANTED = {"app_auth_20261101_v1": "AUTH", "app_db_20261101_v2": "DB"}


def test_creates_only_what_is_missing():
    created = subject.plan(WANTED, {"app_auth_20261101_v1"}, {"DB": "value"})
    assert created == {"app_db_20261101_v2": "value"}


def test_an_existing_secret_needs_no_value():
    assert subject.plan(WANTED, set(WANTED), {}) == {}


def test_a_missing_value_names_the_github_secret_not_the_value():
    with pytest.raises(subject.SecretsError, match="AUTH, DB"):
        subject.plan(WANTED, set(), {"DB": ""})


def test_refuses_an_unversioned_name():
    with pytest.raises(subject.SecretsError, match="database_url"):
        subject.plan({"database_url": "DB"}, set(), {"DB": "value"})
