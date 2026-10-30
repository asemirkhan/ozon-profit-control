"""Enforce one project knowledge base plus the Codex entry protocol."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    errors: list[str] = []
    agents = ROOT / "AGENTS.md"
    project = ROOT / "PROJECT.md"
    for path in (agents, project):
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            errors.append(f"required project file missing or empty: {path.relative_to(ROOT)}")

    if agents.is_file():
        text = agents.read_text(encoding="utf-8")
        if "PROJECT.md" not in text:
            errors.append("AGENTS.md must route agents to PROJECT.md")

    forbidden_md_roots = (ROOT / "docs", ROOT / "research" / "fictional_store")
    for folder in forbidden_md_roots:
        if folder.is_dir():
            for path in folder.rglob("*.md"):
                errors.append(f"duplicate project knowledge Markdown: {path.relative_to(ROOT)}")

    if errors:
        print("Project knowledge validation: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Project knowledge validation: PASS (AGENTS.md + PROJECT.md; no duplicate Markdown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
