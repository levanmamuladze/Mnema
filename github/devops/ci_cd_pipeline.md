# Our CI/CD pipeline

How code gets from a pull request to production. (Contains
**organisation-specific** details.)

## Continuous integration (on every PR)

Every pull request triggers the `ci.yml` workflow, which runs four required
checks in parallel:

1. `build` — compiles the project.
2. `test` — runs the unit and integration test suites.
3. `lint` — style and static analysis.
4. `codeql` — security code scanning.

All four must be green before the PR can merge (enforced by branch
protection on `main`).

## Continuous deployment (on merge to main)

When a PR merges to `main`, the `deploy.yml` workflow runs:

- **Staging deploy is automatic.** Every merge to `main` deploys to
  staging within minutes.
- **Production deploy is gated.** It requires a manual approval in the
  `production` GitHub Environment from a member of the DevOps team.

## Runners

- CI jobs run on **GitHub-hosted `ubuntu-latest`** runners.
- **Production deploys run on our self-hosted runner `northstar-01`**
  (Ubuntu 22.04, 16 vCPU, 32 GB RAM, located in our `eu-central-1` VPC).
  It's the only runner with network access to the production cluster.

## Release tagging

Tagging a commit `v<MAJOR>.<MINOR>.<PATCH>` triggers `release.yml`, which
builds the release artefacts and creates a GitHub Release with a generated
changelog from the squashed commit messages.

## Rollback

To roll back, re-run the `deploy.yml` workflow targeting the previous
release tag. Rollbacks skip the manual approval gate so they're fast in an
incident.
