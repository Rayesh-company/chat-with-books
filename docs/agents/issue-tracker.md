# Issue tracker: GitLab (self-hosted)

Issues and specs for this project live as GitLab issues on `gitlab.rayesh-team.ir`, project `mohamadreza/chatbot-v1`. Use the `glab` CLI for all operations.

## Conventions

Every command pins the host and repo explicitly (the clone's origin is a different repo):

- **Create an issue**: `glab issue create --hostname gitlab.rayesh-team.ir --repo mohamadreza/chatbot-v1 --title "..." --description "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `glab issue view <number> --hostname gitlab.rayesh-team.ir --repo mohamadreza/chatbot-v1 --comments`.
- **List issues**: `glab issue list --hostname gitlab.rayesh-team.ir --repo mohamadreza/chatbot-v1 --state opened` (add `--label "..."` as needed).
- **Comment on an issue**: `glab issue note <number> --hostname ... --repo ... --message "..."`.
- **Labels**: pass `--label "ready-for-agent"` at create time, or manage via the GitLab UI/API. Create a missing label with `glab label create --name <name> ...`.
- **Close**: `glab issue close <number> --hostname ... --repo ...`.
- **Blocking edges** (used by `/to-tickets`): GitLab issue relations ("blocks"/"is blocked by"); when the CLI cannot set a relation, write a `Blocked by #<n>` line at the top of the description so agents can parse it.

## Pull requests as a triage surface

**MRs as a request surface: no.** _(Set to `yes` if this repo treats external MRs as feature requests.)_

When set to `yes`, MRs run through the same labels and states as issues, using the `glab mr` equivalents.
