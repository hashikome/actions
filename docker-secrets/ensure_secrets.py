#!/usr/bin/env python3
"""Create a product's missing Docker secrets. A Docker secret never changes: a new value takes a
new versioned name, so one that exists is left alone and its value isn't needed."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

NAME = re.compile(r"[a-z0-9]+(_[a-z0-9]+)*_\d{8}_v\d+")


class SecretsError(Exception):
    """A safe message: never a value."""


def plan(wanted, existing, values):
    """The secrets to create, as {docker name: value}."""
    for name in wanted:
        if not NAME.fullmatch(name):
            raise SecretsError(f"Expected <product>_<name>_YYYYMMDD_vN, not {name}.")
    missing = {name: source for name, source in wanted.items() if name not in existing}
    absent = sorted(source for source in missing.values() if not values.get(source))
    if absent:
        raise SecretsError("Missing GitHub secrets: " + ", ".join(absent))
    return {name: values[source] for name, source in missing.items()}


def main():
    wanted = json.loads(Path(os.environ["SECRETS_CONFIG"]).read_text())
    values = json.loads(os.environ["SECRET_VALUES"])
    with tempfile.TemporaryDirectory() as directory:
        key, hosts = Path(directory, "key"), Path(directory, "hosts")
        key.write_text(os.environ["SSH_KEY"].rstrip("\n") + "\n")
        key.chmod(0o600)
        hosts.write_text(os.environ["SSH_HOST_KEY"].rstrip("\n") + "\n")

        def remote(command, value=None):
            # A value travels on stdin, so it is never in a command line or a log.
            return subprocess.run(
                ["ssh", "-F", "/dev/null", "-i", str(key), "-o", "BatchMode=yes",
                 "-o", "IdentitiesOnly=yes", "-o", f"UserKnownHostsFile={hosts}",
                 "-o", "StrictHostKeyChecking=yes", os.environ["SSH_TARGET"], command],
                input=value, capture_output=True, text=True, check=True,
            ).stdout

        existing = set(remote("docker secret ls --format '{{.Name}}'").split())
        created = plan(wanted, existing, values)
        for name, value in created.items():
            remote(f"docker secret create {shlex.quote(name)} -", value)
            print("created", name)
    print(f"{len(wanted)} Docker secrets in place.")


if __name__ == "__main__":
    try:
        main()
    except SecretsError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError:
        print("A server command failed; output withheld.", file=sys.stderr)
        raise SystemExit(1)
