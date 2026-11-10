import json
import subprocess

import pytest

import ensure_secrets as subject

VALUES = {"app_auth_20261101_v1": "secret", "app_db_20261101_v2": "postgres://x"}


def test_creates_only_what_is_missing():
    created = subject.plan("app", VALUES, {"app_auth_20261101_v1"})
    assert created == {"app_db_20261101_v2": "postgres://x"}


def test_nothing_to_create_when_all_exist():
    assert subject.plan("app", VALUES, set(VALUES)) == {}


def test_refuses_an_unversioned_name():
    with pytest.raises(subject.SecretsError, match="database_url"):
        subject.plan("app", {"database_url": "x"}, set())


def test_refuses_another_products_name():
    with pytest.raises(subject.SecretsError, match="other_db_20261101_v1"):
        subject.plan("app", {"other_db_20261101_v1": "x"}, set())


def test_an_empty_value_names_the_secret_not_the_value():
    with pytest.raises(subject.SecretsError, match="app_db_20261101_v1 has no value"):
        subject.plan("app", {"app_db_20261101_v1": ""}, set())


def test_decrypt_masks_every_line_and_drops_metadata(tmp_path, monkeypatch, capsys):
    path = tmp_path / "app" / "docker-secrets.json"
    path.parent.mkdir()
    path.write_text("{}")
    decrypted = {"app_key_20261101_v1": "line one\nline two", "sops": {"age": []}}

    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(decrypted))

    monkeypatch.setattr(subject.subprocess, "run", run)
    assert subject.decrypt("sops", path) == {"app_key_20261101_v1": "line one\nline two"}
    assert capsys.readouterr().out.splitlines() == ["::add-mask::line one", "::add-mask::line two"]


def test_a_failed_decrypt_withholds_output(tmp_path, monkeypatch):
    path = tmp_path / "app" / "docker-secrets.json"
    path.parent.mkdir()
    path.write_text("{}")

    def run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args, stderr="secret detail")

    monkeypatch.setattr(subject.subprocess, "run", run)
    with pytest.raises(subject.SecretsError, match="^Could not decrypt app/docker-secrets.json.$"):
        subject.decrypt("sops", path)


def test_a_missing_file_is_named(tmp_path):
    with pytest.raises(subject.SecretsError, match="No app/docker-secrets.json"):
        subject.decrypt("sops", tmp_path / "app" / "docker-secrets.json")
