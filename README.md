# hashikome/actions

Shared GitHub Actions for Hashikome's products: GitHub's `actions` organisation, for Hashikome. Each
action is one folder, called as `hashikome/actions/<name>@<full commit sha>`.

This repository is public and holds logic only. Credentials and anything specific to a product
(targets, services, hosts) stay in that product's repository. Pin a full commit, never a branch, so a
change here reaches a product through a reviewed bump.

| Action | What it does |
| --- | --- |
| [`dokploy-release`](dokploy-release/README.md) | Migrate, verify, then promote immutable images through Dokploy |

Run the tests with `python3 -m unittest discover -s dokploy-release`.
