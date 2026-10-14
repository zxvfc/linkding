"""Parse and validate spec.md produced by the planner.

Format:

    # Goal
    # Context
    # Requirements
    - R1: text
      Scenario: text
    # Tasks
    - T1: text | files: a.py, b.html (new) | depends: none | covers: R1
    # Done when

Usage: python pipeline/specfile.py spec.md [--repo .]
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SECTIONS = ["Goal", "Context", "Requirements", "Tasks", "Done when"]
FORBIDDEN_PREFIXES = ("pipeline/", ".agents/", ".pipeline/", "opencode.json", ".git/")

REQ_RE = re.compile(r"^-\s*(R\d+)\s*:\s*(.+)$")
TASK_RE = re.compile(r"^-\s*(T\d+)\s*:\s*(.+)$")
SCENARIO_RE = re.compile(r"^\s*(?:-\s*)?Scenario\s*:\s*(.+)$", re.IGNORECASE)


@dataclass
class TaskFile:
    path: str
    new: bool = False


@dataclass
class Task:
    id: str
    description: str
    files: list[TaskFile] = field(default_factory=list)
    depends: list[str] = field(default_factory=list)
    covers: list[str] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]


@dataclass
class Requirement:
    id: str
    text: str
    scenario: str = ""


@dataclass
class Spec:
    sections: dict[str, str]
    requirements: list[Requirement]
    tasks: list[Task]
    errors: list[str]

    def task(self, task_id: str) -> Task:
        return next(t for t in self.tasks if t.id == task_id)


def _split_list(value: str) -> list[str]:
    value = value.strip()
    if value.lower() in ("", "none", "-", "нет"):
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_task(task_id: str, rest: str, errors: list[str]) -> Task:
    parts = [p.strip() for p in rest.split("|")]
    task = Task(id=task_id, description=parts[0])
    seen = set()
    for part in parts[1:]:
        if ":" not in part:
            errors.append(f"{task_id}: cannot parse fragment '{part}'")
            continue
        key, value = part.split(":", 1)
        key = key.strip().lower()
        seen.add(key)
        if key == "files":
            for item in _split_list(value):
                new = bool(re.search(r"\(new\)\s*$", item, re.IGNORECASE))
                path = re.sub(r"\(new\)\s*$", "", item, flags=re.IGNORECASE).strip()
                path = path.strip("`").removeprefix("./")
                task.files.append(TaskFile(path, new))
        elif key == "depends":
            task.depends = _split_list(value)
        elif key == "covers":
            task.covers = _split_list(value)
        else:
            errors.append(f"{task_id}: unknown key '{key}'")
    for key in ("files", "depends", "covers"):
        if key not in seen:
            errors.append(f"{task_id}: missing '{key}:'")
    return task


def parse(text: str) -> Spec:
    errors: list[str] = []
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^#\s+(.+?)\s*$", line)
        if m:
            current = m.group(1)
            if current in sections:
                errors.append(f"duplicate section '# {current}'")
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    for name in SECTIONS:
        if name not in sections:
            errors.append(f"missing section '# {name}'")
        elif not "\n".join(sections[name]).strip():
            errors.append(f"section '# {name}' is empty")
    for name in sections:
        if name not in SECTIONS:
            errors.append(f"unexpected section '# {name}'")

    requirements: list[Requirement] = []
    for line in sections.get("Requirements", []):
        m = REQ_RE.match(line.strip())
        s = SCENARIO_RE.match(line)
        if m:
            requirements.append(Requirement(m.group(1), m.group(2).strip()))
        elif s and requirements:
            requirements[-1].scenario = s.group(1).strip()

    tasks: list[Task] = []
    for line in sections.get("Tasks", []):
        m = TASK_RE.match(line.strip())
        if m:
            tasks.append(_parse_task(m.group(1), m.group(2), errors))
        elif line.strip().startswith("-"):
            errors.append(f"cannot parse task line: {line.strip()}")

    return Spec(
        sections={k: "\n".join(v).strip() for k, v in sections.items()},
        requirements=requirements,
        tasks=tasks,
        errors=errors,
    )


def _find_cycle(tasks: list[Task]) -> list[str] | None:
    graph = {t.id: [d for d in t.depends] for t in tasks}
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node):
        state[node] = 1
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in graph:
                continue
            if state.get(dep) == 1:
                return stack[stack.index(dep):] + [dep]
            if state.get(dep) is None:
                found = visit(dep)
                if found:
                    return found
        state[node] = 2
        stack.pop()
        return None

    for node in graph:
        if state.get(node) is None:
            found = visit(node)
            if found:
                return found
    return None


def validate(text: str, repo: str | Path | None = None) -> Spec:
    spec = parse(text)
    errors = spec.errors

    req_ids = [r.id for r in spec.requirements]
    task_ids = [t.id for t in spec.tasks]
    for kind, ids in (("requirement", req_ids), ("task", task_ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            errors.append(f"duplicate {kind} ids: {', '.join(dupes)}")
    if not req_ids:
        errors.append("no requirements (expected '- R1: ...')")
    if not task_ids:
        errors.append("no tasks (expected '- T1: ... | files: ... | depends: ... | covers: ...')")

    for r in spec.requirements:
        if not r.scenario:
            errors.append(f"{r.id}: missing 'Scenario:' line")

    covered = set()
    for t in spec.tasks:
        if not t.files:
            errors.append(f"{t.id}: empty files list")
        for dep in t.depends:
            if dep not in task_ids:
                errors.append(f"{t.id}: depends on unknown task {dep}")
            if dep == t.id:
                errors.append(f"{t.id}: depends on itself")
        for c in t.covers:
            if c not in req_ids:
                errors.append(f"{t.id}: covers unknown requirement {c}")
            covered.add(c)
        for f in t.files:
            if f.path.startswith("/") or ".." in Path(f.path).parts:
                errors.append(f"{t.id}: path must be relative to repo: {f.path}")
            elif f.path.startswith(FORBIDDEN_PREFIXES):
                errors.append(f"{t.id}: path is not allowed: {f.path}")
            elif repo is not None:
                exists = (Path(repo) / f.path).exists()
                if not f.new and not exists:
                    errors.append(f"{t.id}: file does not exist (mark new files with '(new)'): {f.path}")

    for r in req_ids:
        if r not in covered:
            errors.append(f"{r}: not covered by any task")

    cycle = _find_cycle(spec.tasks)
    if cycle:
        errors.append(f"dependency cycle: {' -> '.join(cycle)}")

    return spec


def waves(spec: Spec) -> list[list[Task]]:
    """Group tasks into waves: each wave depends only on earlier waves."""
    remaining = {t.id: t for t in spec.tasks}
    done: set[str] = set()
    result = []
    while remaining:
        ready = [t for t in remaining.values() if all(d in done for d in t.depends)]
        if not ready:
            raise ValueError("dependency cycle")
        ready.sort(key=lambda t: int(t.id[1:]))
        result.append(ready)
        for t in ready:
            done.add(t.id)
            del remaining[t.id]
    return result


def main():
    parser = argparse.ArgumentParser(description="Validate spec.md")
    parser.add_argument("spec")
    parser.add_argument("--repo", default=None, help="check that task files exist in this repo")
    args = parser.parse_args()

    spec = validate(Path(args.spec).read_text(encoding="utf-8"), args.repo)
    if spec.errors:
        print("INVALID spec:")
        for e in spec.errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"OK: {len(spec.requirements)} requirements, {len(spec.tasks)} tasks")
    for i, wave in enumerate(waves(spec), 1):
        print(f"  wave {i}: " + ", ".join(f"{t.id} ({', '.join(t.paths)})" for t in wave))


if __name__ == "__main__":
    main()
