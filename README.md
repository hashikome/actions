# hashikome/actions

Shared GitHub Actions for Hashikome's products: GitHub's `actions` organisation, for Hashikome. Each
action is one folder, called as `hashikome/actions/<name>@v1.1.0`.

This repository is public and holds logic only. Credentials and anything specific to a product
(targets, services, hosts) stay in that product's repository.

| Action | What it does |
| --- | --- |
| [`dokploy-release`](dokploy-release/README.md) | Migrate, verify, then promote immutable images through Dokploy |
| [`docker-secrets`](docker-secrets/README.md) | Create a product's missing Docker secrets on its server from its GitHub secrets |

Run the tests with `python3 -m unittest discover -s dokploy-release` and
`python3 -m unittest discover -s docker-secrets`.

## Versions

One version covers every action in this repository. Products pin an exact version such as `@v1.1.0`,
or a full commit for a change not published yet, never a moving tag or `main`. A change reaches a
product when that product edits its pin. There are no moving major tags and no automated updates.

Releasing: bump the patch for a fix, the minor for something new, and the major for anything a caller
must change. After `main` is green, run `gh release create v1.0.1 --target main --generate-notes`. Published
versions are immutable; a fix is a new version.

## Licence

[MIT](LICENSE). Developed and maintained by **Shikome Technology Limited**
([@hashikome](https://github.com/hashikome)).
