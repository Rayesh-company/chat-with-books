# Project management

Canonical product: **chat-with-books**. Tracker: GitHub Issues (`aubed9/chat-with-books`). Structured index: GitHub Project [chat-with-books Product Delivery](https://github.com/users/aubed9/projects/3). Adapter config: `docs/agents/github-pm.json`.

Issue bodies are the work contract. Project fields are sortable indexes only. Assignment is the authoritative claim.

## Product phases

| Phase | Name | Purpose |
| --- | --- | --- |
| 0 | Research & business planning | Credible problem/opportunity and bilingual proposal before a Demo |
| 1 | Demo | Convince that the core value proposition justifies an MVP |
| 2 | MVP | Real target users succeed with a minimally complete product |
| 3 | V1 | Reliable enough for repeatable commercial or organizational use |
| 4 | Full product | Operate and evolve the mature product at intended scale |

A phase ends when its exit criteria have evidence and the PM chooses a transition. An empty backlog is not an exit criterion.

## PM labels

Identity:

- `pm:project` — one canonical project record
- `pm:phase` — active phase record (`phase:<n>`)
- `pm:task` — executable work
- `pm:milestone` — roadmap milestone/meta work

Workflow:

- `pm:backlog` — not ready to claim
- `pm:ready` — claimable
- `pm:claimed` — assigned owner; valid on GitHub only when assignee is set
- `pm:in-progress` — actively being done
- `pm:blocked` — cannot proceed
- `pm:review` — validation/review
- `pm:done` — complete

Phase labels: `phase:0` … `phase:4`.

Triage labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`) are a parallel queue. Promote `ready-for-agent` into `pm:ready` before claiming as PM work; do not remove `ready-for-agent` unless asked.

## Work-kind labels

Exactly one per task:

- `work:engineering`
- `work:research`
- `work:product`
- `work:design`
- `work:business`
- `work:docs`
- `work:ops`
- `work:meeting`
- `work:validation`
- `work:access`
- `work:other`

## Effort convention

Story points: **1 / 2 / 3 / 5 / 8**. Forecasts, not promises. Expected shape (focused / half-day / day / multi-day) still belongs in the issue body.

## Sprint cadence

**1-week sprints.** Sprint 1 is the deadline sprint: **4–10 September 2026** (through 19 Shahrivar 1405). Sprint planning commits only `pm:ready` work. Unassigned ready work stays claimable.

## Definition of claim

A teammate **claims** a ticket by becoming its GitHub assignee. That assignment is the lock. `pm:claimed` must not exist without an assignee. Re-read live assignment before claiming; do not steal an existing assignee. Run `project-management prepare <ticket>` before grilling/decision-heavy work or nontrivial prerequisites.

## Tracker notes

- Preferred write path: `github_adapter.py` (`issue-create`, `enroll`, `relate`, `claim`, `state`, `done`).
- Native parent/sub-issue and blocking relationships; do not duplicate dependency truth in comments.
- GitHub Project fields: `PM Status`, `Product Phase`, `Sprint`, `Effort`, `Deadline`, `Technical Depth`, `Work Type`, `Priority`.
- If Projects access drops, continue in Issues-only mode. Labels, assignment, and relationships still apply.
