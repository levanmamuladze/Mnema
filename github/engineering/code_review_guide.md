# Code review guide

How we review code. (Some rules here are **organisation policy** — they go
beyond GitHub's defaults.)

## Approvals required

- **Every PR needs at least 1 approval** before it can merge.
- **Any PR that touches authentication, login, sessions, or secrets
  requires 2 approvals — one of them from the security team — plus the
  `security-review` label applied before merge.** This is enforced by
  branch protection on `main`; the merge button stays disabled until both
  conditions are met.
- **Database migrations** need 1 approval **plus** a sign-off from a tech
  lead.

## What reviewers look for

1. **Correctness** — does it do what it claims?
2. **Tests** — are the changes covered? New behaviour needs new tests.
3. **Readability** — would the next person understand this in six months?
4. **Security** — no secrets in code, inputs validated at boundaries.
5. **Scope** — does the PR do one thing, or has it sprawled?

## Etiquette

- Review within **one working day** of being requested.
- Comment on the code, not the coder. Suggest, don't command.
- Use **"Request changes"** sparingly — reserve it for things that must
  change before merge. Use plain comments for nits.
- The author resolves conversations; the reviewer confirms.

## Self-review first

Before requesting review, read your own diff top to bottom. Half of all
review comments are things the author would have caught themselves.
