# docker-secrets

Creates a product's missing Docker secrets on its server, so a new server gets them from the
pipeline. Values stay in the product's GitHub environment, and names stay in its repository.

`deploy/docker-secrets.json` maps each Docker secret to its GitHub secret:

```json
{ "soko_database_url_20261101_v1": "DATABASE_URL" }
```

```yaml
- uses: hashikome/actions/docker-secrets@v1.1.0
  with:
    values: ${{ toJSON(secrets) }}
    ssh-key: ${{ secrets.SERVER_SSH_KEY }}
    ssh-target: ${{ vars.SERVER_SSH_TARGET }}
    ssh-host-key: ${{ vars.SERVER_HOST_KEY }}
```

Run it before the release. A Docker secret never changes: to rotate a value, add a new name, and
the old one stays until nothing uses it.
