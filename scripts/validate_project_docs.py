"""Enforce the registered project knowledge system and one active document per domain."""

from __future__ import annotations

import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED_OR_EXTERNAL = {
    ".git", "node_modules", ".venv", ".next", "archive", "artifacts",
    "exports", "check", "logs", "test_artifacts", "work",
}


def main() -> int:
    errors: list[str] = []
    project = ROOT / "PROJECT.md"
    if not project.is_file():
        errors.append("required project file missing or empty: PROJECT.md")
        project_text = ""
    else:
        project_text = project.read_text(encoding="utf-8")

    registry_start = project_text.find("## Реестр активных инструкций")
    registry_end = project_text.find("\n## ", registry_start + 1)
    registry = project_text[registry_start:registry_end if registry_end >= 0 else None]
    rows: list[tuple[str, str, str, str, str]] = []
    for line in registry.splitlines():
        match = re.match(
            r"^\| (PC-[A-Z-]+) \| \[[^]]+\]\(([^)]+\.md)\) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$",
            line,
        )
        if match:
            doc_id, path, domain, status, owner = match.groups()
            rows.append((doc_id, path.strip(), domain.strip(), status.strip(), owner.strip()))

    if not rows:
        errors.append("PROJECT.md registry has no parseable instruction rows")
    ids = [row[0] for row in rows]
    paths = [row[1] for row in rows]
    domains = [row[2] for row in rows]
    if len(ids) != len(set(ids)):
        errors.append("PROJECT.md registry contains duplicate Document IDs")
    if len(paths) != len(set(paths)):
        errors.append("PROJECT.md registry contains duplicate instruction paths")
    if len(domains) != len(set(domains)):
        errors.append("PROJECT.md registry must have exactly one ACTIVE document per domain")

    canonical = set(paths)
    if "PROJECT.md" not in canonical or "AGENTS.md" not in canonical:
        errors.append("PROJECT.md and AGENTS.md must both be registered")
    for doc_id, name, domain, status, owner in rows:
        if status != "ACTIVE":
            errors.append(f"registered instruction is not ACTIVE: {name} ({status})")
        path = ROOT / name
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            errors.append(f"required project file missing or empty: {name}")
            continue
        content = path.read_text(encoding="utf-8")
        if f"**Document ID:** {doc_id}" not in content:
            errors.append(f"Document ID metadata does not match registry: {name}")
        if "**Status:** ACTIVE" not in content:
            errors.append(f"instruction metadata must say ACTIVE: {name}")
        if f"**Domain:** {domain}" not in content:
            errors.append(f"instruction domain metadata does not match registry: {name}")
        if f"**Owner:** {owner}" not in content:
            errors.append(f"instruction owner metadata does not match registry: {name}")
        if "**Supersedes:**" not in content or "**Revision history:** Git only" not in content:
            errors.append(f"instruction must define supersession and Git-only revision history: {name}")

    agents = ROOT / "AGENTS.md"
    if agents.is_file() and "PROJECT.md" not in agents.read_text(encoding="utf-8"):
        errors.append("AGENTS.md must route agents to PROJECT.md")

    maintained_roots = (".", "docs", "research", "src", "scripts", "tests", "web", "cabinet")
    for name in maintained_roots:
        folder = ROOT / name
        if not folder.is_dir():
            continue
        for path in folder.rglob("*.md"):
            if any(part in GENERATED_OR_EXTERNAL for part in path.parts):
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative not in canonical:
                errors.append(f"unregistered project Markdown; add to PROJECT.md or remove: {relative}")

    if errors:
        print("Project knowledge validation: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Project knowledge validation: PASS ({len(canonical)} active documents; unique domains; no unregistered Markdown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
