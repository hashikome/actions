# dokploy-release

Promotes one release through Dokploy: a one-shot migration Stack first, if the product has one, then
the application Stack. A migration that fails, or can't be proven for this exact attempt, never
touches the application.

Rollback is the platform's, not the product's, and it is automatic:

- **An unhealthy service** is put back by Swarm during the deploy (each service's
  `update_config: failure_action: rollback`, with a health check). Swarm's own verdict ends every
  deploy, from the services' update events (`docker.getEvents`, read while waiting, as Docker keeps only
  its last 256 events): **released** once every new task is healthy on this attempt and Swarm reports
  each update `completed` (after its `monitor` period); **rolled back** or **paused** otherwise.
- **A release that doesn't hold** (Swarm rolled it back or paused it, it didn't converge, or the
  product's `accept` command refused it) puts the application's previous release back when the
  product sets `rollback: "true"`: the action keeps the application Stack's file and inputs from
  Dokploy before deploying, restores them, deploys them, waits for Swarm's verdict on them, and fails the
  run with "Rolled back to <revision>". Dokploy then holds what runs again. Leave it off where a
  migration makes the previous application unsafe on the new schema.

Re-running an earlier release doesn't undo one: the action refuses any revision but `main`'s head.
The verdict and any rollback are printed to the log and the run's summary.

```yaml
- uses: actions/checkout@<sha>
- uses: hashikome/actions/dokploy-release@v1.1.0
  with:
    images: |
      API_IMAGE=${{ needs.image.outputs.api }}
      WEB_IMAGE=${{ needs.image.outputs.web }}
    dokploy-api-key: ${{ secrets.DOKPLOY_API_KEY }}
    registry-id: ${{ vars.DOKPLOY_REGISTRY_ID }}
    github-token: ${{ github.token }}
    # optional: the product's check of the live release, and putting the previous one back
    accept: uv run --locked python tools/access-release.py
    rollback: "true"
```

It runs from the `main` branch of the calling repository, and refuses a revision that is no longer
the branch head.

## The product's configuration

`deploy/release.json` in the calling repository says what is specific to that product:

| Key | Meaning |
| --- | --- |
| `title` | Names deployments in Dokploy |
| `dokployUrl` | The Dokploy origin |
| `application` | `compose` (ID), `stack` (pattern for its name), `manifest` (path) |
| `migration` | Optional: the same, plus `service`, `image` (a key of `images`) and `command` (the exact entrypoint). Leave it out when the application migrates itself at start |
| `services` | Each service key, its `image`, and `healthcheck: false` if it has none |
| `images` | Each image's env key and GHCR package |
| `allowedInputs` | Every other env key the Stacks may hold; anything else stops the release |
| `requiredValues` | Inputs that must equal these values on the application Stack |
| `inputs` | Optional: fixed non-secret values, such as a network or secret names. They win over Dokploy's stored values, so a new server needs fewer of those |
| `sharedInputs` | Inputs the application and migration Stacks must agree on |
| `secretNameSuffixes` | Inputs that must look like secret names (default `_SECRET`) |

Stacks must be raw-source, local to the Dokploy host, with auto-deploy off.
