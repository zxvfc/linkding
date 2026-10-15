# Agent pipeline in opencode

One primary agent `orchestrator` drives four subagents in 8 steps:
preflight → issue + branch → `planner` (spec) → `executor` (parallel per wave) → `tester`
→ `pytest` (+1 fix round by `executor`) → `reviewer` → PR. Everything runs locally in this clone;
only the results go to GitHub (`zxvfc/linkding`): spec comment in the issue, branch
`agent/issue-N`, PR (draft if tests are red), review comment. Merging is up to a human.

Files (committed to the fork; the orchestrator never adds them to task commits):

- `opencode.json` — agents, models, permissions, `/issue` command
- `.agents/*.md` — prompts (orchestrator, planner, executor, tester, reviewer)
- `pipeline/specfile.py` — spec parser/validator; the orchestrator runs it after the planner
  and uses the printed waves to schedule executors
- `pipeline/issues/*.md` — optional local demo issues (not in the repo, create your own;
  the first `# ` line is the title)
- `.pipeline/issue-N/` — run outputs: `spec.md`, `review.md`, `pr.md` (local, not committed)

## Demo

```sh
opencode            # in the repo root; switch agent to "orchestrator" (Tab)
/issue pipeline/issues/01-simple-bookmark-count.md                # from a file: always local only
/issue 12 no-publish                                              # real issue, nothing published
/issue 12                                                         # real issue in the fork, publishes
```

For an issue from a file, N is derived from its number prefix: `01-...` → `1001`, `02-...` → `1002`.

Real issue for the demo: `gh issue create --repo zxvfc/linkding --title "..." --body-file <file.md>`.
An issue number is always looked up in `zxvfc/linkding` only; if it is missing, the run stops.

Before a run: no uncommitted changes in tracked files (untracked files are fine), normally on
`master`, `origin` must point to `zxvfc/linkding` (with `sissbruecker` in the URL only no-publish
runs are allowed). After: you are on `agent/issue-N` (`git log --oneline master..` shows one commit
per task `Tk: ... (#N)`, `Add agent tests (#N)` and maybe `Fix failing tests (#N)`);
`git switch master` to go back. A re-run resets `agent/issue-N` and force-pushes it; an existing PR
for the branch is reused.

Without TUI: `opencode run --agent orchestrator -m <model> --auto "Run the pipeline for: 12"`
(`opencode run` ignores the agent's model from `opencode.json`, so pass `-m` explicitly).

Validate a spec by hand: `python3 pipeline/specfile.py .pipeline/issue-N/spec.md --repo .`
It parses `# Requirements` and `# Tasks` and checks: `R*` with a `Scenario:` line,
`T* ... | files: ... | depends: ... | covers: ...`, unknown ids, uncovered requirements,
dependency cycles, repo-relative paths that exist (new ones marked `(new)`), then prints the waves.
Other sections and which paths agents may touch are not checked here: that is up to the prompts,
permissions and the orchestrator's reverts.

## Guard rails (permissions in opencode.json)

- All agents: no `cd`, no web fetch/search, no access outside the repo; subagents cannot start
  other subagents.
- orchestrator: edits only `.pipeline/**`; may call only the four subagents; shell allow-list only:
  read-only commands, `mkdir -p data/* | .pipeline/*`, specfile.py, `uv run pytest`,
  git status/diff/log/show/add/commit/restore, `git switch -C agent/*`, `git switch master`,
  `git push --force -u origin agent/*`, `gh issue view|comment` and `gh pr list|create|comment`
  with `--repo zxvfc/linkding`. Denied: anything with `sissbruecker`, `gh pr merge`, pushes to master.
- planner / reviewer: read-only (reviewer may use git diff/log/show).
- executor: no edits in `bookmarks/tests/`, `pipeline/`, `.agents/`, `opencode.json`; no git
  writes; may run `uv run pytest` and `uv run ruff`.
- tester: edits only `bookmarks/tests/**` (writes `test_agent_<topic>.py`), may run `uv run pytest`.
- After executors and tester, the orchestrator reverts anything changed outside the allowed files.
- MCP servers from the global config (chrome-devtools) are disabled for this project.
