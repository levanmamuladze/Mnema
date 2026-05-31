# Branching strategy

We use **trunk-based development**: short-lived feature branches that merge
back into `main` quickly.

## The rules

- **`main` is always deployable.** Anything merged to `main` could ship to
  production at any time.
- **Feature branches are short-lived** — aim to merge within 2–3 days.
  Long-running branches drift and cause painful merges.
- **No long-lived `develop` branch.** Trunk-based, not Git Flow.

## Branch naming

`<type>/<short-slug>` — for example:

- `feat/oauth-login`
- `fix/null-pointer-on-export`
- `chore/bump-deps`
- `docs/api-readme`

Common types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`.

## Keeping a branch fresh

Before merging, update your branch with the latest `main` (use the
"Update branch" button on the PR, or rebase locally). Resolve conflicts on
your branch, never on `main`.

## Hotfixes

For urgent production fixes, branch from the production tag, fix, and
fast-track the PR. Forward-merge the fix into `main` afterward so it isn't
lost in the next release.
