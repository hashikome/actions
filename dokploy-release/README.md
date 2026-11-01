# dokploy-release

Promotes one release through Dokploy: a one-shot migration Stack first, then the application Stack.
A migration that fails, or can't be proven for this exact attempt, never touches the application.
There is no automatic rollback.

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
| `migration` | The same, plus `service`, `image` (a key of `images`) and `command` (the exact entrypoint) |
| `services` | Each service key, its `image`, and `healthcheck: false` if it has none |
| `images` | Each image's env key and GHCR package |
| `allowedInputs` | Every other env key the Stacks may hold; anything else stops the release |
| `requiredValues` | Inputs that must equal these values on the application Stack |
| `inputs` | Optional: fixed non-secret values, such as a network or secret names. They win over Dokploy's stored values, so a new server needs fewer of those |
| `sharedInputs` | Inputs the application and migration Stacks must agree on |
| `secretNameSuffixes` | Inputs that must look like secret names (default `_SECRET`) |

Stacks must be raw-source, local to the Dokploy host, with auto-deploy off.
