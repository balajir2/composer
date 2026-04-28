"""Audit relative + anchor links in the active docs.

Prints any link whose target file doesn't exist or whose anchor
doesn't match a heading in the target file.  Skips the archive
folder (intentionally frozen — internal link rot is acceptable).
"""

from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def gh_slug(heading: str) -> str:
    """Approximate GitHub's anchor slugger.

    Lowercase, strip backticks, replace any non-(alphanumeric/dash/underscore)
    char with a dash, then collapse RUNS of dashes — but NOT pairs that
    came from punctuation surrounded by spaces (GitHub preserves
    `oab--composer` for "OAB → Composer" because → -> '' leaves two
    surrounding spaces -> two dashes).  We model that by removing
    backticks/parens FIRST (zero-width replace), and dropping
    punctuation as zero-width too — then the remaining spaces become
    dashes via a separate pass.
    """
    s = heading.lower()
    # Zero-width strip: backticks and other "invisible" punctuation
    # don't leave a placeholder space; they vanish.
    s = re.sub(r"[`()\[\]{}<>'\"!?,;:]", "", s)
    # Non-ASCII glyphs (→, em-dash, etc.) are also stripped to nothing.
    s = re.sub(r"[^\x00-\x7f]", "", s)
    # Periods and slashes are stripped zero-width too — matches
    # GitHub's behaviour on numeric prefixes ("2.").
    s = re.sub(r"[.\\/]", "", s)
    # Now turn any remaining whitespace runs into a single dash, but
    # preserve the pattern where multiple spaces should produce
    # multiple dashes.  GitHub's implementation actually collapses
    # whitespace before slugging, so single dash is correct in most
    # cases.  When the surrounding pattern `oab → composer` becomes
    # `oab  composer` (double space from → vanishing) → GitHub
    # collapses to single space first → "oab-composer".  But when
    # checked on real GitHub, it's actually `oab--composer`.  Hedge:
    # accept both single and double dash patterns by NOT collapsing.
    s = re.sub(r"\s", "-", s.strip())
    # Drop any remaining unsupported chars (keep alphanum, dash, underscore).
    s = re.sub(r"[^a-z0-9\-_]", "", s)
    return s


ACTIVE_FILES = [
    "README.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "docs/README.md",
    "docs/overview.md",
    "docs/architecture.md",
    "docs/getting-started.md",
    "docs/designer-guide.md",
    "docs/admin-guide.md",
    "docs/operations.md",
    "docs/api-reference.md",
    "docs/decisions.md",
    "docs/archive/README.md",
    "docs/operations/admin-operations.md",
    "docs/operations/azure-sso.md",
    "docs/operations/llm-keys.md",
    "docs/operations/monitoring.md",
    "docs/operations/postgres-setup.md",
    "docs/operations/vercel-setup.md",
]


def collect_headings() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in ACTIVE_FILES:
        path = os.path.join(ROOT, f)
        if not os.path.exists(path):
            continue
        out[f] = set()
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^#+\s+(.+?)\s*$", line.rstrip())
                if m:
                    out[f].add(gh_slug(m.group(1)))
    return out


def main() -> int:
    headings = collect_headings()
    link_re = re.compile(r"\]\(([^)\s#]*)(#[^)]+)?\)")
    broken: list[tuple[str, int, str, str]] = []

    for f in ACTIVE_FILES:
        path = os.path.join(ROOT, f)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for ln, line in enumerate(fh, 1):
                for m in link_re.finditer(line):
                    target_path = m.group(1) or ""
                    anchor = m.group(2)
                    if target_path.startswith(("http://", "https://", "mailto:")):
                        continue
                    # same-file anchor
                    if not target_path:
                        if anchor and gh_slug(anchor[1:]) not in headings.get(f, set()):
                            broken.append((f, ln, anchor, "anchor not found in same file"))
                        continue
                    # resolve relative
                    file_dir = os.path.dirname(os.path.join(ROOT, f))
                    resolved = os.path.normpath(os.path.join(file_dir, target_path))
                    if not os.path.exists(resolved):
                        broken.append((f, ln, target_path, "file does not exist"))
                        continue
                    if anchor and resolved.endswith(".md"):
                        rel = os.path.relpath(resolved, ROOT).replace("\\", "/")
                        if rel in headings:
                            if gh_slug(anchor[1:]) not in headings[rel]:
                                broken.append((f, ln, f"{target_path}{anchor}", "anchor not found in target"))

    if not broken:
        print("All links resolve.")
        return 0
    print(f"Broken links: {len(broken)}")
    for b in broken:
        print(f"  {b[0]}:{b[1]}  {b[2]!r}  -- {b[3]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
