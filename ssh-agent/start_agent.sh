#!/usr/bin/env bash
# Start ssh-agent for the rest of the job, load one private key into it and,
# when given, trust the server's known_hosts line(s).
set -euo pipefail

: "${SSH_KEY:?ssh-key is required}"
: "${GITHUB_ENV:?GITHUB_ENV is not set}"

eval "$(ssh-agent -s)" >/dev/null
trap 'ssh-agent -k >/dev/null' ERR
# Keys pasted from Windows carry CR characters that ssh-add rejects.
ssh-add -q - <<<"${SSH_KEY//$'\r'/}"
trap - ERR
printf 'SSH_AUTH_SOCK=%s\nSSH_AGENT_PID=%s\n' "$SSH_AUTH_SOCK" "$SSH_AGENT_PID" >>"$GITHUB_ENV"

if [ -n "${SSH_HOST_KEY:-}" ]; then
  install -m 700 -d "$HOME/.ssh"
  printf '%s\n' "${SSH_HOST_KEY//$'\r'/}" >>"$HOME/.ssh/known_hosts"
fi
echo "ssh-agent: key loaded"
