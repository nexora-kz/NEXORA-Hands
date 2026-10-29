import argparse, hashlib, json, os, shutil, subprocess, sys, time, urllib.request, msvcrt
from pathlib import Path

PAYLOAD=("start.ps1","stop.ps1","self-test.ps1","app/hands.py","app/supabase_channel.py","app/hands_supabase_config.json","app/agent_control.py","app/agent_updater.py")
def digest(p):
    h=hashlib.sha256()
    with open(p,"rb") as f:
        for b in iter(lambda:f.read(1048576),b""): h.update(b)
    return h.hexdigest()
def save(p,o): p.write_text(json.dumps(o,ensure_ascii=False,indent=2),encoding="utf-8")
def copyset(src,dst):
    for rel in PAYLOAD:
        s=src/rel
        if s.exists():
            d=dst/rel; d.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(s,d)
def stop(root,own):
    q=str(root).lower().replace("'","''")
    ps=f"""$all=@(Get-CimInstance Win32_Process); $agents=@($all|Where-Object {{$_.ProcessId -ne {own} -and $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains('{q}') -and ($_.CommandLine -match 'hands\\.py|supabase_channel\\.py')}}); $agents|Sort-Object ProcessId -Unique|ForEach-Object {{Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue}}"""
    subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",ps],capture_output=True); time.sleep(2)
def healthy(root,after,timeout=90):
    channel_state=root/"data/supabase_channel_state.json"
    executor_state=root/"data/state.json"
    expected=digest(root/"app/hands.py")
    for _ in range(timeout):
        time.sleep(1)
        try:
            s=json.loads(channel_state.read_text(encoding="utf-8-sig"))
            e=json.loads(executor_state.read_text(encoding="utf-8-sig"))
            transport_ok=(s.get("heartbeat_ok") and s.get("executor_online") and not s.get("queue_stalled")
                          and float(s.get("channel_started_at") or 0)>=after-3)
            executor_ok=(e.get("loaded_hands_sha256")==expected
                         and float(e.get("executor_started_at") or 0)>=after-3
                         and int(e.get("operation_count") or 0)>0)
            if transport_ok and executor_ok:return True
        except (OSError,ValueError,TypeError): pass
    return False
def require_idle(root):
    """Fail closed: do not interrupt tasks during a release or rollback."""
    state_path = root / "data" / "state.json"
    channel_path = root / "data" / "supabase_channel_state.json"
    for path in (state_path, channel_path):
        if not path.is_file():
            raise RuntimeError("cannot verify idle state: " + str(path))
    executor = json.loads(state_path.read_text(encoding="utf-8-sig"))
    channel = json.loads(channel_path.read_text(encoding="utf-8-sig"))
    if (int(executor.get("active_count") or 0) or executor.get("active_tasks")
            or int(channel.get("active_count") or 0) or channel.get("active_tasks")):
        raise RuntimeError("agent has active executor/transport tasks; update deferred")
    if not channel.get("heartbeat_ok") or not channel.get("executor_online") or channel.get("queue_stalled"):
        raise RuntimeError("agent not healthy; update deferred")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--root",required=True); ap.add_argument("--release-sha"); ap.add_argument("--rollback",action="store_true"); a=ap.parse_args()
    root=Path(a.root).resolve(); data=root/"data"; data.mkdir(exist_ok=True)
    lock_file=open(data/"agent_update.lock","a+b")
    try:
        msvcrt.locking(lock_file.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        lock_file.close(); raise RuntimeError("another agent update/rollback is already running")
    stage=root/"update-stage-native"; previous=root/"previous-verified"; rp=data/"agent_update_report.json"
    rep={"ok":False,"mode":"rollback" if a.rollback else "update","release_sha":a.release_sha,"started_at":time.time()}; save(rp,rep)
    runtime_changed=False
    try:
        if a.rollback:
            if not previous.is_dir(): raise RuntimeError("previous verified runtime unavailable")
            source=previous
        else:
            sha=(a.release_sha or "").strip().lower()
            if len(sha)!=40 or any(c not in "0123456789abcdef" for c in sha): raise ValueError("full Git SHA required")
            if stage.exists(): shutil.rmtree(stage)
            (stage/"app").mkdir(parents=True)
            base=f"https://raw.githubusercontent.com/nexora-kz/NEXORA-Hands/{sha}/"
            for rel in PAYLOAD:
                d=stage/rel; d.parent.mkdir(parents=True,exist_ok=True)
                with urllib.request.urlopen(base+rel,timeout=30) as r:d.write_bytes(r.read())
                if d.stat().st_size<100:raise RuntimeError("invalid payload "+rel)
            cp=subprocess.run([sys.executable,"-m","py_compile",str(stage/"app/hands.py"),str(stage/"app/supabase_channel.py"),str(stage/"app/agent_control.py"),str(stage/"app/agent_updater.py")],capture_output=True,text=True)
            if cp.returncode: raise RuntimeError("compile: "+cp.stderr[-1000:])
            source=stage
            require_idle(root)
            if previous.exists(): shutil.rmtree(previous)
            (previous/"app").mkdir(parents=True); copyset(root,previous)
        hashes={rel:digest(source/rel) for rel in PAYLOAD if (source/rel).exists()}; rep["hashes"]=hashes; save(rp,rep)
        if not a.rollback:
            require_idle(root)
        # Install while idle, preserving the original console and its watchdog.
        # The watchdog restarts only the Python children after they are stopped.
        copyset(source,root); runtime_changed=True
        started=time.time()
        stop(root,os.getpid())
        if not healthy(root,started): raise RuntimeError("fresh health timeout")
        rep["console_preserved"]=True
        save(rp,rep)
        cp=None
        for attempt in range(3):
            cp=subprocess.run(["powershell.exe","-NoProfile","-ExecutionPolicy","RemoteSigned","-File",str(root/"self-test.ps1"),"-PythonPath",sys.executable],capture_output=True,text=True,timeout=120)
            if cp.returncode==0: break
            time.sleep(2)
        if cp.returncode:
            rep["self_test_stdout"]=cp.stdout[-3000:]
            rep["self_test_stderr"]=cp.stderr[-3000:]
            save(rp,rep)
            raise RuntimeError("self-test failed after 3 attempts")
        if not a.rollback: save(data/"release_manifest.json",{"release_sha":a.release_sha,"installed_at":time.time(),"hashes":hashes})
        rep.update(ok=True,completed_at=time.time()); save(rp,rep)
    except Exception as e:
        rep["error"]=f"{type(e).__name__}: {e}"
        if runtime_changed and not a.rollback and previous.is_dir():
            try:
                copyset(previous,root); started=time.time(); stop(root,os.getpid())
                rep["rolled_back"]=True; rep["rollback_healthy"]=healthy(root,started)
            except Exception as re: rep["rollback_error"]=str(re)
        rep["completed_at"]=time.time(); save(rp,rep); raise
    finally:
        try:
            lock_file.seek(0); msvcrt.locking(lock_file.fileno(),msvcrt.LK_UNLCK,1); lock_file.close()
        except Exception: pass
        if stage.exists():
            try:shutil.rmtree(stage)
            except Exception:pass
if __name__=="__main__": main()
