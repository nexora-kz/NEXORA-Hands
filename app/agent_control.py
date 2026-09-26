import hashlib, json, os, platform, shutil, socket, subprocess, sys, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"

def _sha256(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def file_hash(path,algorithm="sha256"):
    p=Path(path)
    if not p.is_file(): raise FileNotFoundError(str(p))
    algo=str(algorithm).lower()
    h=hashlib.new(algo)
    with p.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return {"path":str(p),"algorithm":algo,"hash":h.hexdigest(),"bytes":p.stat().st_size}

def directory_manifest(path,recursive=True,max_files=10000):
    root=Path(path)
    items=[]
    it=root.rglob("*") if recursive else root.glob("*")
    for p in it:
        if p.is_file():
            items.append({"path":str(p.relative_to(root)),"bytes":p.stat().st_size,"sha256":_sha256(p)})
            if len(items)>=int(max_files): break
    return {"root":str(root),"count":len(items),"items":items,"truncated":len(items)>=int(max_files)}

def disk_info():
    rows=[]
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        p=Path(f"{letter}:\\")
        if p.exists():
            try:
                u=shutil.disk_usage(p); rows.append({"drive":str(p),"total":u.total,"used":u.used,"free":u.free})
            except OSError: pass
    return {"drives":rows}

def network_info():
    host=socket.gethostname()
    ips=[]
    try: ips=sorted(set(socket.gethostbyname_ex(host)[2]))
    except OSError: pass
    cp=subprocess.run(["netstat","-ano"],capture_output=True,text=True,encoding="utf-8",errors="replace")
    listeners=[]
    for line in cp.stdout.splitlines():
        parts=line.split()
        if len(parts)>=5 and parts[0].upper()=="TCP" and parts[3].upper()=="LISTENING":
            listeners.append({"protocol":"TCP","local":parts[1],"pid":parts[4]})
    return {"hostname":host,"addresses":ips,"tcp_listeners":listeners}

def process_tree():
    ps="Get-CimInstance Win32_Process|Select ProcessId,ParentProcessId,Name,CommandLine|ConvertTo-Json -Compress"
    cp=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",ps],capture_output=True,text=True,encoding="utf-8",errors="replace")
    if cp.returncode: raise RuntimeError(cp.stderr.strip())
    data=json.loads(cp.stdout or "[]")
    if isinstance(data,dict): data=[data]
    return {"count":len(data),"items":data}

def agent_status():
    manifest={}
    mp=DATA/"release_manifest.json"
    if mp.exists():
        try: manifest=json.loads(mp.read_text(encoding="utf-8-sig"))
        except Exception: pass
    active=(DATA/"active_slot.txt").read_text(encoding="utf-8-sig").strip() if (DATA/"active_slot.txt").exists() else "LEGACY"
    def js(p):
        try:return json.loads(Path(p).read_text(encoding="utf-8-sig"))
        except Exception:return {}
    ex=js(DATA/"state.json"); ch=js(DATA/"supabase_channel_state.json")
    return {"root":str(ROOT),"active_slot":active,"release":manifest,"executor_pid":ex.get("pid"),"executor_status":ex.get("status"),"transport_status":ch.get("status"),"heartbeat_ok":ch.get("heartbeat_ok"),"executor_online":ch.get("executor_online"),"queue_stalled":ch.get("queue_stalled"),"uptime_seconds":max(0,time.time()-float(ex.get("executor_started_at") or time.time()))}

def verify_agent():
    required=["start.ps1","stop.ps1","self-test.ps1","app/hands.py","app/supabase_channel.py","app/hands_supabase_config.json","app/agent_control.py"]
    files={}
    ok=True
    for rel in required:
        p=ROOT/rel
        present=p.is_file()
        item={"present":present}
        if present:item.update({"bytes":p.stat().st_size,"sha256":_sha256(p)})
        else:ok=False
        files[rel]=item
    cp=subprocess.run([sys.executable,"-m","py_compile",str(ROOT/"app/hands.py"),str(ROOT/"app/supabase_channel.py"),str(ROOT/"app/agent_control.py")],capture_output=True,text=True)
    if cp.returncode:ok=False
    st=agent_status()
    if not(st.get("heartbeat_ok") and st.get("executor_online") and not st.get("queue_stalled")):ok=False
    result={"ok":ok,"checked_at":time.time(),"compile_ok":cp.returncode==0,"compile_error":cp.stderr[-2000:],"files":files,"status":st}
    (DATA/"agent_verify.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result

def service_list():
    ps="Get-Service|Select Name,DisplayName,Status,StartType|ConvertTo-Json -Compress"
    cp=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",ps],capture_output=True,text=True,encoding="utf-8",errors="replace")
    if cp.returncode: raise RuntimeError(cp.stderr.strip())
    rows=json.loads(cp.stdout or "[]")
    return {"count":len(rows) if isinstance(rows,list) else 1,"items":rows if isinstance(rows,list) else [rows]}

def service_action(name,action):
    action=str(action).lower(); name=str(name)
    if action not in ("start","stop","restart"): raise ValueError("action must be start, stop or restart")
    q=name.replace("'","''")
    verb={"start":"Start-Service","stop":"Stop-Service","restart":"Restart-Service"}[action]
    cp=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",f"{verb} -Name '{q}' -ErrorAction Stop"],capture_output=True,text=True,encoding="utf-8",errors="replace")
    if cp.returncode: raise RuntimeError(cp.stderr.strip())
    return {"name":name,"action":action,"ok":True}

def start_agent_update(release_sha,mode="update"):
    sha=str(release_sha or "").strip().lower()
    if mode=="update" and (len(sha)!=40 or any(c not in "0123456789abcdef" for c in sha)): raise ValueError("release_sha must be a full 40-character Git SHA")
    updater=ROOT/"app/agent_updater.py"
    if not updater.is_file(): raise FileNotFoundError(str(updater))
    log=DATA/"agent_update_launch.json"
    args=[sys.executable,str(updater),"--root",str(ROOT)]
    if mode=="update": args+=["--release-sha",sha]
    else: args+=["--rollback"]
    flags=getattr(subprocess,"DETACHED_PROCESS",0)|getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)|getattr(subprocess,"CREATE_NO_WINDOW",0)
    p=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True,creationflags=flags)
    out={"accepted":True,"mode":mode,"release_sha":sha or None,"coordinator_pid":p.pid,"requested_at":time.time()}
    log.write_text(json.dumps(out,indent=2),encoding="utf-8")
    return out
