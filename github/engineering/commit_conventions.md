# Commit conventions

We follow **Conventional Commits** so history is readable and releases can
be generated automatically.

## Format

```
<type>(<optional scope>): <subject>

<optional body explaining what and why>
```

## Types

- `feat:` — a new feature.
- `fix:` — a bug fix.
- `docs:` — documentation only.
- `refactor:` — code change that neither fixes a bug nor adds a feature.
- `test:` — adding or fixing tests.
- `chore:` — tooling, dependencies, build.
- `perf:` — a performance improvement.

## Examples

```
feat(auth): add OAuth login with Google
fix(export): handle empty result set without crashing
docs(readme): document the local setup steps
chore(deps): bump fastapi to 0.115
```

## Subject line rules

- Imperative mood: "add", not "added" or "adds".
- No trailing period.
- Keep it under ~70 characters.

## Squash on merge

Because we **squash and merge**, the PR title becomes the single commit on
`main`. Write the PR title as a proper Conventional Commit — that's what
ends up in the public history and the changelog.
