# Pull requests

A **pull request (PR)** proposes merging the changes on one branch into
another (usually into `main`). It's where code review happens.

## The basic flow

1. Create a branch off `main`.
2. Make commits on your branch.
3. Push the branch to GitHub.
4. Open a pull request targeting `main`.
5. Teammates review, comment, and request changes.
6. Once approved and checks pass, the PR is merged.
7. Delete the branch.

## Anatomy of a good PR

- **Title** — short and descriptive.
- **Description** — what changed, *why*, and how to test it.
- **Small and focused** — one logical change per PR. Big PRs are slow and
  risky to review.
- **Linked issue** — reference the issue it closes with `Closes #123`.

## Review states

A reviewer can **Comment**, **Approve**, or **Request changes**. A PR with
"Request changes" is blocked until the reviewer is satisfied.

## Merge methods

- **Merge commit** — keeps all commits plus a merge commit.
- **Squash and merge** — combines all the PR's commits into one. This is
  our default (see commit conventions).
- **Rebase and merge** — replays commits for a linear history with no merge
  commit.

## Draft pull requests

Open a **draft PR** for work in progress — it signals "not ready to merge"
and can't be merged until you mark it ready for review.
