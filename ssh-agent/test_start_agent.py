"""Check that start_agent.sh loads the key into an agent later steps can reach."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "start_agent.sh"


class StartAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        key = self.home / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
        self.key = key.read_text()
        self.fingerprint = subprocess.run(
            ["ssh-keygen", "-lf", f"{key}.pub"], check=True, capture_output=True, text=True
        ).stdout.split()[1]
        self.github_env = self.home / "github_env"
        self.github_env.write_text("")
        self.agents = []

    def tearDown(self):
        for env in self.agents:
            subprocess.run(["ssh-agent", "-k"], env=env, capture_output=True)
        self.tmp.cleanup()

    def start(self, **extra):
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home),
               "GITHUB_ENV": str(self.github_env), **extra}
        return subprocess.run(["bash", SCRIPT], env=env, capture_output=True, text=True)

    def exported(self):
        pairs = (line.split("=", 1) for line in self.github_env.read_text().splitlines())
        env = {**os.environ, **dict(pairs)}
        self.agents.append(env)
        return env

    def test_later_steps_reach_the_loaded_key(self):
        result = self.start(SSH_KEY=self.key.replace("\n", "\r\n"))
        self.assertEqual(result.returncode, 0, result.stderr)
        listed = subprocess.run(["ssh-add", "-l"], env=self.exported(),
                                capture_output=True, text=True, check=True).stdout
        self.assertIn(self.fingerprint, listed)
        self.assertNotIn(self.key.splitlines()[1], result.stdout + result.stderr)
        self.assertFalse((self.home / ".ssh/known_hosts").exists())

    def test_host_key_is_trusted(self):
        line = "[deploy.example]:2222 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample"
        self.assertEqual(self.start(SSH_KEY=self.key, SSH_HOST_KEY=line).returncode, 0)
        self.exported()
        self.assertEqual((self.home / ".ssh/known_hosts").read_text(), line + "\n")

    def test_invalid_key_fails_and_exports_nothing(self):
        result = self.start(SSH_KEY="not a key")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.github_env.read_text(), "")

    def test_missing_key_fails(self):
        self.assertNotEqual(self.start(SSH_KEY="").returncode, 0)


if __name__ == "__main__":
    unittest.main()
