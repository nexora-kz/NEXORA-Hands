from __future__ import annotations
import os
import hashlib
from datetime import datetime, timezone
import json
import time
import uuid
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
INBOX = DATA / "inbox"
OUTBOX = DATA / "outbox"
CONFIG = DATA / "hands_supabase_config.json"
DEFAULT_CONFIG = ROOT / "app" / "hands_supabase_config.json"
PERSISTENT_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "NEXORA" / "Hands"
PERSISTENT_CONFIG = PERSISTENT_ROOT / "data" / "hands_supabase_config.json"
STATE = DATA / "supabase_channel_state.json"
INBOX.mkdir(parents=True, exist_ok=True)
OUTBOX.mkdir(parents=True, exist_ok=True)

def cfg():
    # Reuse persistent machine identity even when launched from %TEMP%.
    for source in (CONFIG, PERSISTENT_CONFIG, DEFAULT_CONFIG):
        if not source.exists(): continue
        try:
            value = json.loads(source.read_text(encoding="utf-8-sig"))
            if value.get("worker_token") or source == DEFAULT_CONFIG: return value
        except Exception: continue
    raise RuntimeError("no usable Hands config")

def token(c):
    return hashlib.sha256(str(c["worker_token"]).encode()).hexdigest()

def register_worker(c):
    rows = request("POST", "/rpc/hands_register_worker", {}, include_worker_token=False)
    rows = rows[0] if isinstance(rows, list) and rows else rows
    if not isinstance(rows, dict): raise RuntimeError("invalid hands_register_worker response")
    worker_id = str(rows.get("worker_id") or "")
    worker_token = str(rows.get("token") or "")
    if not worker_id or not worker_token: raise RuntimeError("worker registration returned empty credentials")
    c["worker_id"] = worker_id; c["worker_token"] = worker_token; c.pop("channel_phrase", None)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    return c

def request(method, path, body=None, include_worker_token=True):
    c = cfg()
    headers = {"apikey": c["publishable_key"], "Prefer": "return=representation", "Content-Type": "application/json"}
    if include_worker_token and c.get("worker_token"):
        headers["x-nexora-hands-token"] = str(c["worker_token"])
    else:
        headers["Authorization"] = "Bearer " + c["publishable_key"]
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(c["rest_url"] + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else None

def heartbeat(c, worker):
    rows = request("POST", "/rpc/hands_worker_heartbeat", {
        "p_worker_id": worker,
        "p_channel_token": token(c),
    })
    if rows is True: return True
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return bool(next(iter(rows[0].values())))
    return bool(rows)

def save_state(**kw):
    state = {}
    if STATE.exists():
        try: state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception: pass
    state.update(kw)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def claim(c, worker):
    q = urllib.parse.urlencode({"channel_token": "eq." + token(c), "worker_id": "eq." + worker, "status": "eq.queued", "order": "created_at.asc", "limit": "1"})
    rows = request("GET", "/hands_commands?" + q)
    if not rows: return None
    cmd = rows[0]
    lease_token = uuid.uuid4().hex
    body = {"status": "claimed", "claimed_at": datetime.now(timezone.utc).isoformat(), "worker_id": worker, "lease_token": lease_token}
    q2 = urllib.parse.urlencode({"id": "eq." + str(cmd["id"]), "status": "eq.queued"})
    updated = request("PATCH", "/hands_commands?" + q2, body)
    return updated[0] if updated else None

def local_active(task_id):
    if (OUTBOX / f"{task_id}.json").exists(): return "result"
    if (INBOX / f"{task_id}.running").exists(): return "running"
    if (INBOX / f"{task_id}.json").exists(): return "queued_local"
    try:
        st = json.loads((DATA / "state.json").read_text(encoding="utf-8"))
        if str(st.get("task_id") or "") == task_id and st.get("status") == "executing": return "executing"
    except Exception: pass
    return "none"

def recover_stale(c, worker):
    stale_seconds = int(c.get("claim_timeout_seconds") or 420)
    cutoff = datetime.now(timezone.utc).timestamp() - stale_seconds
    q = urllib.parse.urlencode({"select": "id,task_id,claimed_at,lease_token", "status": "eq.claimed", "worker_id": "eq." + worker, "order": "claimed_at.asc", "limit": "50"})
    rows = request("GET", "/hands_commands?" + q) or []
    recovered = 0
    guarded = 0
    for row in rows:
        task_id = str(row.get("task_id") or "")
        claimed_at = row.get("claimed_at")
        if not claimed_at or not task_id: continue
        try: ts = datetime.fromisoformat(str(claimed_at).replace("Z", "+00:00")).timestamp()
        except Exception: continue
        if ts >= cutoff: continue
        activity = local_active(task_id)
        if activity in ("result", "running", "executing", "queued_local"):
            guarded += 1
            continue
        q2 = urllib.parse.urlencode({"id": "eq." + str(row["id"]), "status": "eq.claimed", "worker_id": "eq." + worker, "lease_token": "eq." + str(row.get("lease_token") or "")})
        updated = request("PATCH", "/hands_commands?" + q2, {"status": "queued", "claimed_at": None, "worker_id": None, "error": "requeued_stale_claim"})
        if updated: recovered += 1
    return recovered, guarded

def submit_result(c, worker, task_id, result, lease_token, error=None):
    result_error = result.get("error") if isinstance(result, dict) and result.get("status") == "error" else None
    effective_error = error or result_error
    body = {
        "p_task_id": task_id,
        "p_worker_id": worker,
        "p_lease_token": str(lease_token),
        "p_channel_token": token(c),
        "p_result": result,
        "p_error": effective_error,
    }
    rows = request("POST", "/rpc/hands_submit_result_atomic", body) or []
    if isinstance(rows, bool):
        return rows
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return bool(next(iter(rows[0].values())))
    return False

def main():
    c = cfg()
    if not c.get("worker_token"): c = register_worker(c)
    worker = str(c.get("worker_id") or "")
    if not worker: raise RuntimeError("worker_id is empty after registration")
    poll = float(c.get("poll_seconds") or 1.0)
    heartbeat_seconds = max(5.0, float(c.get("heartbeat_seconds") or 15))
    next_heartbeat = 0.0
    save_state(status="starting", worker_id=worker)
    while True:
        try:
            now = time.monotonic()
            if now >= next_heartbeat:
                ok = heartbeat(c, worker)
                save_state(status="heartbeat" if ok else "heartbeat_failed", worker_id=worker, heartbeat_ok=ok, heartbeat_at=time.time())
                next_heartbeat = now + heartbeat_seconds
            recovered, guarded = recover_stale(c, worker)
            if recovered or guarded: save_state(status="recovery_scan", worker_id=worker, recovered=recovered, guarded=guarded)
            cmd = claim(c, worker)
            if cmd:
                task_id = str(cmd.get("task_id") or "")
                if not task_id: raise ValueError("claimed command has no task_id")
                target = INBOX / f"{task_id}.json"
                if target.exists() or (OUTBOX / f"{task_id}.json").exists() or (INBOX / f"{task_id}.running").exists(): raise ValueError(f"duplicate task_id: {task_id}")
                local_command = dict(cmd.get("command") or {})
                local_command["task_id"] = task_id
                target.write_text(json.dumps(local_command, ensure_ascii=False), encoding="utf-8")
                save_state(status="waiting_result", worker_id=worker, task_id=task_id)
                deadline = time.monotonic() + 300
                heartbeat_at = 0.0
                while time.monotonic() < deadline:
                    result_file = OUTBOX / f"{task_id}.json"
                    if result_file.exists():
                        result = json.loads(result_file.read_text(encoding="utf-8"))
                        accepted = submit_result(c, worker, task_id, result, cmd.get("lease_token"))
                        if not accepted:
                            result_file.unlink(missing_ok=True)
                            save_state(status="stale_result_discarded", worker_id=worker, task_id=task_id)
                        else:
                            save_state(status="idle", worker_id=worker, task_id=task_id)
                        break
                    now = time.monotonic()
                    if now >= heartbeat_at:
                        qh = urllib.parse.urlencode({"id": "eq." + str(cmd["id"]), "status": "eq.claimed", "worker_id": "eq." + worker, "lease_token": "eq." + str(cmd.get("lease_token") or "")})
                        request("PATCH", "/hands_commands?" + qh, {"claimed_at": datetime.now(timezone.utc).isoformat()})
                        heartbeat_at = now + max(5.0, min(30.0, float(c.get("heartbeat_seconds") or 15)))
                    now = time.monotonic()
                    if now >= next_heartbeat:
                        ok = heartbeat(c, worker)
                        save_state(heartbeat_ok=ok, heartbeat_at=time.time())
                        next_heartbeat = now + heartbeat_seconds
                    time.sleep(0.5)
                else:
                    submit_result(c, worker, task_id, {"task_id": task_id, "status": "timeout_waiting_result"}, cmd.get("lease_token"), "local result timeout")
                    save_state(status="idle", worker_id=worker, task_id=task_id)
            else:
                save_state(status="idle", worker_id=worker)
        except Exception as e:
            save_state(status="error", worker_id=worker, error=f"{type(e).__name__}: {e}")
        time.sleep(poll)

if __name__ == "__main__": main()
