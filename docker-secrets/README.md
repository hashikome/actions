# docker-secrets

Creates a product's missing Docker secrets on its server from its encrypted file in the private
`hashikome/secrets` repository, so a new server gets them from the pipeline. The product's repository
holds no values and no decryption key.

`<product>/docker-secrets.json` in `hashikome/secrets` holds each value under the exact Docker secret
name the stack mounts, every value encrypted with SOPS:

```json
{ "soko_database_url_20261101_v1": "…" }
```

The action reads the repository with a short-lived token from the `hashikome-secrets-reader` GitHub
App (read-only, installed only on `hashikome/secrets`), and decrypts in memory with the product's
Google Cloud KMS key, which only that product's `production` environment may use: GitHub's OIDC token
is exchanged through Workload Identity Federation, so no decryption key is stored in GitHub. Every
value is masked in the log before anything prints.

The calling job needs `environment: production` and `id-token: write`:

```yaml
deploy:
  environment: production
  permissions:
    contents: read
    id-token: write
  steps:
    - uses: hashikome/actions/docker-secrets@v2.0.0
      with:
        product: soko
        reader-client-id: ${{ vars.SECRETS_READER_CLIENT_ID }}
        reader-private-key: ${{ secrets.SECRETS_READER_PRIVATE_KEY }}
        ssh-key: ${{ secrets.SERVER_SSH_KEY }}
        ssh-target: ${{ vars.SERVER_SSH_TARGET }}
        ssh-host-key: ${{ vars.SERVER_HOST_KEY }}
```

Run it before the release. A Docker secret never changes: to rotate a value, add a new name, and
the old one stays until nothing uses it.
