#!/usr/bin/env python3
"""scripts/backlog.py — K3rNh3LLs.github.io immutable backlog ledger mutation tool.

Port du système SESSION139 de Gen by JRT (scripts/backlog.py) vers K3rNh3LLs.github.io.

THE single entry point to mutate docs/audit/BACKLOG_LEDGER.json. No agent,
no human, no workflow edits the ledger JSON by hand — they all call this CLI.

Pure stdlib (no project import, no package manager) so any model / subagent /
shell can run it via `python3 scripts/backlog.py <cmd>`.

Contract:
  - All mutations under fcntl.flock on docs/audit/BACKLOG_LEDGER.lock
    (re-read-modify-write UNDER the lock — no TOCTOU on stale-release).
  - Atomic file swap via tempfile + os.replace.
  - Append-only global audit trail in docs/audit/BACKLOG_AUDITLOG.jsonl.
  - The ledger + auditlog files are NEVER committed by fourmis (subagents in
    worktrees) — only by the coordinator (they live in the main worktree;
    fourmis call this script via its absolute path so they mutate the MAIN
    ledger, not their worktree copy).

Session UID = CLAUDE_CODE_SESSION_ID env var (harness UUID, model-agnostic).
Commit tag convention: `LEDGER — <ID> — <résumé>`.

Usage:
  python3 scripts/backlog.py seed-from-audit --from <findings.json> [--include-incertain] [--include-refuted] [--limit N]
  python3 scripts/backlog.py import-open-backlog
  python3 scripts/backlog.py next --severity=<critical|high|medium|low|any> [--owner <uid>]
  python3 scripts/backlog.py claim <id> [--owner <uid>]
  python3 scripts/backlog.py heartbeat <id>
  python3 scripts/backlog.py release <id>
  python3 scripts/backlog.py reset <id>            # coordinator cleanup: any status -> open, clears owner/sha/branch/verified
  python3 scripts/backlog.py done <id> <main-sha> <branch>
  python3 scripts/backlog.py verify <id> <CONFIRMED|REFUTED> [--reason "..."] [--verdict-json '{}']
  python3 scripts/backlog.py defer <id> "<reason>"
  python3 scripts/backlog.py add-hors-champ "<desc>" <file:line> <P0|P1|P2|P3|P4>
  python3 scripts/backlog.py add-feedback "<desc>" <file:line> <prio> --source <bug|feedback> [--reporter <name>]
  python3 scripts/backlog.py list [--status <s>] [--severity <s>] [--json]
  python3 scripts/backlog.py show <id>            # full item JSON (for fourmis to fetch details by ID)
  python3 scripts/backlog.py render
  python3 scripts/backlog.py reconcile
  python3 scripts/backlog.py seed-from-feedback
  python3 scripts/backlog.py monthly-audit [--month YYYY-MM]

Exit codes: 0 ok, 1 invariant violation / not found / busy, 2 internal error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import fcntl
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path

# ---------- per-project config (only block that differs between repos) ----------

PROJECT = "K3rNh3LLs.github.io"
BACKLOG_MD_NAME = "BACKLOG.md"
DEFAULT_VERIFY_CMD = "echo Site statique GitHub Pages — verifier le rendu dans le navigateur apres deploiement"

# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "docs" / "audit" / "BACKLOG_LEDGER.json"
LOCK_PATH = ROOT / "docs" / "audit" / "BACKLOG_LEDGER.lock"
AUDITLOG_PATH = ROOT / "docs" / "audit" / "BACKLOG_AUDITLOG.jsonl"
BACKLOG_MD = ROOT / BACKLOG_MD_NAME
AUDIT_JSON = ROOT / "docs" / "audit" / "AUDIT_FINDINGS.json"

LEDGER_BEGIN = "<!--LEDGER:BEGIN-->"
LEDGER_END = "<!--LEDGER:END-->"

SEVERITY_TO_PRIORITY = {
    "critical": "P0",
    "high": "P1",
    "medium": "P2",
    "low": "P4",
}
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
VALID_STATUS = {"open", "in_progress", "done", "deferred", "hors_champ", "refuted"}
VALID_SEVERITY = {"critical", "high", "medium", "low"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def owner_uid(args_owner: str | None = None) -> str:
    if args_owner:
        return args_owner
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or "manual"


def atomic_write(path: Path, content: str) -> None:
    """tempfile in same dir + os.replace (atomic on same fs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.replace(tmp, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class LedgerLock:
    """fcntl.flock exclusive lock on LOCK_PATH for the whole read-modify-write."""

    def __init__(self) -> None:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "LedgerLock":
        self._fd = open(LOCK_PATH, "a+")
        fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc) -> None:
        try:
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
        finally:
            self._fd.close()


def load_ledger() -> dict:
    if not LEDGER_PATH.exists():
        return {"schema_version": 1, "project": PROJECT, "stale_minutes": 60, "items": []}
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: ledger JSON corrupted: {e}", file=sys.stderr)
        sys.exit(2)


def save_ledger(ledger: dict) -> None:
    ledger["schema_version"] = 1
    ledger.setdefault("project", PROJECT)
    ledger.setdefault("stale_minutes", 60)
    atomic_write(LEDGER_PATH, json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")


def append_auditlog(event: dict) -> None:
    AUDITLOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False)
    with open(AUDITLOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_item(item: dict, event: str, actor: str, **extra) -> None:
    entry = {"ts": now_iso(), "event": event, "actor": actor}
    entry.update(extra)
    item.setdefault("audit_log", []).append(entry)
    append_auditlog({"ts": entry["ts"], "event": event, "item_id": item["id"], "actor": actor, **extra})


def stale_items(ledger: dict) -> list[dict]:
    """Return in_progress items whose heartbeat is older than stale_minutes."""
    stale_min = int(ledger.get("stale_minutes", 60))
    cutoff = time.time() - stale_min * 60
    out = []
    for it in ledger.get("items", []):
        if it.get("status") != "in_progress":
            continue
        hb = it.get("heartbeat_at") or it.get("claimed_at")
        if not hb:
            out.append(it)
            continue
        try:
            t = datetime.fromisoformat(hb.replace("Z", "+00:00")).timestamp()
        except ValueError:
            out.append(it)
            continue
        if t < cutoff:
            out.append(it)
    return out


def release_stale(ledger: dict, actor: str = "coordinator") -> int:
    n = 0
    for it in stale_items(ledger):
        it["status"] = "open"
        it["owner_session"] = None
        it["claimed_at"] = None
        it["heartbeat_at"] = None
        log_item(it, "stale-released", actor, reason=f"heartbeat > {ledger.get('stale_minutes',60)}min")
        n += 1
    return n


# ---------- seed ----------

def make_fix_brief(rec: dict) -> str:
    f = rec.get("file", "")
    ln = rec.get("line", 0)
    summ = rec.get("summary", "")
    return (
        f"Confirm the finding at {f}:{ln} (re-open the file, match the verbatim evidence). "
        f"Fix the defect described: {summ}. Keep the fix minimal and real (no mock/placeholder/workaround). "
        f"If the finding is no longer present, return present=false with a reason (do not fabricate a fix)."
    )


def make_verify(rec: dict) -> str:
    return rec.get("verify_cmd") or DEFAULT_VERIFY_CMD


def _new_item(rec: dict, *, source: str, uncertain: bool = False, refuted: bool = False) -> dict:
    return {
        "id": rec["id"], "source": source, "axis": rec.get("axis"),
        "severity": rec.get("severity", "medium"),
        "priority": rec.get("priority") or SEVERITY_TO_PRIORITY.get(rec.get("severity", "medium"), "P3"),
        "status": "refuted" if refuted else "open", "uncertain": uncertain,
        "owner_session": None, "claimed_at": None, "heartbeat_at": None,
        "branch": None, "commit_sha": None,
        "verified": "REFUTED" if refuted else None, "verdict": None,
        "file": rec.get("file"), "line": rec.get("line"),
        "summary": rec.get("summary"), "evidence": rec.get("evidence"),
        "failure_scenario": rec.get("failure_scenario"),
        "fix_brief": "[REFUTED — kept for transparency, do not fix]" if refuted else make_fix_brief(rec),
        "files_to_modify": [] if refuted else ([rec["file"]] if rec.get("file") else []),
        "verify_cmd": "" if refuted else make_verify(rec),
        "deferred_reason": None,
        "verdict_reason": rec.get("verdict_reason"), "audit_log": [],
    }


def cmd_seed_from_audit(args) -> int:
    # --from lets any audit (workflow fan-out, manual review) seed its findings
    # JSON. Defaults to docs/audit/AUDIT_FINDINGS.json.
    audit_path = Path(getattr(args, "from_path", None) or AUDIT_JSON)
    if not audit_path.exists():
        print(f"ERROR: {audit_path} not found", file=sys.stderr)
        return 2
    data = json.loads(audit_path.read_text(encoding="utf-8"))
    with LedgerLock() as _:
        ledger = load_ledger()
        existing_ids = {it["id"] for it in ledger.get("items", [])}
        added = 0
        for rec in data.get("confirmed", []):
            if rec["id"] in existing_ids:
                continue
            if args.limit and added >= args.limit:
                break
            it = _new_item(rec, source="audit")
            log_item(it, "seeded", "coordinator", source="audit", axis=rec.get("axis"))
            ledger.setdefault("items", []).append(it)
            existing_ids.add(it["id"])
            added += 1
        if args.include_incertain:
            for rec in data.get("uncertain", []):
                if rec["id"] in existing_ids:
                    continue
                it = _new_item(rec, source="audit", uncertain=True)
                it["fix_brief"] += " [UNCERTAIN: re-verify at file:line BEFORE fixing; if refuted, call verify REFUTED and return present=false.]"
                log_item(it, "seeded", "coordinator", source="audit", uncertain=True)
                ledger.setdefault("items", []).append(it)
                existing_ids.add(it["id"])
                added += 1
        if args.include_refuted:
            for rec in data.get("refuted", []):
                if rec["id"] in existing_ids:
                    continue
                it = _new_item(rec, source="audit", refuted=True)
                log_item(it, "seeded", "coordinator", source="audit", refuted=True)
                ledger.setdefault("items", []).append(it)
                existing_ids.add(it["id"])
                added += 1
        save_ledger(ledger)
    print(json.dumps({"seeded": added, "total_items": len(ledger.get("items", []))}, ensure_ascii=False, indent=2))
    return 0


# Formats reconnus dans le backlog markdown existant :
#   - [SESSION87-LIVE-001] [P0] [OUVERT] description...        (legacy Gen by JRT)
#   - **ITEM-001** (P2) — description                          (bullet gras)
#   - [ ] `CI-LOCAL-001` [M] P0 — description                  (checkbox + ID backtické)
#   - [ ] [CAD-N1000-RECALC-FINAL] description                 (checkbox + ID crocheté)
LOOSE_BULLET_RE = re.compile(r"^\s*-\s*\*\*([A-Z][A-Z0-9\-]+)\*\*\s*(?:\((P[0-5])\))?\s*—")
LEGACY_ITEM_RE = re.compile(
    r"^\s*-\s*\[([A-Za-z0-9\-]+)\]\s+\[P[0-5]\]\s+\[(OUVERT|FIXÉ|DEFERRED|INFO|PARTIEL)\]"
)
CHECKBOX_BACKTICK_RE = re.compile(r"^\s*-\s*\[ \]\s*.*?`([A-Z][A-Z0-9\-]{2,})`")
CHECKBOX_BRACKET_RE = re.compile(r"^\s*-\s*\[ \]\s*\[([A-Z][A-Z0-9\-]{2,})\]")


def cmd_import_open_backlog(args) -> int:
    if not BACKLOG_MD.exists():
        print(f"ERROR: {BACKLOG_MD_NAME} not found", file=sys.stderr)
        return 2
    text = BACKLOG_MD.read_text(encoding="utf-8")
    in_ledger_block = False
    candidates = []
    for line in text.splitlines():
        if LEDGER_BEGIN in line:
            in_ledger_block = True
            continue
        if LEDGER_END in line:
            in_ledger_block = False
            continue
        if in_ledger_block:
            continue  # never re-import our own render
        m = LEGACY_ITEM_RE.match(line)
        if m and m.group(2) != "OUVERT":
            continue
        m = m or CHECKBOX_BACKTICK_RE.match(line) or CHECKBOX_BRACKET_RE.match(line) or LOOSE_BULLET_RE.match(line)
        if m:
            candidates.append(m.group(1))
    with LedgerLock() as _:
        ledger = load_ledger()
        existing = {it["id"] for it in ledger.get("items", [])}
        added = 0
        for cid in candidates:
            if cid in existing:
                continue
            it = _new_item(
                {"id": cid, "severity": "medium",
                 "summary": f"Imported from {BACKLOG_MD_NAME} ({cid})"},
                source="existing",
            )
            it["axis"] = "backlog-existing"
            it["fix_brief"] = f"Re-verify {cid} at its file:line in {BACKLOG_MD_NAME}, then fix."
            log_item(it, "imported", "coordinator", source="backlog-existing")
            ledger.setdefault("items", []).append(it)
            existing.add(cid)
            added += 1
        save_ledger(ledger)
    print(json.dumps({"imported": added, "candidates_seen": len(candidates), "total_items": len(ledger.get("items", []))}, ensure_ascii=False, indent=2))
    return 0


# ---------- claim / next ----------

def _pick_next(ledger: dict, severity: str, exclude_owner: str | None) -> dict | None:
    """Pick highest-severity open item not owned by exclude_owner."""
    candidates = [it for it in ledger.get("items", [])
                  if it.get("status") == "open" and it.get("id") is not None]
    if severity != "any":
        candidates = [it for it in candidates if it.get("severity") == severity]
    if exclude_owner:
        candidates = [it for it in candidates if it.get("owner_session") != exclude_owner]
    if not candidates:
        return None
    candidates.sort(key=lambda it: (SEVERITY_RANK.get(it.get("severity", "medium"), 2),
                                     it.get("id", "")))
    return candidates[0]


def cmd_next(args) -> int:
    uid = owner_uid(args.owner)
    with LedgerLock() as _:
        ledger = load_ledger()
        released = release_stale(ledger, actor=uid)
        item = _pick_next(ledger, args.severity, exclude_owner=None)
        if item is None:
            save_ledger(ledger)
            print(json.dumps({"claimed": False, "released_stale": released, "reason": "no open item matches"}, ensure_ascii=False))
            return 0
        item["status"] = "in_progress"
        item["owner_session"] = uid
        item["claimed_at"] = now_iso()
        item["heartbeat_at"] = now_iso()
        log_item(item, "claimed", uid, severity=args.severity)
        save_ledger(ledger)
    print(json.dumps({"claimed": True, "released_stale": released, "item": _public(item)}, ensure_ascii=False, indent=2))
    return 0


def cmd_claim(args) -> int:
    uid = owner_uid(args.owner)
    with LedgerLock() as _:
        ledger = load_ledger()
        item = _find(ledger, args.id)
        if item is None:
            print(f"ERROR: item {args.id} not found", file=sys.stderr)
            return 1
        if item.get("status") == "in_progress" and item.get("owner_session") != uid:
            print(json.dumps({"claimed": False, "reason": "held by another live session", "owner": item.get("owner_session")}, ensure_ascii=False))
            return 1
        release_stale(ledger, actor=uid)
        item["status"] = "in_progress"
        item["owner_session"] = uid
        item["claimed_at"] = now_iso()
        item["heartbeat_at"] = now_iso()
        log_item(item, "claimed", uid)
        save_ledger(ledger)
    print(json.dumps({"claimed": True, "item": _public(item)}, ensure_ascii=False, indent=2))
    return 0


def cmd_heartbeat(args) -> int:
    uid = owner_uid()
    with LedgerLock() as _:
        ledger = load_ledger()
        item = _find(ledger, args.id)
        if item is None:
            print(f"ERROR: {args.id} not found", file=sys.stderr)
            return 1
        item["heartbeat_at"] = now_iso()
        log_item(item, "heartbeat", uid)
        save_ledger(ledger)
    print(json.dumps({"heartbeat": True, "id": args.id, "at": item["heartbeat_at"]}, ensure_ascii=False))
    return 0


def cmd_release(args) -> int:
    uid = owner_uid()
    with LedgerLock() as _:
        ledger = load_ledger()
        item = _find(ledger, args.id)
        if item is None:
            print(f"ERROR: {args.id} not found", file=sys.stderr)
            return 1
        item["status"] = "open"
        item["owner_session"] = None
        item["claimed_at"] = None
        item["heartbeat_at"] = None
        log_item(item, "released", uid)
        save_ledger(ledger)
    print(json.dumps({"released": True, "id": args.id}, ensure_ascii=False))
    return 0


def cmd_reset(args) -> int:
    """Coordinator cleanup: reset any item back to open (clears owner/sha/branch/verified)."""
    uid = owner_uid()
    with LedgerLock() as _:
        ledger = load_ledger()
        item = _find(ledger, args.id)
        if item is None:
            print(f"ERROR: {args.id} not found", file=sys.stderr)
            return 1
        item["status"] = "open"
        item["owner_session"] = None
        item["claimed_at"] = None
        item["heartbeat_at"] = None
        item["commit_sha"] = None
        item["branch"] = None
        item["verified"] = None
        item["verdict"] = None
        log_item(item, "reset", uid, reason=args.reason or "coordinator cleanup")
        save_ledger(ledger)
    print(json.dumps({"reset": True, "id": args.id, "reason": args.reason or ""}, ensure_ascii=False))
    return 0


def cmd_done(args) -> int:
    uid = owner_uid()
    with LedgerLock() as _:
        ledger = load_ledger()
        item = _find(ledger, args.id)
        if item is None:
            print(f"ERROR: {args.id} not found", file=sys.stderr)
            return 1
        item["status"] = "done"
        item["commit_sha"] = args.main_sha
        item["branch"] = args.branch
        item["owner_session"] = None
        item["claimed_at"] = None
        item["heartbeat_at"] = None
        log_item(item, "done", uid, sha=args.main_sha, branch=args.branch)
        save_ledger(ledger)
    print(json.dumps({"done": True, "id": args.id, "sha": args.main_sha, "branch": args.branch}, ensure_ascii=False))
    return 0


def cmd_verify(args) -> int:
    uid = owner_uid()
    with LedgerLock() as _:
        ledger = load_ledger()
        item = _find(ledger, args.id)
        if item is None:
            print(f"ERROR: {args.id} not found", file=sys.stderr)
            return 1
        item["verified"] = args.verdict
        if args.reason:
            item["verdict_reason"] = args.reason
        if args.verdict_json:
            try:
                item["verdict"] = json.loads(args.verdict_json)
            except json.JSONDecodeError as e:
                print(f"ERROR: --verdict-json not valid JSON: {e}", file=sys.stderr)
                return 1
        if args.verdict == "REFUTED" and item.get("status") != "done":
            item["status"] = "refuted"
            item["owner_session"] = None
            item["claimed_at"] = None
            item["heartbeat_at"] = None
        log_item(item, "verified", uid, verdict=args.verdict, reason=args.reason or "")
        save_ledger(ledger)
    print(json.dumps({"verified": True, "id": args.id, "verdict": args.verdict}, ensure_ascii=False))
    return 0


def cmd_defer(args) -> int:
    uid = owner_uid()
    with LedgerLock() as _:
        ledger = load_ledger()
        item = _find(ledger, args.id)
        if item is None:
            print(f"ERROR: {args.id} not found", file=sys.stderr)
            return 1
        item["status"] = "deferred"
        item["deferred_reason"] = args.reason
        item["owner_session"] = None
        item["claimed_at"] = None
        item["heartbeat_at"] = None
        log_item(item, "deferred", uid, reason=args.reason)
        save_ledger(ledger)
    print(json.dumps({"deferred": True, "id": args.id, "reason": args.reason}, ensure_ascii=False))
    return 0


def _mint_new_id(ledger: dict, prefix: str) -> str:
    used = {it["id"] for it in ledger.get("items", [])}
    n = 1
    while f"{prefix}-{n:03d}" in used:
        n += 1
    return f"{prefix}-{n:03d}"


def _parse_file_line(s: str) -> tuple[str | None, int | None]:
    m = re.match(r"^(.+?):(\d+)$", s.strip())
    if m:
        return m.group(1), int(m.group(2))
    return s.strip() or None, None


def cmd_add_hors_champ(args) -> int:
    uid = owner_uid()
    file_ref, line_ref = _parse_file_line(args.file_line)
    with LedgerLock() as _:
        ledger = load_ledger()
        new_id = _mint_new_id(ledger, "HC")
        it = _new_item(
            {"id": new_id, "severity": "medium", "priority": args.prio,
             "file": file_ref, "line": line_ref, "summary": args.desc},
            source="discovery",
        )
        it["axis"] = "hors-champ"
        it["fix_brief"] = f"Hors-champ discovery noted by {uid}: {args.desc}"
        it["verify_cmd"] = ""
        log_item(it, "hors-champ", uid, desc=args.desc)
        ledger.setdefault("items", []).append(it)
        save_ledger(ledger)
    print(json.dumps({"added": True, "id": new_id, "desc": args.desc}, ensure_ascii=False))
    return 0


def cmd_add_feedback(args) -> int:
    uid = owner_uid()
    file_ref, line_ref = _parse_file_line(args.file_line)
    with LedgerLock() as _:
        ledger = load_ledger()
        new_id = _mint_new_id(ledger, "FB")
        sev = "high" if args.prio in ("P0", "P1") else ("medium" if args.prio == "P2" else "low")
        it = _new_item(
            {"id": new_id, "severity": sev, "priority": args.prio,
             "file": file_ref, "line": line_ref, "summary": args.desc},
            source=args.source,
        )
        it["axis"] = "feedback"
        it["fix_brief"] = f"{args.source} from {args.reporter or 'unknown'}: {args.desc}"
        it["verify_cmd"] = ""
        log_item(it, "feedback-seeded", uid, source=args.source, reporter=args.reporter or "")
        ledger.setdefault("items", []).append(it)
        save_ledger(ledger)
    print(json.dumps({"added": True, "id": new_id, "source": args.source}, ensure_ascii=False))
    return 0


# ---------- list / find ----------

def _find(ledger: dict, item_id: str) -> dict | None:
    for it in ledger.get("items", []):
        if it.get("id") == item_id:
            return it
    return None


def _public(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "audit_log"}


def cmd_show(args) -> int:
    with LedgerLock() as _:
        ledger = load_ledger()
        item = _find(ledger, args.id)
        if item is None:
            print(f"ERROR: {args.id} not found", file=sys.stderr)
            return 1
    print(json.dumps(item, ensure_ascii=False, indent=2))
    return 0


def cmd_list(args) -> int:
    with LedgerLock() as _:
        ledger = load_ledger()
        items = ledger.get("items", [])
        if args.status:
            items = [it for it in items if it.get("status") == args.status]
        if args.severity:
            items = [it for it in items if it.get("severity") == args.severity]
        out = [_public(it) for it in items]
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for it in out:
            print(f"[{it.get('status','?'):11s}] {it.get('severity','?'):8s} {it.get('id','?'):10s} {it.get('file','') or ''}")
    return 0


# ---------- render ----------

def _render_block(ledger: dict) -> str:
    items = ledger.get("items", [])
    by_status: dict[str, list] = {}
    for it in items:
        by_status.setdefault(it.get("status", "open"), []).append(it)
    lines = [LEDGER_BEGIN, "", f"> Generated {now_iso()} from docs/audit/BACKLOG_LEDGER.json — DO NOT hand-edit this block (run `python3 scripts/backlog.py render`).", ""]
    # counts
    counts = {"open": 0, "in_progress": 0, "done": 0, "deferred": 0, "hors_champ": 0, "refuted": 0}
    for s in counts:
        counts[s] = len(by_status.get(s, []))
    actionable = counts["open"] + counts["in_progress"] + counts["hors_champ"]
    lines.append(f"**Décompte actionnable (hors réfutés): {actionable}** — open={counts['open']} in_progress={counts['in_progress']} done={counts['done']} deferred={counts['deferred']} hors_champ={counts['hors_champ']} | réfutés={counts['refuted']} (non comptés)")
    lines.append("")
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    for status, label in [("in_progress", "🟣 EN COURS"), ("open", "🔵 OUVERT"), ("done", "✅ LIVRÉ"), ("deferred", "⏸ DEFERRED"), ("hors_champ", "🟡 HORS-CHAMP"), ("refuted", "⚫ RÉFUTÉ (non compté)")]:
        bucket = sorted(by_status.get(status, []), key=lambda it: (sev_order.get(it.get("severity", "medium"), 2), it.get("id", "")))
        if not bucket:
            continue
        lines.append(f"### {label} ({len(bucket)})")
        for it in bucket:
            sev = it.get("severity", "?")
            oid = it.get("id", "?")
            f = it.get("file", "")
            ln = it.get("line", "")
            summ = (it.get("summary", "") or "").replace("\n", " ")
            owner = it.get("owner_session") or ""
            sha = it.get("commit_sha") or ""
            sha_short = sha[:8] if sha else ""
            owner_short = owner[:8] if owner else ""
            loc = f"`{f}:{ln}`" if f else ""
            meta = []
            if owner_short:
                meta.append(f"owner=`{owner_short}`")
            if sha_short:
                meta.append(f"commit=`{sha_short}`")
            if it.get("branch"):
                meta.append(f"branch=`{it['branch']}`")
            if it.get("uncertain"):
                meta.append("uncertain")
            meta_s = (" " + " ".join(meta)) if meta else ""
            lines.append(f"- **[{sev.upper()}] {oid}**{meta_s} — {loc} — {summ}")
        lines.append("")
    lines.append(LEDGER_END)
    return "\n".join(lines) + "\n"


def cmd_render(args) -> int:
    with LedgerLock() as _:
        ledger = load_ledger()
        block = _render_block(ledger)
    if not BACKLOG_MD.exists():
        BACKLOG_MD.write_text(f"# {PROJECT} — Backlog\n\n" + block + "\n", encoding="utf-8")
        print(f"render: created {BACKLOG_MD_NAME} with marker block")
        return 0
    text = BACKLOG_MD.read_text(encoding="utf-8")
    if LEDGER_BEGIN in text and LEDGER_END in text:
        # replace existing block
        start = text.index(LEDGER_BEGIN)
        end = text.index(LEDGER_END) + len(LEDGER_END)
        new_text = text[:start] + block + text[end:]
        atomic_write(BACKLOG_MD, new_text)
    else:
        # insert markers once: append at end of file with a header
        if not text.endswith("\n"):
            text += "\n"
        insertion = "\n## Ledger immuable (source de vérité machine)\n\n" + block + "\n"
        atomic_write(BACKLOG_MD, text + insertion)
    print(f"render: {BACKLOG_MD_NAME} marker block updated")
    return 0


# ---------- reconcile ----------

def cmd_reconcile(args) -> int:
    violations = []
    with LedgerLock() as _:
        ledger = load_ledger()
        released = release_stale(ledger, actor="reconcile")
        for it in ledger.get("items", []):
            st = it.get("status")
            if st not in VALID_STATUS:
                violations.append(f"{it.get('id')}: invalid status {st!r}")
            if st == "done" and not it.get("commit_sha"):
                violations.append(f"{it.get('id')}: done without commit_sha")
            if st == "in_progress" and not it.get("owner_session"):
                violations.append(f"{it.get('id')}: in_progress without owner_session")
            if st == "refuted" and it.get("verified") != "REFUTED":
                violations.append(f"{it.get('id')}: refuted but verified={it.get('verified')!r}")
        # marker block sync
        if BACKLOG_MD.exists():
            text = BACKLOG_MD.read_text(encoding="utf-8")
            if LEDGER_BEGIN not in text or LEDGER_END not in text:
                violations.append(f"{BACKLOG_MD_NAME}: missing LEDGER marker block (run `python3 scripts/backlog.py render`)")
        save_ledger(ledger)
    if violations:
        print("RECONCILE FAILED:")
        for v in violations:
            print(f"  - {v}")
        if released:
            print(f"  (auto-released {released} stale claim(s))")
        return 1
    print(json.dumps({"reconcile": "ok", "released_stale": released, "items": len(ledger.get("items", []))}, ensure_ascii=False))
    return 0


# ---------- seed-from-feedback ----------

def cmd_seed_from_feedback(args) -> int:
    """Best-effort ingestion of bugs/feedback from GitHub issues (gh CLI)."""
    added = 0
    errors = []
    uid = owner_uid()

    if shutil.which("gh"):
        try:
            out = subprocess.run(
                ["gh", "issue", "list", "--state", "all", "--limit", "200", "--json", "number,title,labels,createdAt,body"],
                capture_output=True, text=True, timeout=60, cwd=str(ROOT),
            )
            if out.returncode == 0:
                issues = json.loads(out.stdout or "[]")
                for iss in issues:
                    labels = " ".join(l.get("name", "") for l in iss.get("labels", []))
                    is_bug = "bug" in labels.lower()
                    is_fb = "feedback" in labels.lower()
                    if not (is_bug or is_fb):
                        continue
                    src = "bug" if is_bug else "feedback"
                    desc = f"gh#{iss['number']}: {iss.get('title', '')}"
                    with LedgerLock() as _:
                        ledger = load_ledger()
                        existing_summaries = {it.get("summary") for it in ledger.get("items", [])}
                        if desc in existing_summaries:
                            continue
                        new_id = _mint_new_id(ledger, "FB")
                        it = _new_item(
                            {"id": new_id,
                             "severity": "high" if is_bug else "medium",
                             "priority": "P1" if is_bug else "P3",
                             "summary": desc, "evidence": (iss.get("body") or "")[:500]},
                            source=src,
                        )
                        it["axis"] = "feedback"
                        it["fix_brief"] = f"GitHub issue {desc}"
                        it["verify_cmd"] = ""
                        log_item(it, "feedback-seeded", uid, source=src, reporter="github")
                        ledger.setdefault("items", []).append(it)
                        save_ledger(ledger)
                        added += 1
            else:
                errors.append(f"gh: {out.stderr.strip()[:200]}")
        except Exception as e:
            errors.append(f"gh: {type(e).__name__}: {e}")
    else:
        errors.append("gh CLI not found — skipping GitHub issues")

    print(json.dumps({"seeded_from_feedback": added, "errors": errors}, ensure_ascii=False, indent=2))
    return 0


def cmd_monthly_audit(args) -> int:
    month = args.month or datetime.now(timezone.utc).strftime("%Y-%m")
    recap_path = ROOT / "docs" / "audit" / f"MONTHLY_AUDIT_{month}_RECAP.md"
    rc = cmd_seed_from_feedback(args)
    cmd_render(args)
    cmd_reconcile(args)
    with LedgerLock() as _:
        ledger = load_ledger()
    items = ledger.get("items", [])
    from collections import Counter
    by_status = Counter(it.get("status") for it in items)
    by_source = Counter(it.get("source") for it in items)
    recap = [
        f"# Audit mensuel — {PROJECT} — {month}",
        "",
        "> Généré par `python3 scripts/backlog.py monthly-audit`.",
        "",
        "## État du ledger",
        f"- Items total: {len(items)}",
        f"- Par statut: {dict(by_status)}",
        f"- Par source: {dict(by_source)}",
        "",
        "## Étapes à exécuter (voir docs/BACKLOG_LEDGER.md)",
        "1. Lancer un audit fan-out du projet → produire un findings JSON "
        "(clés `confirmed`/`uncertain`/`refuted`, items `{id, severity, file, line, summary, ...}`).",
        "2. `python3 scripts/backlog.py seed-from-audit --from <findings.json>`.",
        "3. `python3 scripts/backlog.py seed-from-feedback` (déjà exécuté ici).",
        "4. `python3 scripts/backlog.py render && python3 scripts/backlog.py reconcile` (déjà exécuté).",
        "5. Revue humaine David — prioriser P0/P1.",
        "",
    ]
    recap_path.parent.mkdir(parents=True, exist_ok=True)
    recap_path.write_text("\n".join(recap) + "\n", encoding="utf-8")
    print(f"WROTE {recap_path}")
    print(json.dumps({"monthly_audit": month, "status": dict(by_status), "source": dict(by_source)}, ensure_ascii=False, indent=2))
    return rc


# ---------- argparse ----------

def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="backlog.py", description=f"Immutable backlog ledger mutation tool ({PROJECT})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("seed-from-audit"); sp.add_argument("--from", dest="from_path", default=None, help="audit findings JSON to seed from (default: docs/audit/AUDIT_FINDINGS.json)"); sp.add_argument("--include-incertain", action="store_true"); sp.add_argument("--include-refuted", action="store_true"); sp.add_argument("--limit", type=int, default=0); sp.set_defaults(func=cmd_seed_from_audit)

    sp = sub.add_parser("import-open-backlog"); sp.set_defaults(func=cmd_import_open_backlog)

    sp = sub.add_parser("next"); sp.add_argument("--severity", default="any", choices=["any", "critical", "high", "medium", "low"]); sp.add_argument("--owner"); sp.set_defaults(func=cmd_next)

    sp = sub.add_parser("claim"); sp.add_argument("id"); sp.add_argument("--owner"); sp.set_defaults(func=cmd_claim)

    sp = sub.add_parser("heartbeat"); sp.add_argument("id"); sp.set_defaults(func=cmd_heartbeat)

    sp = sub.add_parser("release"); sp.add_argument("id"); sp.set_defaults(func=cmd_release)

    sp = sub.add_parser("reset"); sp.add_argument("id"); sp.add_argument("--reason", default=""); sp.set_defaults(func=cmd_reset)

    sp = sub.add_parser("done"); sp.add_argument("id"); sp.add_argument("main_sha"); sp.add_argument("branch"); sp.set_defaults(func=cmd_done)

    sp = sub.add_parser("verify"); sp.add_argument("id"); sp.add_argument("verdict", choices=["CONFIRMED", "REFUTED"]); sp.add_argument("--reason"); sp.add_argument("--verdict-json"); sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("defer"); sp.add_argument("id"); sp.add_argument("reason"); sp.set_defaults(func=cmd_defer)

    sp = sub.add_parser("add-hors-champ"); sp.add_argument("desc"); sp.add_argument("file_line"); sp.add_argument("prio", choices=["P0", "P1", "P2", "P3", "P4"]); sp.set_defaults(func=cmd_add_hors_champ)

    sp = sub.add_parser("add-feedback"); sp.add_argument("desc"); sp.add_argument("file_line"); sp.add_argument("prio", choices=["P0", "P1", "P2", "P3", "P4"]); sp.add_argument("--source", required=True, choices=["bug", "feedback"]); sp.add_argument("--reporter"); sp.set_defaults(func=cmd_add_feedback)

    sp = sub.add_parser("list"); sp.add_argument("--status", choices=sorted(VALID_STATUS)); sp.add_argument("--severity", choices=sorted(VALID_SEVERITY)); sp.add_argument("--json", action="store_true"); sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show"); sp.add_argument("id"); sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("render"); sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("reconcile"); sp.set_defaults(func=cmd_reconcile)

    sp = sub.add_parser("seed-from-feedback"); sp.set_defaults(func=cmd_seed_from_feedback)

    sp = sub.add_parser("monthly-audit"); sp.add_argument("--month"); sp.set_defaults(func=cmd_monthly_audit)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
