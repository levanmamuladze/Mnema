# Secrets management

How we store and rotate credentials used by GitHub Actions. (Contains
**organisation-specific** policy.)

## Where secrets live

- **Repository secrets** — scoped to a single repo.
- **Environment secrets** — scoped to a deployment environment (e.g.
  `production`), and can require approval before they're exposed.
- **Organisation secrets** — shared across repos, with access lists.

Access them in a workflow as `${{ secrets.NAME }}`. They are encrypted at
rest and masked in logs.

## Our rotation policy

- **Deployment tokens are rotated every 45 days** by the scheduled
  `rotate-tokens` workflow, which runs on a cron trigger and writes the new
  values back as environment secrets.
- **The production database password is rotated every 90 days.**
- **Personal access tokens** used in automation must be **fine-grained**,
  scoped to the minimum repositories, and expire in **90 days** or less.

## Hard rules

1. **Never** commit a secret to a repository. If one is committed, treat it
   as compromised: revoke it, rotate it, then clean history.
2. **Never** echo a secret in workflow logs (GitHub masks known secrets,
   but constructed strings can leak).
3. Production secrets live **only** in the `production` Environment, which
   requires DevOps approval to deploy.

## Secret scanning

**Secret scanning** is enabled organisation-wide. If a known credential
format is pushed, GitHub alerts the security team and (for supported
providers) can automatically revoke it.
