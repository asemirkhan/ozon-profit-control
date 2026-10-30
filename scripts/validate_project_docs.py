"""Validate the project's canonical-document registry without external packages."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "PROJECT.md"
DOC_ROW = re.compile(r"^\|\s*(PC-[A-Z0-9-]+)\s*\|\s*([^|]+)\|([^\n]+)$", re.MULTILINE)


def main() -> int:
    project_text = PROJECT.read_text(encoding="utf-8")
    errors: list[str] = []
    registered: dict[str, tuple[str, str]] = {}
    for doc_id, status, description in DOC_ROW.findall(project_text):
        paths = re.findall(r"`([^`]+)`", description)
        if not paths:
            errors.append(f"registry row has no document path: {doc_id}")
            continue
        rel_path = paths[-1]
        if doc_id in registered:
            errors.append(f"duplicate DOC-ID in registry: {doc_id}")
        registered[doc_id] = (status.strip(), rel_path)
        path = ROOT / rel_path
        if not path.is_file():
            errors.append(f"registered document missing: {rel_path}")
            continue
        content = path.read_text(encoding="utf-8")
        if doc_id not in content:
            errors.append(f"document does not declare registered DOC-ID {doc_id}: {rel_path}")
        if status.strip() == "CANONICAL / ACTIVE":
            if "CANONICAL / ACTIVE" not in content:
                errors.append(f"active registry entry lacks active status in document: {rel_path}")
            if "**SCOPE:**" not in content:
                errors.append(f"active document lacks SCOPE metadata: {rel_path}")

    active_scopes: dict[str, list[str]] = {}
    for path in (ROOT / "docs").glob("*.md"):
        content = path.read_text(encoding="utf-8")
        if "CANONICAL / ACTIVE" not in content:
            continue
        match = re.search(r"\*\*SCOPE:\*\*\s*([^·\n]+)", content)
        if not match:
            errors.append(f"active document lacks parseable scope: {path.relative_to(ROOT)}")
            continue
        scope = match.group(1).strip().lower()
        active_scopes.setdefault(scope, []).append(str(path.relative_to(ROOT)))
        if str(path.relative_to(ROOT)) not in {v[1] for v in registered.values()}:
            errors.append(f"unregistered active document: {path.relative_to(ROOT)}")
    for scope, paths in active_scopes.items():
        if len(paths) > 1:
            errors.append(f"multiple active documents for scope '{scope}': {', '.join(paths)}")

    if errors:
        print("Project documentation validation: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Project documentation validation: PASS ({len(registered)} registry entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
