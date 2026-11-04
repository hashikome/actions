import copy
from pathlib import Path

import pytest

import release as subject

REVISION = "a" * 40
ATTEMPT = "123-2"
API_IMAGE = f"ghcr.io/hashikome/example/api:{REVISION}@sha256:{'b' * 64}"
WEB_IMAGE = f"ghcr.io/hashikome/example/web:{REVISION}@sha256:{'c' * 64}"
COMMAND = ["dist/migrate.js"]
APP_MANIFEST = "image: ${API_IMAGE:?x}\nweb: ${WEB_IMAGE:?x}\nhost: ${PUBLIC_HOST:?x}\n" \
    "net: ${BACKEND_NETWORK:?x}\nsecret: ${AUTH_SECRET:?x}\nattempt: ${RELEASE_ATTEMPT}\n" \
    "optional: ${ANALYTICS_TOKEN:-}\n"
MIGRATION_MANIFEST = "image: ${API_IMAGE:?x}\nnet: ${BACKEND_NETWORK:?x}\n"
STORED = "PUBLIC_HOST=example.com\nBACKEND_NETWORK=data\nAUTH_SECRET=auth_v1\n# a comment\n"
RELEASE = {
    "revision": REVISION,
    "attempt": ATTEMPT,
    "images": {"API_IMAGE": API_IMAGE, "WEB_IMAGE": WEB_IMAGE},
    "registry": "registry",
}
CONFIG = {
    "title": "example.",
    "dokployUrl": "https://dokploy.example.com",
    "application": {
        "compose": "application",
        "stack": r"example-[a-z0-9]{6}",
        "manifest": "app.yml",
    },
    "migration": {
        "compose": "migration",
        "stack": r"example-release-[a-z0-9]{6}",
        "manifest": "migrate.yml",
        "service": "migration",
        "image": "API_IMAGE",
        "command": COMMAND,
    },
    "services": {
        "api": {"image": "API_IMAGE"},
        "web": {"image": "WEB_IMAGE"},
        "worker": {"image": "API_IMAGE", "healthcheck": False},
    },
    "images": {
        "API_IMAGE": "ghcr.io/hashikome/example/api",
        "WEB_IMAGE": "ghcr.io/hashikome/example/web",
    },
    "allowedInputs": ["PUBLIC_HOST", "BACKEND_NETWORK", "AUTH_SECRET", "ANALYTICS_TOKEN"],
    "requiredValues": {"PUBLIC_HOST": "example.com"},
    "sharedInputs": ["BACKEND_NETWORK"],
}


def target(migration):
    return {
        "composeId": "migration" if migration else "application",
        "appName": "example-release-abc123" if migration else "example-abc123",
        "composeType": "stack",
        "sourceType": "raw",
        "autoDeploy": False,
        "serverId": None,
        "env": STORED,
    }


def migration_container():
    return {
        "Config": {
            "Image": API_IMAGE,
            "Env": [f"RELEASE_ATTEMPT={ATTEMPT}"],
            "Cmd": list(COMMAND),
            "Labels": {
                subject.SWARM_SERVICE: "example-release-abc123_migration",
                subject.SWARM_TASK: "task",
            },
        },
        "State": {"Status": "exited", "ExitCode": 0, "OOMKilled": False},
    }


def test_inputs_in_the_repository_win_over_stored_values(tmp_path):
    (tmp_path / "app.yml").write_text(APP_MANIFEST)
    config = {**CONFIG, "inputs": {"BACKEND_NETWORK": "fixed"}}
    env = subject.environment(STORED, config, RELEASE, "application", tmp_path)
    assert "BACKEND_NETWORK=fixed\n" in env
    assert "BACKEND_NETWORK=data\n" not in env


def test_refuses_an_input_the_configuration_does_not_allow():
    config = {**CONFIG, "inputs": {"SURPRISE": "value"}}
    with pytest.raises(subject.ReleaseError):
        subject.environment(STORED, config, RELEASE, "application", ".")


class Dokploy:
    """A fake Dokploy that records every call."""

    def __init__(self, outcome="complete", previous=False, stops=True):
        self.outcome, self.old_running, self.stops = outcome, previous, stops
        self.calls, self.titles = [], {}

    def __call__(self, operation, payload=None, query=None):
        payload, query = payload or {}, query or {}
        self.calls.append(f"{operation}:{payload.get('composeId') or query.get('composeId') or ''}")
        if operation == "compose.one":
            if query["composeId"] == "previous":
                return {**target(False), "composeId": "previous", "appName": "example-old"}
            return target(query["composeId"] == "migration")
        if operation == "compose.stop":
            self.old_running = not self.stops
            return {}
        if operation == "compose.deploy":
            self.titles[payload["composeId"]] = payload["title"]
            return {}
        if operation == "deployment.allByCompose":
            return [{"title": self.titles.get(query["composeId"]), "status": "done"}]
        if operation == "docker.getContainersByAppNameMatch":
            if query["appName"] == "example-old":
                return [{"state": "running"}] if self.old_running else []
            if query["appName"] == target(True)["appName"]:
                assert "appType" not in query  # Include completed containers.
                return [{"containerId": "a" * 12, "state": "exited"}]
            return [{"containerId": c * 12, "state": "running"} for c in "bcd"]
        if operation == "docker.getConfig":
            if query["containerId"] == "a" * 12:
                container = migration_container()
                if self.outcome == "failed":
                    container["State"]["ExitCode"] = 1
                if self.outcome == "stale":
                    container["Config"]["Env"] = ["RELEASE_ATTEMPT=123-1"]
                if self.outcome == "pending":
                    container["State"]["Status"] = "running"
                return container
            service = {"b": "api", "c": "web", "d": "worker"}[query["containerId"][0]]
            state = {"Status": "running"}
            if service != "worker":
                state["Health"] = {"Status": "healthy"}
            return {
                "Config": {
                    "Image": WEB_IMAGE if service == "web" else API_IMAGE,
                    "Env": [f"RELEASE_ATTEMPT={ATTEMPT}"],
                    "Labels": {subject.SWARM_SERVICE: f"example-abc123_{service}"},
                },
                "State": state,
            }
        return {}


@pytest.fixture
def root(tmp_path):
    (tmp_path / "app.yml").write_text(APP_MANIFEST)
    (tmp_path / "migrate.yml").write_text(MIGRATION_MANIFEST)
    return tmp_path


def render(root, stored=STORED, part="application", config=CONFIG):
    return subject.environment(stored, config, RELEASE, part, root)


def promote(root, api, config=CONFIG):
    subject.promote(api, config, RELEASE, root, lambda: None, 1)


def test_refuses_mutable_foreign_old_revision_and_reused_targets():
    for image in (
        "latest",
        API_IMAGE.replace("hashikome/example", "other/product"),
        API_IMAGE.replace("a" * 40, "d" * 40),
        f"ghcr.io/hashikome/example/api:{REVISION}",
    ):
        with pytest.raises(subject.ReleaseError):
            subject.validate(CONFIG, {**RELEASE, "images": {**RELEASE["images"], "API_IMAGE": image}})
    with pytest.raises(subject.ReleaseError):
        subject.validate(CONFIG, {**RELEASE, "images": {"API_IMAGE": API_IMAGE}})
    reused = copy.deepcopy(CONFIG)
    reused["migration"]["compose"] = "application"
    with pytest.raises(subject.ReleaseError):
        subject.validate(reused, RELEASE)
    subject.validate(CONFIG, RELEASE)


def test_refuses_unknown_duplicate_and_unresolved_inputs(root):
    for stored in (
        STORED + "DATABASE_URL=postgresql://private\n",
        STORED + "PUBLIC_HOST=duplicate\n",
        STORED.replace("BACKEND_NETWORK=data", "BACKEND_NETWORK=SET_NETWORK"),
        STORED.replace("AUTH_SECRET=auth_v1", "AUTH_SECRET=not a secret name"),
        STORED.replace("PUBLIC_HOST=example.com\n", ""),
    ):
        with pytest.raises(subject.ReleaseError):
            render(root, stored)
    rendered = render(root)
    assert f"API_IMAGE={API_IMAGE}\n" in rendered
    assert f"RELEASE_ATTEMPT={ATTEMPT}\n" in rendered
    assert "APP_REPLICAS=1\n" in rendered


def test_refuses_an_unexpected_product_value(root):
    with pytest.raises(subject.ReleaseError):
        render(root, STORED.replace("PUBLIC_HOST=example.com", "PUBLIC_HOST=elsewhere.example"))


def test_only_the_exact_attempt_and_entrypoint_at_exit_zero_prove_migration():
    name = target(True)["appName"]
    state = lambda container: subject.migration_state(container, name, CONFIG, RELEASE)  # noqa: E731
    assert state(migration_container()) == "complete"
    stale = migration_container()
    stale["Config"]["Env"] = ["RELEASE_ATTEMPT=123-1"]
    assert state(stale) == "stale"
    running = migration_container()
    running["State"]["Status"] = "running"
    assert state(running) == "pending"
    failed = migration_container()
    failed["State"]["ExitCode"] = 1
    with pytest.raises(subject.ReleaseError):
        state(failed)
    bypass = migration_container()
    bypass["Config"]["Cmd"] = ["-e", "process.exit(0)"]
    with pytest.raises(subject.ReleaseError):
        state(bypass)


def test_a_bad_migration_never_touches_the_application(root):
    for outcome in ("failed", "stale", "pending"):
        api = Dokploy(outcome)
        with pytest.raises(subject.ReleaseError):
            promote(root, api)
        assert "compose.update:application" not in api.calls
        assert "compose.deploy:application" not in api.calls


def test_migration_comes_first_then_exact_convergence(root):
    api = Dokploy()
    promote(root, api)
    assert api.calls.index("compose.deploy:migration") < api.calls.index("compose.update:application")
    assert [call for call in api.calls if call.startswith("compose.deploy:")] == [
        "compose.deploy:migration",
        "compose.deploy:application",
    ]


def test_a_shared_input_must_agree(root):
    api = Dokploy()
    original = api.__call__

    def differs(operation, payload=None, query=None):
        result = original(operation, payload, query)
        if operation == "compose.one" and query["composeId"] == "migration":
            result["env"] = result["env"].replace("BACKEND_NETWORK=data", "BACKEND_NETWORK=other")
        return result

    with pytest.raises(subject.ReleaseError):
        promote(root, differs)
    assert "compose.deploy:migration" not in api.calls


def test_a_service_key_change_is_configuration():
    renamed = copy.deepcopy(CONFIG)
    renamed["services"] = {
        "example-api": {"image": "API_IMAGE"},
        "example-web": {"image": "WEB_IMAGE"},
        "example-worker": {"image": "API_IMAGE", "healthcheck": False},
    }
    container = {
        "Config": {
            "Image": API_IMAGE,
            "Env": [f"RELEASE_ATTEMPT={ATTEMPT}"],
            "Labels": {subject.SWARM_SERVICE: "example-abc123_example-worker"},
        },
        "State": {"Status": "running"},
    }
    assert subject.application_service(container, "example-abc123", renamed, RELEASE) == "example-worker"


def test_a_healthcheck_is_required_unless_the_service_opts_out():
    container = {
        "Config": {
            "Image": API_IMAGE,
            "Env": [f"RELEASE_ATTEMPT={ATTEMPT}"],
            "Labels": {subject.SWARM_SERVICE: "example-abc123_api"},
        },
        "State": {"Status": "running", "Health": {"Status": "starting"}},
    }
    assert subject.application_service(container, "example-abc123", CONFIG, RELEASE) is None


def test_parses_images_strictly():
    assert subject.parse_images("A=one\n\n  B=two  \n") == {"A": "one", "B": "two"}
    for text in ("no separator", "A=one\nA=two"):
        with pytest.raises(subject.ReleaseError):
            subject.parse_images(text)
