from __future__ import annotations
import os
import hashlib
from datetime import datetime, timezone
import json
import time
import uuid
import urllib.parse
import urllib.request
import urllib.error
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

DEFAULT_MAX_PARALLEL_COMMANDS = 5

def sanitize_transport_value(value):
    """Recursively remove PostgreSQL-incompatible U+0000 from JSON payloads."""
    if isinstance(value,str):
        return value.replace("\x00","\\x00")
    if isinstance(value,bytes):
        return value.decode("utf-8","replace").replace("\x00","\\x00")
    if isinstance(value,dict):
        return {
            (sanitize_transport_value(k) if isinstance(k,(str,bytes)) else k): sanitize_transport_value(v)
            for k,v in value.items()
        }
    if isinstance(value,(list,tuple)):
        return [sanitize_transport_value(v) for v in value]
    return value

def max_parallel_commands(c):
    try: value = int(c.get("max_parallel_commands") or DEFAULT_MAX_PARALLEL_COMMANDS)
    except Exception: value = DEFAULT_MAX_PARALLEL_COMMANDS
    return max(1, min(16, value))

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
    safe_body = None if body is None else sanitize_transport_value(body)
    data = None if safe_body is None else json.dumps(safe_body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(c["rest_url"] + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        try: detail=e.read().decode("utf-8","replace")
        except Exception: detail=""
        raise RuntimeError(f"HTTP {e.code} {method} {path}: {detail[:1000]}") from e

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
    state.update(sanitize_transport_value(kw))
    STATE.write_text(json.dumps(sanitize_transport_value(state), ensure_ascii=False, indent=2), encoding="utf-8")

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

def valid_lease_token(value):
    try:
        return bool(uuid.UUID(str(value)))
    except Exception:
        return False

def repair_claim_lease(row, worker):
    lease_token=uuid.uuid4().hex
    q=urllib.parse.urlencode({
        "id":"eq."+str(row["id"]),
        "status":"eq.claimed",
        "worker_id":"eq."+worker,
    })
    body={"lease_token":lease_token,"claimed_at":datetime.now(timezone.utc).isoformat(),"error":None}
    updated=request("PATCH","/hands_commands?"+q,body) or []
    return updated[0] if updated else None

def requeue_claim(row, worker, reason):
    filters={
        "id":"eq."+str(row["id"]),
        "status":"eq.claimed",
        "worker_id":"eq."+worker,
    }
    if valid_lease_token(row.get("lease_token")):
        filters["lease_token"]="eq."+str(row.get("lease_token"))
    q=urllib.parse.urlencode(filters)
    updated=request("PATCH","/hands_commands?"+q,{
        "status":"queued",
        "claimed_at":None,
        "worker_id":None,
        "lease_token":None,
        "error":reason,
    }) or []
    return bool(updated)

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
        if requeue_claim(row,worker,"requeued_stale_claim"):
            recovered += 1
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

def claimed_rows(c, worker):
    q = urllib.parse.urlencode({
        "select":"id,task_id,claimed_at,lease_token",
        "status":"eq.claimed",
        "worker_id":"eq."+worker,
        "order":"claimed_at.asc",
        "limit":"100",
    })
    return request("GET","/hands_commands?"+q) or []

def active_entry(cmd, timeout_seconds=300):
    return {
        "id":cmd.get("id"),
        "task_id":str(cmd.get("task_id") or ""),
        "lease_token":str(cmd.get("lease_token") or ""),
        "deadline":time.monotonic()+float(timeout_seconds),
        "lease_refresh_at":0.0,
    }

def adopt_local_claims(c, worker):
    active={}
    repaired=0
    for row in claimed_rows(c,worker):
        task_id=str(row.get("task_id") or "")
        if not task_id:
            continue
        activity=local_active(task_id)
        if activity not in ("result","running","executing","queued_local"):
            continue
        if not valid_lease_token(row.get("lease_token")):
            fixed=repair_claim_lease(row,worker)
            if not fixed:
                continue
            row=fixed
            repaired+=1
        active[task_id]=active_entry(row)
    return active,repaired

def refresh_lease(worker, meta):
    if not valid_lease_token(meta.get("lease_token")):
        raise ValueError(f"invalid lease token for task {meta.get('task_id')}")
    qh=urllib.parse.urlencode({
        "id":"eq."+str(meta["id"]),
        "status":"eq.claimed",
        "worker_id":"eq."+worker,
        "lease_token":"eq."+str(meta["lease_token"]),
    })
    request("PATCH","/hands_commands?"+qh,{"claimed_at":datetime.now(timezone.utc).isoformat()})

def dispatch_claimed(cmd):
    task_id=str(cmd.get("task_id") or "")
    if not task_id:
        raise ValueError("claimed command has no task_id")
    target=INBOX/f"{task_id}.json"
    if target.exists() or (OUTBOX/f"{task_id}.json").exists() or (INBOX/f"{task_id}.running").exists():
        raise ValueError(f"duplicate task_id: {task_id}")
    local_command=dict(cmd.get("command") or {})
    local_command["task_id"]=task_id
    tmp=target.with_suffix(".tmp")
    tmp.write_text(json.dumps(local_command,ensure_ascii=False),encoding="utf-8")
    os.replace(tmp,target)
    return task_id

def main():
    c=cfg()
    if not c.get("worker_token"):
        c=register_worker(c)
    worker=str(c.get("worker_id") or "")
    if not worker:
        raise RuntimeError("worker_id is empty after registration")
    poll=max(0.1,float(c.get("poll_seconds") or 1.0))
    heartbeat_seconds=max(5.0,float(c.get("heartbeat_seconds") or 15))
    lease_interval=max(5.0,min(30.0,heartbeat_seconds))
    limit=max_parallel_commands(c)
    next_heartbeat=0.0
    active,repaired_legacy=adopt_local_claims(c,worker)
    save_state(status="starting",worker_id=worker,max_parallel_commands=limit,
               active_count=len(active),active_tasks=sorted(active),
               repaired_legacy_claims=repaired_legacy)
    while True:
        try:
            now=time.monotonic()
            if now>=next_heartbeat:
                ok=heartbeat(c,worker)
                save_state(status="heartbeat" if ok else "heartbeat_failed",worker_id=worker,
                           heartbeat_ok=ok,heartbeat_at=time.time(),
                           max_parallel_commands=limit,active_count=len(active),active_tasks=sorted(active))
                next_heartbeat=now+heartbeat_seconds

            recovered,guarded=recover_stale(c,worker)
            if recovered or guarded:
                save_state(status="recovery_scan",worker_id=worker,recovered=recovered,guarded=guarded,
                           max_parallel_commands=limit,active_count=len(active),active_tasks=sorted(active))

            result_submit_errors={}
            for task_id,meta in list(active.items()):
                try:
                    result_file=OUTBOX/f"{task_id}.json"
                    if result_file.exists():
                        result=sanitize_transport_value(json.loads(result_file.read_text(encoding="utf-8")))
                        accepted=submit_result(c,worker,task_id,result,meta["lease_token"])
                        if not accepted:
                            result_file.unlink(missing_ok=True)
                            save_state(status="stale_result_discarded",worker_id=worker,task_id=task_id)
                        active.pop(task_id,None)
                        continue

                    now=time.monotonic()
                    if now>=meta["deadline"]:
                        submit_result(c,worker,task_id,
                                      {"task_id":task_id,"status":"timeout_waiting_result"},
                                      meta["lease_token"],"local result timeout")
                        active.pop(task_id,None)
                        continue

                    if now>=meta["lease_refresh_at"]:
                        refresh_lease(worker,meta)
                        meta["lease_refresh_at"]=now+lease_interval
                except Exception as task_error:
                    # A single malformed/failed result must not block the other
                    # worker slots or stop claiming new commands.
                    result_submit_errors[task_id]=f"{type(task_error).__name__}: {task_error}"
                    continue

            while len(active)<limit:
                cmd=claim(c,worker)
                if not cmd:
                    break
                task_id=dispatch_claimed(cmd)
                active[task_id]=active_entry(cmd)

            save_state(status="busy" if active else "idle",worker_id=worker,
                       heartbeat_ok=True,max_parallel_commands=limit,
                       active_count=len(active),active_tasks=sorted(active),
                       error=("; ".join(f"{k}: {v}" for k,v in sorted(result_submit_errors.items())) if result_submit_errors else ""),
                       result_submit_errors=result_submit_errors)
        except Exception as e:
            save_state(status="error",worker_id=worker,error=f"{type(e).__name__}: {e}",
                       max_parallel_commands=limit,active_count=len(active),active_tasks=sorted(active))
        time.sleep(poll)

if __name__ == "__main__": main()
