"""Check that start_agent.sh loads the key into an agent later steps can reach."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "start_agent.sh"


class Runner:
    """Runs the script in a scratch HOME and stops every agent it started."""

    def __init__(self, home):
        self.home = home
        key = home / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
        self.key = key.read_text()
        self.fingerprint = subprocess.run(
            ["ssh-keygen", "-lf", f"{key}.pub"], check=True, capture_output=True, text=True
        ).stdout.split()[1]
        self.github_env = home / "github_env"
        self.github_env.write_text("")
        self.agents = []

    def start(self, **extra):
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home),
               "GITHUB_ENV": str(self.github_env), **extra}
        return subprocess.run(["bash", SCRIPT], env=env, capture_output=True, text=True)

    def exported(self):
        pairs = (line.split("=", 1) for line in self.github_env.read_text().splitlines())
        env = {**os.environ, **dict(pairs)}
        self.agents.append(env)
        return env


@pytest.fixture
def runner(tmp_path):
    runner = Runner(tmp_path)
    yield runner
    for env in runner.agents:
        subprocess.run(["ssh-agent", "-k"], env=env, capture_output=True)


def test_later_steps_reach_the_loaded_key(runner):
    result = runner.start(SSH_KEY=runner.key.replace("\n", "\r\n"))
    assert result.returncode == 0, result.stderr
    listed = subprocess.run(["ssh-add", "-l"], env=runner.exported(),
                            capture_output=True, text=True, check=True).stdout
    assert runner.fingerprint in listed
    assert runner.key.splitlines()[1] not in result.stdout + result.stderr
    assert not (runner.home / ".ssh/known_hosts").exists()


def test_host_key_is_trusted(runner):
    line = "[deploy.example]:2222 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample"
    assert runner.start(SSH_KEY=runner.key, SSH_HOST_KEY=line).returncode == 0
    runner.exported()
    assert (runner.home / ".ssh/known_hosts").read_text() == line + "\n"


def test_invalid_key_fails_and_exports_nothing(runner):
    result = runner.start(SSH_KEY="not a key")
    assert result.returncode != 0
    assert runner.github_env.read_text() == ""


def test_missing_key_fails(runner):
    assert runner.start(SSH_KEY="").returncode != 0
