# Agent pipeline

`pipeline/run.py` runs the pipeline for one issue in 8 steps. Deterministic steps (git, pytest,
gh) are plain code; only the thinking is delegated to four opencode agents:

1. preflight — clean tracked files, `origin` is `zxvfc/linkding`
2. issue + branch `agent/issue-N`
3. `planner` writes the spec → `specfile.py` validates it (one retry with the errors)
4. `executor` per task, in parallel per wave; one commit per task, stray changes are reverted
5. `tester` writes `bookmarks/tests/test_agent_<topic>.py`; changes outside tests are reverted
6. `pytest` (+1 fix round by `executor`)
7. `reviewer` checks the diff against the spec → `review.md`
8. publish: push, spec comment in the issue, PR (draft if tests are red), review comment

Results go to GitHub (`zxvfc/linkding`). Merging is up to a human.

Files:

- `pipeline/run.py` — the pipeline
- `pipeline/specfile.py` — spec parser/validator and wave planner, used by `run.py`
- `opencode.json` — agents and their permissions
- `.agents/*.md` — prompts (planner, executor, tester, reviewer)
- `.github/workflows/agents.yaml` — runs the pipeline in GitHub Actions
- `.pipeline/issue-N/` — run outputs: `spec.md`, `review.md`, `pr.md` and `agents/` with every
  agent call (message, answer, raw events); git-ignored

## Run in GitHub Actions

- Put the label `agent` on an issue, or
- Actions → *Agent pipeline* → *Run workflow* with an issue number (optionally `no_publish`).

The run summary is shown on the workflow run page, `.pipeline/` is uploaded as an artifact.
Only one run per issue at a time.

Setup (once):

- A GitHub App installed on the fork with *Contents*, *Issues* and *Pull requests* read/write.
  Secrets `AGENTS_APP_ID` and `AGENTS_APP_PRIVATE_KEY`. An App token is used instead of
  `GITHUB_TOKEN` because PRs opened with `GITHUB_TOKEN` do not trigger CI.
- Optional repository variable `AGENT_MODEL` (`provider/model`) to override the agents' model.
- The workflow must be on the default branch for the label trigger and the *Run workflow* button.

The issue text is untrusted input for agents with shell access and a write token: the workflow
starts only on the `agent` label (needs triage rights) or a manual run, never on new issues.

## Run locally

Needs opencode V2 (`npm install -g @opencode/cli`, binary `opencode2`), `uv`, `gh` (logged in).

```sh
python3 pipeline/run.py 12                        # issue of the fork, publishes
python3 pipeline/run.py 12 --no-publish           # nothing is pushed or posted
python3 pipeline/run.py pipeline/issues/01-x.md   # issue from a file, never publishes
```

An issue number is looked up in `zxvfc/linkding` only. For a file, the first `# ` line is the
title and N comes from the number prefix: `01-...` → `1001`.

Before a run: no uncommitted changes in tracked files (untracked files are kept). After: you are
on `agent/issue-N` (`git log --oneline master..` shows `Tk: ... (#N)`, `Add agent tests (#N)`,
maybe `Fix failing tests (#N)`); `git switch master` to go back. A re-run resets and force-pushes
`agent/issue-N`; an existing PR for the branch is updated.

Model: `--model provider/model` or `AGENT_MODEL`, otherwise the agent's model from
`opencode.json` (passed with `-m`, because `opencode run` ignores it). `OPENCODE_BIN` overrides
the binary name. On rate limits an agent call is retried after 30s, 60s, 120s and 240s.

Validate a spec by hand: `python3 pipeline/specfile.py .pipeline/issue-N/spec.md --repo .`
It parses `# Requirements` and `# Tasks` and checks: `R*` with a `Scenario:` line,
`T* ... | files: ... | depends: ... | covers: ...`, unknown ids, uncovered requirements,
dependency cycles, repo-relative paths that exist (new ones marked `(new)`), then prints the waves.

## Guard rails

In `run.py`:

- GitHub repo is always `zxvfc/linkding`; with `sissbruecker` in `origin` only no-publish runs.
- Pushes only `agent/issue-N`; never merges.
- Commits per task only the task's files; everything else is reverted (new files deleted).

Permissions in `opencode.json` (agents run with `--auto`: anything not denied is allowed):

- All agents: no `cd`, no web fetch/search, no access outside the repo, no subagents;
  shell allow-list only.
- planner / reviewer: read-only (reviewer may use git diff/log/show).
- executor: no edits in `bookmarks/tests/`, `pipeline/`, `.agents/`, `opencode.json`, `.pipeline/`,
  `.git/`, `.github/`; no git writes; may run `uv run pytest` and `uv run ruff`.
- tester: edits only `bookmarks/tests/**`, may run `uv run pytest`.
- MCP servers from the global config (chrome-devtools) are disabled for this project.
