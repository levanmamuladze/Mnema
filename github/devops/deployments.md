# Deployments & Environments

How we use GitHub **Environments** to control where code goes. (Contains
**organisation-specific** configuration.)

## Environments

We have three GitHub Environments:

- **`development`** — ephemeral, auto-deploys from any feature branch on
  demand via `workflow_dispatch`.
- **`staging`** — auto-deploys on every merge to `main`. No approval.
- **`production`** — deploys only after a **manual approval** from a member
  of the DevOps team. Holds the production secrets.

## Protection rules on `production`

- **Required reviewers**: the `devops` team. A deploy waits, paused, until
  one of them clicks approve.
- **Wait timer**: a 5-minute cool-down after approval before the deploy
  proceeds, so a mistaken approval can be cancelled.
- **Deployment branches**: only `main` and `v*` tags may deploy to
  production.

## The deploy flow

1. PR merges to `main` → staging deploys automatically.
2. Smoke tests run against staging.
3. A DevOps engineer approves the `production` job.
4. After the 5-minute timer, `deploy.yml` runs on `northstar-01` and ships
   to the production cluster.
5. The deployment shows up in the repo's **Deployments** timeline with its
   status (in progress / success / failure).

## Monitoring a deploy

The **Deployments** tab and the Environments page show the live status,
who approved, and a link to the running workflow. Failed deploys are
re-runnable from the same screen.
