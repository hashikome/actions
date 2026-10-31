#!/usr/bin/env python3
"""Promote a product release through Dokploy: migration Stack first, then the application Stack.

The sequence lives here; everything specific to a product comes from its release configuration.
A failed migration never touches the application, and there is no automatic rollback. Standard
library only, so no product's toolchain is needed.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlencode

SWARM_SERVICE = "com.docker.swarm.service.name"
SWARM_TASK = "com.docker.swarm.task.id"
RELEASE_KEYS = {"RELEASE_SHA", "RELEASE_ATTEMPT", "APP_REPLICAS"}


class ReleaseError(Exception):
    """A refusal of ours. The message is written here, so it is safe to print."""


def validate(config, release):
    if not re.fullmatch(r"[a-f0-9]{40}", release["revision"]) or not re.fullmatch(
        r"\d+-\d+", release["attempt"]
    ):
        raise ReleaseError("A full revision and GitHub run-attempt identity are required.")
    if set(release["images"]) != set(config["images"]):
        raise ReleaseError("Every configured image, and nothing else, must be supplied.")
    for key, package in config["images"].items():
        pattern = rf"{re.escape(package)}:{release['revision']}@sha256:[a-f0-9]{{64}}"
        if not re.fullmatch(pattern, release["images"][key]):
            raise ReleaseError(f"{key} must be this revision by digest in the product's package.")
    application, migration = config["application"]["compose"], config["migration"]["compose"]
    if not application or not migration or application == migration:
        raise ReleaseError("Application and migration require distinct Dokploy targets.")


def manifest_text(config, part, root):
    return (Path(root) / config[part]["manifest"]).read_text()


def environment(previous, config, release, part, root="."):
    """The Stack's env file: stored values, refreshed release values, every required input set."""
    allowed = set(config["allowedInputs"]) | RELEASE_KEYS | set(config["images"])
    values = {}
    for line in previous.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z0-9_]+)=(.*)", line)
        if not match or match[1] not in allowed or match[1] in values:
            raise ReleaseError("Unexpected or duplicate deployment input; configuration withheld.")
        values[match[1]] = match[2]
    values.update(release["images"])
    values.update(
        RELEASE_SHA=release["revision"], RELEASE_ATTEMPT=release["attempt"], APP_REPLICAS="1"
    )
    secret_suffixes = tuple(config.get("secretNameSuffixes", ["_SECRET"]))
    for name in re.findall(r"\$\{([A-Z0-9_]+)(?::\?[^}]*)?\}", manifest_text(config, part, root)):
        value = values.get(name)
        if not value or "SET_" in value or re.search(r"[\r\n$`]", value):
            raise ReleaseError(f"Missing or unresolved deployment input: {name}")
        if name.endswith(secret_suffixes) and not re.fullmatch(r"[a-zA-Z0-9_.-]+", value):
            raise ReleaseError(f"Expected a secret name: {name}")
    if part == "application":
        for name, expected in config.get("requiredValues", {}).items():
            if values.get(name) != expected:
                raise ReleaseError(f"Unexpected value for {name}.")
    return "".join(f"{key}={value}\n" for key, value in values.items())


def inspected(value):
    parsed = json.loads(value) if isinstance(value, str) else value
    return (parsed[0] if isinstance(parsed, list) and parsed else parsed) or {}


def migration_state(container, app_name, config, release):
    settings = config["migration"]
    runtime = container.get("Config") or {}
    labels = runtime.get("Labels") or {}
    state = container.get("State") or {}
    if (
        labels.get(SWARM_SERVICE) != f"{app_name}_{settings['service']}"
        or not labels.get(SWARM_TASK)
        or runtime.get("Image") != release["images"][settings["image"]]
        or f"RELEASE_ATTEMPT={release['attempt']}" not in (runtime.get("Env") or [])
    ):
        return "stale"
    if runtime.get("Cmd") != settings["command"]:
        raise ReleaseError("Migration task does not run the reviewed entrypoint.")
    if state.get("OOMKilled") or state.get("Status") == "dead":
        raise ReleaseError("Migration task died; application remains unchanged.")
    if state.get("Status") != "exited":
        return "pending"
    if state.get("ExitCode") != 0:
        raise ReleaseError("Migration task failed; application remains unchanged.")
    return "complete"


def application_service(container, app_name, config, release):
    """The configured service this container proves healthy on this release, or None."""
    runtime = container.get("Config") or {}
    state = container.get("State") or {}
    name = (runtime.get("Labels") or {}).get(SWARM_SERVICE) or ""
    service = name[len(app_name) + 1 :] if name.startswith(f"{app_name}_") else ""
    settings = config["services"].get(service)
    if (
        not settings
        or state.get("Status") != "running"
        or runtime.get("Image") != release["images"][settings["image"]]
        or f"RELEASE_ATTEMPT={release['attempt']}" not in (runtime.get("Env") or [])
    ):
        return None
    if settings.get("healthcheck", True) and (state.get("Health") or {}).get("Status") != "healthy":
        return None
    return service


def wait_for(api, target, title, config, release, migration, sleep, attempts):
    for _ in range(attempts):
        deployments = api("deployment.allByCompose", query={"composeId": target["composeId"]})
        deployment = next((item for item in deployments if item["title"] == title), None)
        if deployment and deployment["status"] == "error":
            raise ReleaseError("Dokploy deployment failed; inspect before retrying.")
        if deployment and deployment["status"] == "done":
            # Dokploy's stack listing filters desired-state=running. The all-state listing
            # includes completed migrations, so verify exact Swarm labels below.
            query = {"appName": target["appName"]}
            if not migration:
                query["appType"] = "stack"
            listing = api("docker.getContainersByAppNameMatch", query=query)
            complete = matched = 0
            running = {}
            for item in listing:
                if not re.fullmatch(r"[a-f0-9]{12,64}", item.get("containerId", "")):
                    continue
                container = inspected(
                    api("docker.getConfig", query={"containerId": item["containerId"]})
                )
                if migration:
                    state = migration_state(container, target["appName"], config, release)
                    matched += state != "stale"
                    complete += state == "complete"
                else:
                    service = application_service(container, target["appName"], config, release)
                    if service:
                        running[service] = running.get(service, 0) + 1
            if migration and matched > 1:
                raise ReleaseError("Ambiguous migration completion; inspect the target.")
            if migration and complete == 1:
                return
            services = config["services"]
            if (
                not migration
                and sum(item.get("state") == "running" for item in listing) == len(services)
                and all(running.get(service) == 1 for service in services)
            ):
                return
        sleep()
    raise ReleaseError("Release acceptance timed out; no automatic schema or image rollback.")


def stack_problem(target, pattern):
    return (
        target.get("composeType") != "stack"
        or target.get("sourceType") != "raw"
        or target.get("autoDeploy") is not False
        or target.get("serverId")
        or not re.fullmatch(pattern, target.get("appName", ""))
    )


def promote(api, config, release, root=".", sleep=lambda: time.sleep(5), attempts=120):
    validate(config, release)
    application = api("compose.one", query={"composeId": config["application"]["compose"]})
    migration = api("compose.one", query={"composeId": config["migration"]["compose"]})
    if stack_problem(application, config["application"]["stack"]) or stack_problem(
        migration, config["migration"]["stack"]
    ):
        raise ReleaseError("Expected dedicated local raw-source Stacks with auto-deploy disabled.")
    app_env = environment(application["env"], config, release, "application", root)
    migration_env = environment(migration["env"], config, release, "migration", root)

    def line(env, key):
        return next((item for item in env.splitlines() if item.startswith(f"{key}=")), None)

    for key in config.get("sharedInputs", []):
        if line(app_env, key) != line(migration_env, key):
            raise ReleaseError(f"Application and migration disagree on {key}.")
    previous = None
    if config.get("previousApplication"):
        settings = config["previousApplication"]
        if settings["compose"] == config["application"]["compose"]:
            raise ReleaseError("The previous application must be a distinct target.")
        previous = api("compose.one", query={"composeId": settings["compose"]})
        if (
            stack_problem(previous, re.escape(settings["stack"]))
            or line(environment(previous["env"], config, release, "application", root), "BACKEND_NETWORK")
            != line(app_env, "BACKEND_NETWORK")
        ):
            raise ReleaseError("Unexpected previous application target.")
    api("registry.testRegistryById", {"registryId": release["registry"]})
    for part, target, env in (
        ("migration", migration, migration_env),
        ("application", application, app_env),
    ):
        title = f"{config['title']} {part} {release['revision']} ({release['attempt']})"
        api(
            "compose.update",
            {
                "composeId": target["composeId"],
                "composeFile": manifest_text(config, part, root),
                "env": env,
                "command": "",
                "autoDeploy": False,
            },
        )
        if part == "application" and previous:
            # The migration is accepted; prevent duplicate routes and workers.
            def active():
                listing = api(
                    "docker.getContainersByAppNameMatch", query={"appName": previous["appName"]}
                )
                return any(item.get("state") not in ("exited", "dead") for item in listing)

            if active():
                api("compose.stop", {"composeId": previous["composeId"]})
            for _ in range(attempts):
                if not active():
                    break
                sleep()
            else:
                raise ReleaseError("Previous application has not stopped; replacement refused.")
        api("compose.deploy", {"composeId": target["composeId"], "title": title})
        wait_for(api, target, title, config, release, part == "migration", sleep, attempts)


def parse_images(text):
    images = {}
    for line in text.splitlines():
        if line.strip():
            key, separator, value = line.strip().partition("=")
            if not separator or key in images:
                raise ReleaseError("Images must be one KEY=image per line.")
            images[key] = value
    return images


def dokploy(origin, key):
    def api(operation, payload=None, query=None):
        # Curl config on stdin keeps the credential out of argv, shell expansion and logs.
        url = f"{origin}/api/{operation}" + (f"?{urlencode(query)}" if query else "")
        lines = [f"url = {json.dumps(url)}", "header = " + json.dumps(f"x-api-key: {key}")]
        if payload is not None:
            lines += [
                'header = "Content-Type: application/json"',
                f"data = {json.dumps(json.dumps(payload))}",
            ]
        result = subprocess.run(
            ["curl", "--silent", "--show-error", "--max-time", "45", "--config", "-"]
            + ["--write-out", "\n%{http_code}"],
            input="\n".join(lines),
            text=True,
            capture_output=True,
        )
        body, _, status = result.stdout.rpartition("\n")
        if result.returncode or status != "200":
            raise ReleaseError(f"Dokploy {operation} failed; response withheld.")
        return json.loads(body or "null")

    return api


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ReleaseError("Run release promotion from the main-branch GitHub workflow.")
    revision = os.environ.get("GITHUB_SHA", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    head = subprocess.run(
        ["gh", "api", f"repos/{repository}/git/ref/heads/main", "--jq", ".object.sha"],
        text=True,
        capture_output=True,
    )
    if head.returncode or head.stdout.strip() != revision:
        raise ReleaseError("Source revision is superseded or unreadable.")
    key = os.environ.get("DOKPLOY_API_KEY", "")
    if not key or re.search(r"[\r\n]", key):
        raise ReleaseError("Missing deployment credential.")
    config = json.loads(Path(os.environ["RELEASE_CONFIG"]).read_text())
    release = {
        "revision": revision,
        "attempt": f"{os.environ.get('GITHUB_RUN_ID')}-{os.environ.get('GITHUB_RUN_ATTEMPT')}",
        "images": parse_images(os.environ.get("RELEASE_IMAGES", "")),
        "registry": os.environ.get("DOKPLOY_REGISTRY_ID", ""),
    }
    promote(dokploy(config["dokployUrl"], key), config, release)
    print(f"{config['title']} migration completed and application tasks converged.")


if __name__ == "__main__":
    try:
        main()
    except ReleaseError as error:
        print(f"Release refused or failed: {error}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Anything unforeseen could carry a response body: name the failure, not its content.
        print("Release failed unexpectedly; details withheld. No rollback was attempted.", file=sys.stderr)
        sys.exit(1)
