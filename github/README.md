# GitHub Demo Knowledge Base

A compact, **deliberately verifiable** knowledge base for demoing Mnema.

Two kinds of facts live here on purpose:

1. **Well-known facts** anyone can confirm (e.g. "GitHub was acquired by
   Microsoft in 2018 for $7.5B"). These let the audience trust the
   answers without reading anything.
2. **Planted facts** — fictional, company-specific details that the base
   language model cannot possibly know (e.g. our CI runner is named
   `northstar-01`). When Mnema answers these correctly, it *proves* the
   answer came from these documents (RAG), not from the model's memory.

## Roles

| Role | Persona | Sees |
|---|---|---|
| `everyone` | Hub | General GitHub knowledge — what it is, history, glossary, plans, account security |
| `engineering` | Octo | Day-to-day dev workflow — pull requests, branching, issues, code review, commits |
| `devops` | Cypher | CI/CD and infrastructure — Actions, pipelines, secrets, branch protection, deployments, billing |

Every demo user is a member of `everyone` plus (optionally) one
specialised role. That makes the RBAC story easy to show: an engineer
can answer workflow questions but is refused secrets/billing questions,
which only `devops` can see.

## Suggested demo script

1. **Trust** — log in as `sam` (everyone): *"What is GitHub and when did
   Microsoft acquire it?"* → 2018, $7.5B. Audience nods.
2. **Proof of RAG** — log in as `alex` (engineering): *"How many approvals
   does a PR that touches authentication need?"* → 2 approvals + the
   `security-review` label. (No public model knows this — it's planted.)
3. **RBAC** — still as `alex`: *"How often do we rotate deployment
   tokens?"* → refused (that's devops-only).
4. **Authorised answer** — log in as `dana` (devops): same question →
   "every 45 days via the `rotate-tokens` workflow."
