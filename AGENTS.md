# Actions repository guide

Read README.md and the README of the action being changed. Releases, pins and tests are in
README.md.

## Working rhythm

Make substantial changes on a local branch, then `git switch main && git merge --ff-only <branch>`
and push `main` with the owner's go. Never push the branch or open a pull request. A release is a
separate step after `main` is green (README.md, Versions).
