# Agent pipeline in opencode

One primary agent `orchestrator` drives four subagents: `planner` → `executor` (parallel per wave)
→ `tester` → `pytest` (+1 fix round) → `reviewer` → PR. Everything runs locally in this clone;
only the results go to GitHub (`zxvfc/linkding`): plan comment in the issue, branch
`agent/issue-N`, PR (draft if tests are red), review comment. Merging is up to a human.

Files (git-excluded via `.git/info/exclude`, never committed):

- `opencode.json` — agents, permissions, `/issue` command
- `.agents/*.md` — prompts (orchestrator, planner, executor, tester, reviewer)
- `pipeline/specfile.py` — spec validator, the orchestrator calls it after the planner
- `pipeline/issues/*.md` — demo issues
- `.pipeline/issue-N/` — spec.md, review.md, pr.md of a run

## Demo

```sh
opencode            # in the repo root; switch agent to "orchestrator" (Tab)
/issue pipeline/issues/01-simple-bookmark-count.md no-publish     # local only
/issue 12                                                         # real issue in the fork, publishes
```

Real issue for the demo: `gh issue create --repo zxvfc/linkding --title "..." --body-file pipeline/issues/02-main-domain-filter.md`.

Before a run: working tree clean, on `master`. After: you are on `agent/issue-N`
(`git log --oneline master..` shows task commits and tests); `git switch master` to go back.
A re-run resets `agent/issue-N`.

Without TUI: `opencode run --agent orchestrator -m opencode/big-pickle --auto "Run the pipeline for: 12"`
(`opencode run` ignores the agent's model, so pass `-m`).

Validate a spec by hand: `python3 pipeline/specfile.py .pipeline/issue-N/spec.md --repo .`

## Guard rails (permissions in opencode.json)

- orchestrator: edits only `.pipeline/**`; shell allow-list only (git add/commit/restore,
  `git switch -C agent/*`, `git push --force -u origin agent/*`, `gh ... --repo zxvfc/linkding ...`,
  pytest, specfile.py); denied: anything with `sissbruecker`, `gh pr merge`, pushes to master, `cd`.
- planner / reviewer: read-only. executor: no edits in `bookmarks/tests/`, `pipeline/`, `.agents/`,
  `opencode.json`. tester: edits only `bookmarks/tests/**`. Subagents cannot start other subagents.
- MCP servers from the global config are disabled for this project.

Free model (`opencode/big-pickle`) can skip steps or hit rate limits: rehearse 2–3 times.
