#!/usr/bin/env python3
"""scripts/check_backlog_consistency.py — K3rNh3LLs.github.io

Pre-commit gate: validate the immutable backlog ledger invariants
(docs/audit/BACKLOG_LEDGER.json) and the BACKLOG.md render sync.

Port du système SESSION139 de Gen by JRT (scripts/check_backlog_consistency.py).
The ledger is the atomic machine source of truth; BACKLOG.md is its render.

Invariants:
  - no item with status='done' without a commit_sha
  - no item with status='in_progress' without an owner_session
  - no item with status='refuted' without verified=='REFUTED'
  - no item with an invalid status
  - no duplicate item ids
  - if the ledger exists and BACKLOG.md exists, the
    <!--LEDGER:BEGIN-->..<!--LEDGER:END--> block must be present (render in sync).

Also (legacy drift check): any `- [ID] [Px] [FIXÉ]` line in BACKLOG.md
must have a commit referencing its ID, and `[OUVERT]` items must not have a
closing commit on HEAD. Repos that don't use this line format simply have
zero legacy items — the check is a no-op there.

Exit codes:
    0  all invariants hold
    1  drift detected (commit blocked)
    2  internal error

Usage:
    python3 scripts/check_backlog_consistency.py              # check
    python3 scripts/check_backlog_consistency.py --json       # machine-readable
    python3 scripts/check_backlog_consistency.py --strict     # fail on pending-merge
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKLOG_MD = ROOT / "BACKLOG.md"
LEDGER_PATH = ROOT / "docs" / "audit" / "BACKLOG_LEDGER.json"

LEDGER_BEGIN = "<!--LEDGER:BEGIN-->"
LEDGER_END = "<!--LEDGER:END-->"

# Legacy line format: - [ID-001] [P0] [OUVERT] description...
ITEM_RE = re.compile(
    r"^\s*-\s*\[([A-Za-z0-9\-]+)\]\s+\[P[0-5]\]\s+\[(OUVERT|FIXÉ|DEFERRED|INFO|PARTIEL)\]"
)
COMMIT_REF_RE = re.compile(
    r"(?:commit|commits?)\s+[`']?([a-f0-9]{7,40})[`']?", re.IGNORECASE
)


def sh(cmd: str) -> str:
    return subprocess.check_output(
        ["bash", "-c", cmd], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
    ).strip()


def commits_for_item(item_id: str) -> list[str]:
    try:
        out = sh(f"git log --all --format=%H --grep='{item_id}'")
        return [line.strip() for line in out.splitlines() if line.strip()]
    except subprocess.CalledProcessError:
        return []


def commits_on_head_for_item(item_id: str) -> list[str]:
    try:
        out = sh(f"git log HEAD --format=%H --grep='{item_id}'")
        return [line.strip() for line in out.splitlines() if line.strip()]
    except subprocess.CalledProcessError:
        return []


@dataclass(frozen=True)
class BacklogItem:
    item_id: str
    status: str
    line_no: int
    line_text: str
    commit_refs_in_line: tuple[str, ...]


@dataclass
class Result:
    item_id: str
    status: str
    backlog_status: str
    commits_found: list[str]
    detail: str = ""


def parse_backlog() -> list[BacklogItem]:
    if not BACKLOG_MD.is_file():
        return []
    text = BACKLOG_MD.read_text(encoding="utf-8")
    items: list[BacklogItem] = []
    in_ledger_block = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        if LEDGER_BEGIN in line:
            in_ledger_block = True
            continue
        if LEDGER_END in line:
            in_ledger_block = False
            continue
        if in_ledger_block:
            continue
        m = ITEM_RE.match(line)
        if not m:
            continue
        item_id, status = m.group(1), m.group(2)
        refs = tuple(COMMIT_REF_RE.findall(line))
        items.append(BacklogItem(item_id, status, line_no, line, refs))
    return items


def run_checks() -> list[Result]:
    items = parse_backlog()
    results: list[Result] = []
    for item in items:
        if item.status in ("DEFERRED", "INFO"):
            results.append(
                Result(item.item_id, "skip", item.status, [], "informational status")
            )
            continue

        if item.status == "FIXÉ" and item.commit_refs_in_line:
            results.append(
                Result(item.item_id, "ok", item.status,
                       list(item.commit_refs_in_line),
                       f"commit refs in line: {', '.join(item.commit_refs_in_line)}")
            )
            continue

        head_commits = commits_on_head_for_item(item.item_id)
        all_commits = commits_for_item(item.item_id)

        if item.status == "FIXÉ":
            if head_commits:
                results.append(Result(item.item_id, "ok", item.status, head_commits,
                                      f"{len(head_commits)} commit(s) on HEAD"))
            elif all_commits:
                results.append(Result(item.item_id, "pending-merge", item.status, all_commits,
                                      f"{len(all_commits)} commit(s) found but NOT on HEAD ancestry"))
            else:
                results.append(Result(item.item_id, "drift", item.status, [],
                                      "FIXÉ but no commit referencing this ID found anywhere"))

        elif item.status == "OUVERT":
            if head_commits:
                results.append(Result(item.item_id, "drift", item.status, head_commits,
                                      f"OUVERT but {len(head_commits)} commit(s) on HEAD close it"))
            else:
                results.append(Result(item.item_id, "ok", item.status, [],
                                      "no closing commit on HEAD"))

        elif item.status == "PARTIEL":
            results.append(Result(item.item_id, "ok", item.status, head_commits,
                                  f"PARTIEL with {len(head_commits)} commit(s)"
                                  if head_commits else "PARTIEL, no commits yet"))

    return results


def render_text(results: list[Result]) -> str:
    counts = {
        s: sum(1 for r in results if r.status == s)
        for s in ("ok", "drift", "pending-merge", "skip")
    }
    lines = [
        "check_backlog_consistency — BACKLOG.md vs git commits",
        f"  ok={counts['ok']}  drift={counts['drift']}  "
        f"pending-merge={counts['pending-merge']}  skip={counts['skip']}",
        "",
    ]
    for r in results:
        if r.status == "ok":
            lines.append(f"  [OK]    {r.item_id}: {r.detail}")
        elif r.status == "drift":
            lines.append(f"  [DRIFT] {r.item_id}")
            lines.append(f"          BACKLOG says : {r.backlog_status}")
            lines.append(f"          git commits   : {r.commits_found or 'none'}")
            lines.append(f"          ({r.detail})")
        elif r.status == "pending-merge":
            lines.append(f"  [PEND]  {r.item_id}: {r.detail}")
        elif r.status == "skip":
            lines.append(f"  [SKIP]  {r.item_id}: {r.detail}")
    if counts["drift"]:
        lines.extend([
            "",
            "BACKLOG drift detected. Either:",
            "  1. Update BACKLOG.md status to match git reality, OR",
            "  2. Revert the commit that closed the item prematurely.",
            "",
            "Do not bypass this hook with --no-verify; drift compulsion is",
            "exactly what this guards against.",
        ])
    return "\n".join(lines)


VALID_STATUS = {"open", "in_progress", "done", "deferred", "hors_champ", "refuted"}


def check_ledger_invariants() -> list[str]:
    """Validate docs/audit/BACKLOG_LEDGER.json invariants.

    Returns a list of violation strings (empty = ok). Pure read — does not
    take the flock (pre-commit is single-writer anyway).
    """
    violations: list[str] = []
    if not LEDGER_PATH.is_file():
        # ledger absent: not an error (pre-adoption state) — skip ledger checks
        return violations
    try:
        ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"ledger JSON corrupted: {e}"]
    seen_ids: set[str] = set()
    for it in ledger.get("items", []):
        st = it.get("status")
        iid = it.get("id", "?")
        if iid in seen_ids:
            violations.append(f"{iid}: duplicate item id")
        seen_ids.add(iid)
        if st not in VALID_STATUS:
            violations.append(f"{iid}: invalid status {st!r}")
        if st == "done" and not it.get("commit_sha"):
            violations.append(f"{iid}: status=done without commit_sha")
        if st == "in_progress" and not it.get("owner_session"):
            violations.append(f"{iid}: status=in_progress without owner_session")
        if st == "refuted" and it.get("verified") != "REFUTED":
            violations.append(f"{iid}: status=refuted but verified={it.get('verified')!r}")
    if BACKLOG_MD.is_file():
        text = BACKLOG_MD.read_text(encoding="utf-8")
        if LEDGER_BEGIN not in text or LEDGER_END not in text:
            violations.append(
                "BACKLOG.md: missing <!--LEDGER:BEGIN/END--> marker block "
                "(run `python3 scripts/backlog.py render`)")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify BACKLOG.md statuses and BACKLOG_LEDGER.json invariants.",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--strict", action="store_true", help="also treat pending-merge as drift")
    parser.add_argument("--skip-ledger", action="store_true",
                        help="skip BACKLOG_LEDGER.json invariant checks")
    args = parser.parse_args()

    try:
        results = run_checks()
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2

    ledger_violations: list[str] = []
    if not args.skip_ledger:
        ledger_violations = check_ledger_invariants()

    if args.json:
        print(json.dumps({
            "legacy": [r.__dict__ for r in results],
            "ledger_violations": ledger_violations,
        }, indent=2, default=str))
    else:
        print(render_text(results))
        if ledger_violations:
            print("\ncheck_backlog_consistency — BACKLOG_LEDGER.json invariants")
            for v in ledger_violations:
                print(f"  [LEDGER-DRIFT] {v}")

    fail_statuses = {"drift"}
    if args.strict:
        fail_statuses.add("pending-merge")
    legacy_fail = any(r.status in fail_statuses for r in results)
    return 1 if (legacy_fail or bool(ledger_violations)) else 0


if __name__ == "__main__":
    sys.exit(main())
