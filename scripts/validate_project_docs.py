"""Enforce a single maintained project knowledge base."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED_OR_EXTERNAL = {".git", "node_modules", ".venv", ".next"}


def main() -> int:
    errors: list[str] = []
    for name in ("AGENTS.md", "PROJECT.md"):
        path = ROOT / name
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            errors.append(f"required project file missing or empty: {name}")

    agents = ROOT / "AGENTS.md"
    if agents.is_file() and "PROJECT.md" not in agents.read_text(encoding="utf-8"):
        errors.append("AGENTS.md must route agents to PROJECT.md")

    maintained_roots = ("docs", "research", "src", "scripts", "tests", "web", "cabinet")
    for name in maintained_roots:
        folder = ROOT / name
        if not folder.is_dir():
            continue
        for path in folder.rglob("*.md"):
            if any(part in GENERATED_OR_EXTERNAL for part in path.parts):
                continue
            errors.append(f"duplicate project Markdown; merge into PROJECT.md: {path.relative_to(ROOT)}")

    if errors:
        print("Project knowledge validation: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Project knowledge validation: PASS (single PROJECT.md; AGENTS.md is the entry protocol)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
