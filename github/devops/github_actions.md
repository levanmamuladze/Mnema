# GitHub Actions (basics)

**GitHub Actions** is GitHub's built-in automation and CI/CD system. You
describe automated processes in YAML files and GitHub runs them for you.

## Core concepts

- **Workflow** — an automated process, defined in a `.yml` file under
  `.github/workflows/`. Triggered by events.
- **Event** — what starts a workflow: `push`, `pull_request`, `schedule`
  (cron), `workflow_dispatch` (manual), and more.
- **Job** — a set of steps that run on the same runner. Jobs run in
  parallel by default; you can make them depend on each other with `needs`.
- **Step** — a single task: either a shell command (`run`) or a reusable
  **action** (`uses`).
- **Runner** — the machine that executes a job. GitHub-hosted runners are
  provided by GitHub; self-hosted runners are machines you provide.

## A minimal workflow

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm install
      - run: npm test
```

## Marketplace actions

Thousands of reusable actions exist in the **GitHub Marketplace** —
`actions/checkout`, `actions/setup-node`, `docker/build-push-action`, and
so on. Pin them to a version (or a commit SHA) for reproducibility.

## Secrets in workflows

Workflows access encrypted **secrets** via `${{ secrets.NAME }}`. Never
hard-code credentials in a workflow file. (See secrets management.)
