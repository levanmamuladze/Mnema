# Issues and Projects

## Issues

An **issue** is a tracked unit of work — a bug, a task, or a feature
request. Issues are the conversation record for *what* needs doing and
*why*.

Good issues have:

- A clear, specific **title**.
- **Steps to reproduce** (for bugs) or **acceptance criteria** (for
  features).
- **Labels** to categorise (`bug`, `enhancement`, `good first issue`).
- An **assignee** when someone picks it up.

### Linking issues to PRs

Put `Closes #123` (or `Fixes #123`) in a pull request description. When the
PR merges, GitHub **automatically closes** the linked issue.

## Labels we use

- `bug` — something is broken.
- `enhancement` — a new feature or improvement.
- `security-review` — required label for auth/secrets PRs (see code review).
- `good first issue` — suitable for newcomers.
- `blocked` — waiting on something else.

## Milestones

Group issues and PRs into a **milestone** to track progress toward a
release or deadline. The milestone shows percent complete.

## Projects

**GitHub Projects** is a flexible board/table view that pulls in issues and
PRs across repositories. We use a simple board with columns: **Backlog →
In progress → In review → Done.** Items move automatically when their PR is
opened or merged.
