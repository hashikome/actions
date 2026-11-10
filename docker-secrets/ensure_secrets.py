#!/usr/bin/env python3
"""Create a product's missing Docker secrets from its encrypted file in hashikome/secrets. A Docker
secret never changes: a new value takes a new versioned name, so one that exists is left alone."""
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


def plan(product, values, existing):
    """The secrets to create, as {docker name: value}."""
    for name, value in values.items():
        if not NAME.fullmatch(name) or not name.startswith(f"{product}_"):
            raise SecretsError(f"Expected {product}_<name>_YYYYMMDD_vN, not {name}.")
        if not isinstance(value, str) or not value:
            raise SecretsError(f"{name} has no value.")
    return {name: value for name, value in values.items() if name not in existing}


def decrypt(sops, path):
    """The file's values, decrypted in memory and masked in the job's log before anything prints."""
    if not path.is_file():
        raise SecretsError(f"No {path.parent.name}/{path.name} in hashikome/secrets.")
    try:
        text = subprocess.run(
            [sops, "decrypt", "--output-type", "json", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout
    except subprocess.CalledProcessError:
        raise SecretsError(f"Could not decrypt {path.parent.name}/{path.name}.") from None
    values = json.loads(text)
    values.pop("sops", None)
    for value in values.values():
        for line in str(value).splitlines():
            if line.strip():
                print(f"::add-mask::{line}")
    return values


def main():
    product = os.environ["PRODUCT"]
    values = decrypt(os.environ["SOPS"], Path(os.environ["SECRETS_DIR"], product, "docker-secrets.json"))
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
        created = plan(product, values, existing)
        for name, value in created.items():
            remote(f"docker secret create {shlex.quote(name)} -", value)
            print("created", name)
    print(f"{len(values)} Docker secrets in place.")


if __name__ == "__main__":
    try:
        main()
    except SecretsError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError:
        print("A server command failed; output withheld.", file=sys.stderr)
        raise SystemExit(1)
