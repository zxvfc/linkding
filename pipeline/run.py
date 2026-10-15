"""Run the agent pipeline for one issue.

Deterministic steps (git, pytest, gh) are done here; only planning, coding, writing tests and
reviewing are delegated to opencode agents (planner, executor, tester, reviewer).

Usage:
    python3 pipeline/run.py 12                       # issue of the fork, publishes
    python3 pipeline/run.py 12 --no-publish          # nothing is pushed or posted
    python3 pipeline/run.py pipeline/issues/01-x.md  # issue from a file, never publishes

Environment:
    AGENT_MODEL   provider/model for all agents (default: the agent's model in opencode.json)
    OPENCODE_BIN  opencode V2 binary (default: opencode2)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import specfile  # noqa: E402

REPO = "zxvfc/linkding"
TESTS_DIR = "bookmarks/tests/"
STEPS = 8
RETRY_DELAYS = (30, 60, 120, 240)  # seconds between attempts on rate limits
RETRYABLE = re.compile(
    r"rate limit|too many requests|\b429\b|overloaded", re.IGNORECASE
)


class Stop(Exception):
    """Unrecoverable failure: the run ends with a summary."""


def log(msg: str = ""):
    print(msg, flush=True)


def sh(*cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    log(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise Stop(f"`{' '.join(cmd)}` failed:\n{result.stdout}{result.stderr}".strip())
    return result


def out(*cmd: str) -> str:
    return sh(*cmd).stdout.strip()


def between(text: str, begin: str, end: str) -> str | None:
    blocks = re.findall(rf"^{begin}\s*$(.*?)^{end}\s*$", text, re.MULTILINE | re.DOTALL)
    return blocks[-1].strip() + "\n" if blocks else None


class Agents:
    def __init__(self, model: str | None):
        self.bin = os.environ.get("OPENCODE_BIN", "opencode2")
        self.model = model
        self.config = json.loads(Path("opencode.json").read_text(encoding="utf-8"))[
            "agents"
        ]
        self.log_dir: Path | None = (
            None  # every call is saved here as NN-agent.md/.jsonl
        )
        self.calls = 0
        self.lock = threading.Lock()

    def run(self, name: str, message: str) -> str:
        for delay in (*RETRY_DELAYS, None):
            try:
                return self.run_once(name, message)
            except RuntimeError as e:
                if delay is None or not RETRYABLE.search(str(e)):
                    raise
                log(f"{e}; retrying in {delay}s")
                time.sleep(delay)

    def run_once(self, name: str, message: str) -> str:
        # `opencode run` ignores the agent's model from opencode.json, so always pass -m
        model = self.model or self.config[name]["model"]
        cmd = [
            self.bin,
            "run",
            "--standalone",
            "--auto",
            "--format",
            "json",
            "--agent",
            name,
            "-m",
            model,
            message,
        ]
        log(f"$ {self.bin} run --agent {name} -m {model} <{len(message)} chars>")
        # stdin must be closed: without a TTY `opencode run` waits for piped input
        result = subprocess.run(
            cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        texts, errors = [], []
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "text":
                texts.append(event["part"]["text"])
            elif event.get("type") == "error":
                errors.append(event["error"].get("message", str(event["error"])))
        self.save(name, message, "\n".join(texts), errors, result)
        if result.returncode != 0 or errors:
            detail = "; ".join(errors) or result.stderr.strip()[-500:]
            raise RuntimeError(f"agent {name} failed: {detail}")
        return "\n".join(texts)

    def save(self, name, message, answer, errors, result):
        if self.log_dir is None:
            return
        with self.lock:
            self.calls += 1
            base = self.log_dir / f"{self.calls:02d}-{name}"
        base.with_suffix(".jsonl").write_text(result.stdout, encoding="utf-8")
        report = f"# {name}\n\n## Message\n\n{message}\n\n## Answer\n\n{answer}\n"
        if errors or result.returncode:
            report += f"\n## Errors (exit {result.returncode})\n\n" + "\n".join(errors)
            report += f"\n\n```\n{result.stderr[-3000:]}\n```\n"
        base.with_suffix(".md").write_text(report, encoding="utf-8")


class Pipeline:
    def __init__(self, target: str, publish: bool, model: str | None):
        self.target = target
        self.publish = publish
        self.agents = Agents(model)
        self.summary: list[tuple[str, str]] = []
        self.n = None
        self.dir = None
        self.spec = None
        self.green = False
        self.untracked_before: set[str] = set()

    def step(self, k: int, title: str):
        log(f"\n== STEP {k}/{STEPS}: {title} ==")

    def row(self, step: str, result: str):
        self.summary.append((step, result))
        log(f"-> {step}: {result}")

    # STEP 1
    def preflight(self):
        self.step(1, "preflight")
        origin = out("git", "remote", "get-url", "origin")
        if "sissbruecker" in origin and self.publish:
            log("origin points to sissbruecker: switching to no-publish mode")
            self.publish = False
        elif REPO not in origin and self.publish:
            raise Stop(f"origin is {origin}, expected {REPO}")
        if out("git", "status", "--porcelain", "--untracked-files=no"):
            raise Stop(
                "uncommitted changes in tracked files: commit or stash them first"
            )
        self.base = out("git", "branch", "--show-current") or "master"
        self.base_sha = out("git", "rev-parse", "HEAD")
        self.untracked_before = set(self.untracked())

    # STEP 2
    def load_issue(self):
        self.step(2, "issue and branch")
        if self.target.isdigit():
            self.n = int(self.target)
            result = sh(
                "gh",
                "issue",
                "view",
                "--repo",
                REPO,
                str(self.n),
                "--json",
                "number,title,body",
                check=False,
            )
            if result.returncode != 0:
                raise Stop(f"Issue {self.n} not found in {REPO}.")
            issue = json.loads(result.stdout)
            self.title, self.body = issue["title"], issue["body"] or ""
        else:
            path = Path(self.target)
            m = re.match(r"(\d+)", path.name)
            if not path.is_file() or not m:
                raise Stop(f"{path}: expected an existing file named like 01-title.md")
            self.n = 1000 + int(m.group(1))
            self.publish = False
            text = path.read_text(encoding="utf-8")
            heading = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
            self.title = heading.group(1).strip() if heading else path.stem
            self.body = text
        self.branch = f"agent/issue-{self.n}"
        sh("git", "switch", "-C", self.branch)
        for d in ("data/assets", "data/favicons", "data/previews"):
            Path(d).mkdir(parents=True, exist_ok=True)
        self.dir = Path(f".pipeline/issue-{self.n}")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.agents.log_dir = self.dir / "agents"
        shutil.rmtree(self.agents.log_dir, ignore_errors=True)
        self.agents.log_dir.mkdir()
        self.row(
            "issue",
            f"#{self.n} {self.title}" + ("" if self.publish else " (no-publish)"),
        )

    # STEP 3
    def plan(self):
        self.step(3, "plan")
        issue = f"Issue #{self.n}: {self.title}\n\n{self.body}"
        message = f"{issue}\n\nExplore the code and return the spec for this issue."
        for attempt in (1, 2):
            text = between(
                self.agents.run("planner", message), "BEGIN_SPEC", "END_SPEC"
            )
            if text is None:
                errors = ["no BEGIN_SPEC/END_SPEC block in the answer"]
            else:
                (self.dir / "spec.md").write_text(text, encoding="utf-8")
                self.spec = specfile.validate(text, repo=".")
                errors = self.spec.errors
            if not errors:
                break
            log("INVALID spec:\n" + "\n".join(f"  - {e}" for e in errors))
            if attempt == 2:
                raise Stop("planner returned an invalid spec twice")
            message = (
                f"{issue}\n\nYour previous spec was rejected by the validator:\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\n\nReturn the corrected full spec."
            )
        self.spec_text = text
        self.waves = specfile.waves(self.spec)
        self.row(
            "plan",
            f"{len(self.spec.requirements)} requirements, {len(self.spec.tasks)} tasks, "
            f"{len(self.waves)} waves",
        )
        if self.publish:
            sh(
                "gh",
                "issue",
                "comment",
                "--repo",
                REPO,
                str(self.n),
                "--body-file",
                str(self.dir / "spec.md"),
            )

    # STEP 4
    def execute(self):
        self.step(4, "execute, wave by wave")
        self.task_status: dict[str, str] = {}
        for i, wave in enumerate(self.waves, 1):
            runnable = []
            for task in wave:
                blocked = [d for d in task.depends if self.task_status.get(d) != "done"]
                if blocked:
                    self.task_status[task.id] = (
                        f"skipped (depends on {', '.join(blocked)})"
                    )
                else:
                    runnable.append(task)
            log(f"wave {i}: " + (", ".join(t.id for t in runnable) or "nothing to run"))
            with ThreadPoolExecutor(max_workers=max(len(runnable), 1)) as pool:
                futures = {
                    t.id: pool.submit(
                        self.agents.run, "executor", self.executor_message(t)
                    )
                    for t in runnable
                }
            for task in runnable:
                try:
                    futures[task.id].result()
                except RuntimeError as e:
                    log(str(e))
                    self.task_status[task.id] = "failed (agent error)"
                    continue
                self.task_status[task.id] = self.commit_task(task)
            self.discard_stray(keep=lambda path: False)
        for task in self.spec.tasks:
            self.row(task.id, self.task_status[task.id])

    def executor_message(self, task: specfile.Task) -> str:
        files = ", ".join(f.path + (" (new)" if f.new else "") for f in task.files)
        line = f"- {task.id}: {task.description} | files: {files}"
        return (
            f"Spec:\n\n{self.spec_text}\nYour task: {task.id}\n{line}\n\n"
            "Implement only this task, change only its files, no tests, no git."
        )

    def commit_task(self, task: specfile.Task) -> str:
        changed = self.changed(task.paths)
        if not changed:
            return "failed (no changes)"
        sh("git", "add", "-A", "--", *changed)
        sh("git", "commit", "-q", "-m", f"{task.id}: {task.description} (#{self.n})")
        return "done"

    # STEP 5
    def write_tests(self):
        self.step(5, "tests by the tester")
        diff = out("git", "diff", "--stat", f"{self.base_sha}..HEAD")
        message = (
            f"Spec:\n\n{self.spec_text}\nChanges made by the executors "
            f"(git diff --stat):\n{diff}\n\nWrite tests for the requirements and run them."
        )
        try:
            self.agents.run("tester", message)
        except RuntimeError as e:
            log(str(e))
        self.discard_stray(keep=lambda path: path.startswith(TESTS_DIR))
        tests = self.changed([TESTS_DIR])
        if tests:
            sh("git", "add", "-A", "--", TESTS_DIR)
            sh("git", "commit", "-q", "-m", f"Add agent tests (#{self.n})")
        self.row("tests written", ", ".join(tests) or "none")

    # STEP 6
    def run_tests(self):
        self.step(6, "test suite")
        ok, line, output = self.pytest()
        if not ok:
            log(output[-4000:])
            message = (
                f"Spec:\n\n{self.spec_text}\nThe test suite fails:\n\n{output[-6000:]}\n\n"
                "Fix the application code so these tests pass; do not edit bookmarks/tests/."
            )
            try:
                self.agents.run("executor", message)
            except RuntimeError as e:
                log(str(e))
            self.discard_stray(keep=lambda path: not path.startswith(TESTS_DIR))
            fixed = [p for p in self.changed([]) if p not in self.untracked_before]
            if fixed:
                sh("git", "add", "-A", "--", *fixed)
                sh("git", "commit", "-q", "-m", f"Fix failing tests (#{self.n})")
            ok, line, output = self.pytest()
        self.green = ok
        self.test_line = line
        self.row("test suite", f"{'GREEN' if ok else 'RED'}: {line}")

    def pytest(self) -> tuple[bool, str, str]:
        result = sh(
            "uv",
            "run",
            "pytest",
            "-n",
            "auto",
            "-q",
            "-p",
            "no:cacheprovider",
            check=False,
        )
        output = result.stdout + result.stderr
        # the summary line, e.g. "2 failed, 1390 passed in 45s" (stderr may hold unrelated warnings)
        lines = [
            x
            for x in result.stdout.splitlines()
            if re.search(r"\d+ (passed|failed|error)", x)
        ]
        line = (
            lines[-1].strip("= ")
            if lines
            else f"pytest exited with {result.returncode}"
        )
        return result.returncode == 0, line, output

    # STEP 7
    def review(self):
        self.step(7, "review")
        message = (
            f"Spec:\n\n{self.spec_text}\nBASE_SHA: {self.base_sha}\n"
            f"Test suite: {'GREEN' if self.green else 'RED'} ({self.test_line})\n\n"
            f"Review `git diff {self.base_sha}..HEAD` against the spec."
        )
        try:
            answer = self.agents.run("reviewer", message)
        except RuntimeError as e:
            answer = str(e)
        raw = between(answer, "BEGIN_REVIEW", "END_REVIEW")
        try:
            review = json.loads(raw or "")
            md = render_review(review)
            verdict = review.get("verdict", "?")
        except (json.JSONDecodeError, AttributeError, TypeError):
            md = f"## Agent review\n\nCould not parse the reviewer's answer:\n\n```\n{answer}\n```\n"
            verdict = "unparsed"
        (self.dir / "review.md").write_text(md, encoding="utf-8")
        self.row("review", verdict)

    # STEP 8
    def publish_pr(self):
        self.step(8, "publish")
        if not self.publish:
            self.row("PR", "not published")
            return
        sh("git", "push", "--force", "-u", "origin", self.branch)
        tasks = "\n".join(
            f"- {t.id}: {t.description} — {self.task_status[t.id]}"
            for t in self.spec.tasks
        )
        pr = (
            f"Closes #{self.n}\n\n## Goal\n\n{self.spec.sections.get('Goal', '')}\n\n"
            f"## Tasks\n\n{tasks}\n\n## Tests\n\n{'GREEN' if self.green else 'RED'}: "
            f"{self.test_line}\n\n<details><summary>Spec</summary>\n\n{self.spec_text}\n"
            "</details>\n\nGenerated by the agent pipeline. Review and merge manually.\n"
        )
        (self.dir / "pr.md").write_text(pr, encoding="utf-8")
        existing = json.loads(
            out(
                "gh",
                "pr",
                "list",
                "--repo",
                REPO,
                "--head",
                self.branch,
                "--json",
                "url",
            )
        )
        if existing:
            url = existing[0]["url"]
            sh(
                "gh",
                "pr",
                "edit",
                "--repo",
                REPO,
                url,
                "--body-file",
                str(self.dir / "pr.md"),
            )
        else:
            cmd = [
                "gh",
                "pr",
                "create",
                "--repo",
                REPO,
                "--base",
                self.base,
                "--head",
                self.branch,
                "--title",
                f"[agents] {self.title} (#{self.n})",
                "--body-file",
                str(self.dir / "pr.md"),
            ]
            url = out(*cmd, *([] if self.green else ["--draft"])).splitlines()[-1]
        sh(
            "gh",
            "pr",
            "comment",
            "--repo",
            REPO,
            url,
            "--body-file",
            str(self.dir / "review.md"),
        )
        self.row("PR", url)

    # helpers
    def untracked(self) -> list[str]:
        return out("git", "ls-files", "--others", "--exclude-standard").splitlines()

    def changed(self, paths: list[str]) -> list[str]:
        # not out(): stripping would cut the leading status column of the first line
        status = sh(
            "git", "status", "--porcelain", "--untracked-files=all", "--", *paths
        )
        return [line[3:].split(" -> ")[-1] for line in status.stdout.splitlines()]

    def discard_stray(self, keep):
        """Revert uncommitted changes for which keep(path) is false; new files are deleted."""
        for path in self.changed([]):
            if keep(path) or path in self.untracked_before:
                continue
            log(f"warning: discarding change outside the allowed files: {path}")
            if (
                sh(
                    "git", "ls-files", "--error-unmatch", "--", path, check=False
                ).returncode
                == 0
            ):
                sh("git", "restore", "--staged", "--worktree", "--", path)
            else:
                Path(path).unlink(missing_ok=True)

    def run(self) -> int:
        code = 0
        try:
            self.preflight()
            self.load_issue()
            self.plan()
            self.execute()
            self.write_tests()
            self.run_tests()
            self.review()
            self.publish_pr()
        except (Stop, RuntimeError) as e:
            log(f"\nSTOPPED: {e}")
            self.row("stopped", str(e).splitlines()[0])
            code = 1
        self.print_summary()
        return code

    def print_summary(self):
        table = "| step | result |\n|---|---|\n" + "\n".join(
            f"| {s} | {r.replace('|', '/')} |" for s, r in self.summary
        )
        if self.n:
            table += f"\n| branch | agent/issue-{self.n} |"
        log(f"\n{table}\n")
        if self.n:
            log("You are on the agent branch; `git switch master` to go back.")
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"## Agent pipeline\n\n{table}\n")


def render_review(review: dict) -> str:
    lines = [
        "## Agent review",
        "",
        f"**Verdict:** {review.get('verdict', '?')}",
        "",
        review.get("summary", ""),
        "",
        "| id | status | comment |",
        "|---|---|---|",
    ]
    for r in review.get("requirements", []):
        comment = str(r.get("comment", "")).replace("|", "/")
        lines.append(f"| {r.get('id', '')} | {r.get('status', '')} | {comment} |")
    issues = review.get("issues", [])
    lines += ["", "### Issues", ""] + ([f"- {i}" for i in issues] or ["- none"])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Run the agent pipeline for an issue")
    parser.add_argument(
        "target", help="issue number in the fork or path to an issue .md file"
    )
    parser.add_argument(
        "--no-publish", action="store_true", help="do not push, comment or open a PR"
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AGENT_MODEL") or None,
        help="provider/model for all agents (default: $AGENT_MODEL or opencode.json)",
    )
    args = parser.parse_args()
    sys.exit(Pipeline(args.target, not args.no_publish, args.model).run())


if __name__ == "__main__":
    main()
