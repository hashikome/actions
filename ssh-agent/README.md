# ssh-agent

Starts `ssh-agent` for the rest of the job, loads the deploy key into it and trusts the server's
pinned host key. Later steps run `ssh`, `scp` or `rsync` with no key file on disk and no
`ssh-keyscan`, which would trust whatever answers on first use.

```yaml
- uses: hashikome/actions/ssh-agent@v1.2.0
  with:
    ssh-key: ${{ secrets.SERVER_SSH_KEY }}
    ssh-host-key: ${{ vars.SERVER_HOST_KEY }}
- run: ssh "$SERVER_SSH_TARGET" docker service ls
```

`ssh-host-key` is the server's `known_hosts` line, for example from `ssh-keyscan -p 22 host` checked
against the server's own `/etc/ssh/ssh_host_ed25519_key.pub`. Keep it in a repository variable: it is
public, and a variable shows in logs where a secret would be masked.

The agent ends with the job's runner. On a self-hosted runner, end it yourself with
`ssh-agent -k` in an `if: always()` step.
