# hashikome/actions

Shared GitHub Actions for Hashikome's products: GitHub's `actions` organisation, for Hashikome. Each
action is one folder, called as `hashikome/actions/<name>@v1`.

This repository is public and holds logic only. Credentials and anything specific to a product
(targets, services, hosts) stay in that product's repository.

| Action | What it does |
| --- | --- |
| [`dokploy-release`](dokploy-release/README.md) | Migrate, verify, then promote immutable images through Dokploy |

Run the tests with `python3 -m unittest discover -s dokploy-release`.

## Versions

One version covers every action in this repository. `v1` always points at the latest 1.x release, so
products get fixes without a change of their own; `v1.0.0` never moves. Use a major tag or an exact
version, never `main`.

Releasing: bump the patch for a fix, the minor for something new, and the major for anything a caller
must change. After `main` is green:

```bash
gh release create v1.0.1 --target main --generate-notes
git fetch --tags && git tag -f v1 v1.0.1 && git push -f origin v1
```

## Licence

[MIT](LICENSE). Developed and maintained by **Shikome Technology Limited**
([@hashikome](https://github.com/hashikome)).
