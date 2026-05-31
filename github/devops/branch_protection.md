# Branch protection

Rules that guard the `main` branch. Configured under **Settings → Branches
→ Branch protection rules**. (Contains **organisation-specific** settings.)

## Rules on `main`

- **Require a pull request before merging.** No direct pushes to `main`.
- **Require approvals** — at least 1, and 2 (one from security) for auth/
  secrets changes carrying the `security-review` label.
- **Require status checks to pass** — `build`, `test`, `lint`, and `codeql`
  must all be green.
- **Require branches to be up to date** before merging.
- **Require linear history** — merges must be squash or rebase, no merge
  commits.
- **Require signed commits** — every commit must be verified (GPG/SSH).
- **Do not allow bypassing the above** — the rules apply to administrators
  too.

## Who can push directly

Nobody pushes to `main` directly. The only exceptions are:

- **Renovate** (the dependency-update bot) auto-merges, after checks pass.
- **Rollback automation**, which is logged as an incident.

## Tags

Release tags matching `v*` are **protected** — only the release automation
(and DevOps admins) can create or delete them.

## Why linear history

A linear history makes `git bisect` reliable and the changelog clean. Since
we squash-merge anyway, every entry on `main` is one reviewed change.
