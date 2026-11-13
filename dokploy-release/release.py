#!/usr/bin/env python3
"""Promote a product release through Dokploy: its migration Stack first, if it has one, then the
application Stack.

The sequence lives here; everything specific to a product comes from its release configuration.
A failed migration never touches the application. Rollback is Dokploy's, on Docker Swarm: a service
whose new tasks don't become healthy is put back during the deploy (each Stack's `update_config`), and
a release found wrong later is undone by redeploying the previous revision through Dokploy. Standard library only, so no product's toolchain
is needed.
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
# Swarm's verdict on a service's update, from its events (`updatestate.new`): Docker keeps only the
# last 256 events, so they are collected while waiting, not asked for afterwards.
ROLLED_BACK = {"rollback_started", "rollback_completed", "rollback_paused"}
STOPPED = {"paused"}
# Reads while a Stack deploys can fail for a moment (Dokploy busy, the edge); so many in a row is real
POLL_FAILURES = 6


class ReleaseError(Exception):
    """A refusal of ours. The message is written here, so it is safe to print."""


class Unanswered(ReleaseError):
    """A Dokploy call that didn't succeed: while a Stack deploys, a read may be retried."""


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
    application = config["application"]["compose"]
    migration = config["migration"]["compose"] if "migration" in config else None
    if not application or migration == "" or application == migration:
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
    # Fixed inputs live in the product's repository and win over stored values.
    unknown = set(config.get("inputs", {})) - allowed
    if unknown:
        raise ReleaseError("Unexpected or duplicate deployment input; configuration withheld.")
    values.update(config.get("inputs", {}))
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


def verdicts(api, app_name, since, seen):
    """Each service's latest Swarm update state since the deploy began, added to `seen`."""
    result = api("docker.getEvents", query={"minutes": 1}) or {}
    for event in sorted(result.get("events") or [], key=lambda e: e.get("timeNano") or 0):
        attributes = (event.get("Actor") or {}).get("Attributes") or {}
        name, state = attributes.get("name") or "", attributes.get("updatestate.new")
        if event.get("Type") == "service" and state and name.startswith(f"{app_name}_") and event.get("time", 0) >= since:
            seen[name[len(app_name) + 1 :]] = state
    return seen


def summary(lines):
    """The verdict in the run's log, and in its summary when GitHub gives one."""
    print("\n".join(line.replace("**", "") for line in lines), flush=True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as out:
            out.write("\n".join(lines) + "\n")


def wait_for(api, target, title, config, release, migration, sleep, attempts, since=0):
    seen, failures = {}, 0
    for _ in range(attempts):
        try:
            if poll(api, target, title, config, release, migration, since, seen):
                return
            failures = 0
        except Unanswered:  # a read Dokploy couldn't answer is retried; a verdict or a refusal is final
            failures += 1
            if failures >= POLL_FAILURES:
                raise
        sleep()
    raise ReleaseError("Release acceptance timed out; Swarm keeps or restores the previous tasks.")


def poll(api, target, title, config, release, migration, since, seen):
    """One look at the deploy: True once it is released, False to look again; raises on a verdict."""
    if not migration:
        verdicts(api, target["appName"], since, seen)
        back = sorted(s for s, state in seen.items() if state in ROLLED_BACK)
        if back:
            summary([f"**Rolled back by Swarm:** {', '.join(back)}; the previous release keeps running."])
            raise ReleaseError(f"Swarm rolled back {', '.join(back)}; the previous release keeps running.")
        if any(state in STOPPED for state in seen.values()):
            raise ReleaseError("Swarm paused the update; inspect the services.")
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
            return True
        services = config["services"]
        if (
            not migration
            and sum(item.get("state") == "running" for item in listing) == len(services)
            and all(running.get(service) == 1 for service in services)
            # a service Swarm is updating is done only once its watch period passed (`completed`)
            and all(state == "completed" for state in seen.values())
        ):
            summary([f"**Released:** {service}: {seen.get(service, 'created')}" for service in services])
            return True
    return False


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
    # A product whose application migrates itself at start (a single-writer database) has no
    # migration Stack: the application is then the only part.
    parts = [part for part in ("migration", "application") if part in config]
    targets = {part: api("compose.one", query={"composeId": config[part]["compose"]}) for part in parts}
    if any(stack_problem(targets[part], config[part]["stack"]) for part in parts):
        raise ReleaseError("Expected dedicated local raw-source Stacks with auto-deploy disabled.")
    envs = {part: environment(targets[part]["env"], config, release, part, root) for part in parts}

    def line(env, key):
        return next((item for item in env.splitlines() if item.startswith(f"{key}=")), None)

    if "migration" in envs:
        for key in config.get("sharedInputs", []):
            if line(envs["application"], key) != line(envs["migration"], key):
                raise ReleaseError(f"Application and migration disagree on {key}.")
    api("registry.testRegistryById", {"registryId": release["registry"]})
    for part, target, env in ((part, targets[part], envs[part]) for part in parts):
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
        since = int(time.time()) - 60  # the deploy's events, allowing for the runner's and server's clocks
        api("compose.deploy", {"composeId": target["composeId"], "title": title})
        wait_for(api, target, title, config, release, part == "migration", sleep, attempts, since)


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
            raise Unanswered(f"Dokploy {operation} failed; response withheld.")
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
    done = "migration completed and application" if "migration" in config else "application"
    print(f"{config['title']} {done} tasks converged.")


if __name__ == "__main__":
    try:
        main()
    except ReleaseError as error:
        print(f"Release refused or failed: {error}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Anything unforeseen could carry a response body: name the failure, not its content.
        print("Release failed unexpectedly; details withheld.", file=sys.stderr)
        sys.exit(1)
