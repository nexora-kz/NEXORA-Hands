import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ.setdefault("PYTHONUTF8","1")
os.environ.setdefault("PYTHONIOENCODING","utf-8")
for _stream in (sys.stdout,sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8",errors="replace")
    except (AttributeError,ValueError):
        pass

PS_UTF8_BOOTSTRAP=(
    "$utf8=[Text.UTF8Encoding]::new($false); "
    "try{[Console]::InputEncoding=$utf8}catch{}; "
    "try{[Console]::OutputEncoding=$utf8}catch{}; "
    "$OutputEncoding=$utf8; "
    "$PSDefaultParameterValues['*:Encoding']='utf8'; "
    "$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; "
    "try{chcp.com 65001 > $null}catch{}; "
)
def powershell_args(command):
    import base64
    wrapped=PS_UTF8_BOOTSTRAP+str(command)
    encoded=base64.b64encode(wrapped.encode("utf-16le")).decode("ascii")
    return ["powershell.exe","-NoProfile","-NonInteractive","-EncodedCommand",encoded]

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"; PROCESS_DIR=DATA/"processes"; PROCESS_DIR.mkdir(parents=True,exist_ok=True)
INBOX,OUTBOX=DATA/"inbox",DATA/"outbox"; STATE=DATA/"state.json"
for p in (INBOX,OUTBOX): p.mkdir(parents=True,exist_ok=True)
DEFAULT_MAX_PARALLEL_COMMANDS=5

def max_parallel_commands():
    value=os.environ.get("NEXORA_HANDS_MAX_PARALLEL")
    if value is None:
        cp=DATA/"hands_local_config.json"
        if cp.exists():
            try: value=json.loads(cp.read_text(encoding="utf-8")).get("max_parallel_commands")
            except Exception: value=None
    try: value=int(value or DEFAULT_MAX_PARALLEL_COMMANDS)
    except Exception: value=DEFAULT_MAX_PARALLEL_COMMANDS
    return max(1,min(16,value))

def state(status,task_id="",error="",active_tasks=None,max_parallel=None):
    tasks=list(active_tasks or [])
    payload={"name":"NEXORA Hands","status":status,"pid":os.getpid(),"task_id":task_id,"error":error,
             "active_tasks":tasks,"active_count":len(tasks),
             "max_parallel_commands":int(max_parallel or max_parallel_commands()),"updated_at":time.time()}
    tmp=STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    os.replace(tmp,STATE)
OP_RU={"shell":"PowerShell","read_file":"\u0427\u0442\u0435\u043d\u0438\u0435 \u0444\u0430\u0439\u043b\u0430","write_file":"\u0417\u0430\u043f\u0438\u0441\u044c \u0444\u0430\u0439\u043b\u0430","list_directory":"\u041f\u0440\u043e\u0441\u043c\u043e\u0442\u0440 \u043f\u0430\u043f\u043a\u0438","copy":"\u041a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435","move":"\u041f\u0435\u0440\u0435\u043c\u0435\u0449\u0435\u043d\u0438\u0435","delete":"\u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435","process_list":"\u0421\u043f\u0438\u0441\u043e\u043a \u043f\u0440\u043e\u0446\u0435\u0441\u0441\u043e\u0432","process_start":"\u0417\u0430\u043f\u0443\u0441\u043a \u043f\u0440\u043e\u0446\u0435\u0441\u0441\u0430","process_wait":"\u041e\u0436\u0438\u0434\u0430\u043d\u0438\u0435 \u043f\u0440\u043e\u0446\u0435\u0441\u0441\u0430","system_info":"\u0418\u043d\u0444\u043e\u0440\u043c\u0430\u0446\u0438\u044f \u043e \u043a\u043e\u043c\u043f\u044c\u044e\u0442\u0435\u0440\u0435","system_resources":"\u0420\u0435\u0441\u0443\u0440\u0441\u044b \u043a\u043e\u043c\u043f\u044c\u044e\u0442\u0435\u0440\u0430","start_search":"\u041f\u043e\u0438\u0441\u043a \u0444\u0430\u0439\u043b\u043e\u0432"}
def console(text):
    print(text, flush=True)
def safe_command_preview(value):
    import re
    text=str(value or "")
    patterns=[
        r'(?i)(password|passwd|pwd|token|secret|api[_-]?key|authorization)(\s*[:=]\s*)([^\s;]+)',
        r'(?i)(-password|-token|-secret|-api[_-]?key)(\s+)([^\s;]+)',
        r'(?i)(bearer\s+)([A-Za-z0-9._~+/-]+=*)',
    ]
    for pattern in patterns:
        text=re.sub(pattern,lambda m: m.group(1)+m.group(2)+"[REDACTED]",text)
    return text if len(text)<=500 else text[:500]+"... [TRUNCATED]"
def execute(c):
    op=str(c.get("operation") or c.get("type") or "").strip().lower()
    if op=="shell":
        cmd=str(c.get("command") or "")
        if not cmd: raise ValueError("shell command is empty")
        import base64
        wrapped="$ErrorActionPreference='Continue'; $ProgressPreference='SilentlyContinue'; & {"+cmd+"} 2>&1 | Out-String | ForEach-Object { [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($_)) }"
        encoded=base64.b64encode((PS_UTF8_BOOTSTRAP+wrapped).encode("utf-16le")).decode("ascii")
        r=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-EncodedCommand",encoded],capture_output=True,text=True,encoding="ascii",errors="replace",timeout=int(c.get("timeout_seconds") or 300))
        raw=(r.stdout or "").strip(); out=""
        if raw:
            try: out=base64.b64decode(raw).decode("utf-16le")
            except Exception: out=raw
        return {"returncode":r.returncode,"stdout":out,"stderr":r.stderr}
    if op=="read_file":
        p=Path(str(c["path"])).resolve(); return {"path":str(p),"content":p.read_text(encoding="utf-8",errors="replace")}
    if op=="write_file":
        p=Path(str(c["path"])).resolve(); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(str(c.get("content") or ""),encoding="utf-8"); return {"path":str(p),"bytes":p.stat().st_size}
    if op=="list_directory":
        p=Path(str(c.get("path") or ".")).resolve(); return {"path":str(p),"items":[{"name":x.name,"directory":x.is_dir(),"size":x.stat().st_size if x.is_file() else None} for x in p.iterdir()]}
    if op=="delete":
        p=Path(str(c["path"])).resolve()
        if p.is_dir():
            shutil.rmtree(p) if bool(c.get("recursive")) else p.rmdir()
        else: p.unlink()
        return {"path":str(p),"deleted":True}
    if op=="mkdir":
        p=Path(str(c["path"])).resolve(); p.mkdir(parents=True,exist_ok=True); return {"path":str(p),"created":True}
    if op=="copy":
        src=Path(str(c["source"])).resolve(); dst=Path(str(c["destination"])).resolve(); dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst); return {"source":str(src),"destination":str(dst)}
    if op=="exists":
        p=Path(str(c["path"])).resolve(); return {"path":str(p),"exists":p.exists(),"is_file":p.is_file(),"is_directory":p.is_dir()}
    if op=="stat":
        p=Path(str(c["path"])).resolve()
        if not p.exists(): raise FileNotFoundError(str(p))
        s=p.stat(); return {"path":str(p),"size":s.st_size,"created":s.st_ctime,"modified":s.st_mtime,"is_file":p.is_file(),"is_directory":p.is_dir()}
    if op=="read_file_chunk":
        p=Path(str(c["path"])).resolve(); offset=max(0,int(c.get("offset") or 0)); length=max(0,int(c.get("length") or 65536))
        with p.open("r",encoding="utf-8",errors="replace") as f:
            f.seek(offset); content=f.read(length)
        return {"path":str(p),"offset":offset,"length":len(content),"eof":len(content)<length,"content":content}
    if op=="move":
        src=Path(str(c["source"])).resolve(); dst=Path(str(c["destination"])).resolve(); dst.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(src),str(dst)); return {"source":str(src),"destination":str(dst)}
    if op=="search_files":
        root=Path(str(c.get("path") or ".")).resolve(); pattern=str(c.get("pattern") or "*"); recursive=bool(c.get("recursive",True)); limit=max(1,int(c.get("limit") or 100)); iterator=root.rglob(pattern) if recursive else root.glob(pattern); items=[]
        for x in iterator:
            items.append({"path":str(x),"directory":x.is_dir(),"size":x.stat().st_size if x.is_file() else None})
            if len(items)>=limit: break
        return {"path":str(root),"pattern":pattern,"recursive":recursive,"count":len(items),"items":items}
    if op=="read_multiple_files":
        paths=c.get("paths") or []
        if not isinstance(paths,list) or not paths: raise ValueError("read_multiple_files paths is empty")
        items=[]
        for raw in paths:
            p=Path(str(raw)).resolve()
            try: items.append({"path":str(p),"content":p.read_text(encoding="utf-8",errors="replace")})
            except Exception as e: items.append({"path":str(p),"error":f"{type(e).__name__}: {e}"})
        return {"count":len(items),"items":items}
    if op=="get_file_info":
        p=Path(str(c["path"])).resolve()
        if not p.exists(): raise FileNotFoundError(str(p))
        st=p.stat(); return {"path":str(p),"size":st.st_size,"created":st.st_ctime,"modified":st.st_mtime,"accessed":st.st_atime,"is_file":p.is_file(),"is_directory":p.is_dir(),"suffix":p.suffix,"name":p.name}
    if op=="read_process_output":
        pid=int(c["pid"]); offset=max(0,int(c.get("offset") or 0)); length=max(1,int(c.get("length") or 1000)); meta=None
        for mf in PROCESS_DIR.glob("*.json"):
            try:
                x=json.loads(mf.read_text(encoding="utf-8"))
                if int(x.get("pid"))==pid: meta=x; break
            except Exception: continue
        if not meta: raise FileNotFoundError(f"process metadata for pid {pid} not found")
        out=Path(meta["stdout"]).read_text(encoding="utf-8",errors="replace") if Path(meta["stdout"]).exists() else ""
        lines=out.splitlines(True); chunk=lines[offset:offset+length]
        return {"pid":pid,"offset":offset,"next_offset":offset+len(chunk),"total_lines":len(lines),"completed":meta.get("exitcode") is not None,"exitcode":meta.get("exitcode"),"stdout":"".join(chunk)}
    if op=="interact_with_process":
        pid=int(c["pid"]); data=str(c.get("input") or ""); meta=None
        for mf in PROCESS_DIR.glob("*.json"):
            try:
                x=json.loads(mf.read_text(encoding="utf-8"))
                if int(x.get("pid"))==pid: meta=x; break
            except Exception: continue
        if not meta or not meta.get("input"): raise FileNotFoundError(f"interactive process metadata for pid {pid} not found")
        Path(meta["input"]).open("a",encoding="utf-8").write(data); return {"pid":pid,"sent":len(data)}
    if op=="list_sessions":
        items=[]
        for mf in PROCESS_DIR.glob("*.json"):
            try:
                m=json.loads(mf.read_text(encoding="utf-8"))
                if m.get("session"): items.append({"session_id":mf.stem,"pid":m.get("pid"),"child_pid":m.get("child_pid"),"exitcode":m.get("exitcode"),"running":m.get("exitcode") is None})
            except Exception: continue
        return {"count":len(items),"items":items}
    if op=="kill_process": return execute({"operation":"process_stop","pid":c["pid"]})
    if op=="get_config":
        cp=ROOT/"data"/"hands_local_config.json"
        return {"path":str(cp),"values":json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else {}}
    if op=="set_config_value":
        cp=ROOT/"data"/"hands_local_config.json"; cfg=json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else {}
        key=str(c["key"]); cfg[key]=c.get("value"); cp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding="utf-8")
        return {"path":str(cp),"key":key,"value":cfg[key]}
    if op=="move_file": return execute({"operation":"move","source":c["source"],"destination":c["destination"]})
    if op=="start_search":
        root=Path(str(c.get("path") or ".")).resolve(); pattern=str(c.get("pattern") or ""); literal=bool(c.get("literalSearch",False)); ignore=bool(c.get("ignoreCase",True)); max_results=max(1,int(c.get("maxResults") or 100)); file_pattern=str(c.get("filePattern") or "*")
        sid=uuid.uuid4().hex; results=[]
        candidates=root.rglob(file_pattern) if bool(c.get("recursive",True)) else root.glob(file_pattern)
        for fp in candidates:
            if not fp.is_file(): continue
            try: text=fp.read_text(encoding="utf-8",errors="replace")
            except Exception: continue
            hay=text if not ignore else text.lower(); needle2=pattern if not ignore else pattern.lower()
            if (needle2 in hay) if literal else __import__('re').search(pattern,text,__import__('re').I if ignore else 0):
                results.append({"path":str(fp),"match":True})
                if len(results)>=max_results: break
        sp=PROCESS_DIR/f"search-{sid}.json"; sp.write_text(json.dumps({"session_id":sid,"results":results,"stopped":False},ensure_ascii=False),encoding="utf-8")
        return {"sessionId":sid,"count":len(results),"results":results,"complete":True}
    if op=="get_more_search_results":
        sid=str(c["sessionId"]); offset=max(0,int(c.get("offset") or 0)); length=max(1,int(c.get("length") or 100)); sp=PROCESS_DIR/f"search-{sid}.json"
        if not sp.exists(): raise FileNotFoundError(str(sp))
        m=json.loads(sp.read_text(encoding="utf-8")); rows=m.get("results",[]); chunk=rows[offset:offset+length]
        return {"sessionId":sid,"offset":offset,"nextOffset":offset+len(chunk),"count":len(rows),"results":chunk,"complete":offset+len(chunk)>=len(rows),"stopped":m.get("stopped",False)}
    if op=="stop_search":
        sid=str(c["sessionId"]); sp=PROCESS_DIR/f"search-{sid}.json"
        if not sp.exists(): raise FileNotFoundError(str(sp))
        m=json.loads(sp.read_text(encoding="utf-8")); m["stopped"]=True; sp.write_text(json.dumps(m,ensure_ascii=False),encoding="utf-8"); return {"sessionId":sid,"stopped":True}
    if op=="edit_block":
        fp=Path(str(c["path"])).resolve(); old=str(c["old_string"]); new=str(c.get("new_string") or ""); text=fp.read_text(encoding="utf-8",errors="replace"); count=text.count(old); expected=int(c.get("expected_replacements") or 1)
        if count!=expected: raise ValueError(f"expected {expected} replacements, found {count}")
        fp.write_text(text.replace(old,new,expected),encoding="utf-8"); return {"path":str(fp),"replacements":count}
    if op=="write_pdf":
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        fp=Path(str(c["path"])).resolve(); content=str(c.get("content") or ""); fp.parent.mkdir(parents=True,exist_ok=True); pdf=canvas.Canvas(str(fp),pagesize=A4); width,height=A4; y=height-50
        for line in content.splitlines() or [""]:
            if y<50: pdf.showPage(); y=height-50
            pdf.drawString(40,y,line[:110]); y-=14
        pdf.save(); return {"path":str(fp),"bytes":fp.stat().st_size}
    if op=="get_prompts":
        return {"prompts":[{"id":"onb2_01","title":"Organize my Downloads folder"},{"id":"onb2_02","title":"Explain a codebase or repository"},{"id":"onb2_03","title":"Create organized knowledge base"},{"id":"onb2_04","title":"Analyze a data file"},{"id":"onb2_05","title":"Check system health and resources"}]}
    if op=="process_list":
        r=subprocess.run(powershell_args("Get-Process | Select-Object Id,ProcessName,CPU,WorkingSet64 | ConvertTo-Json -Compress"),capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=30)
        rows=json.loads(r.stdout or "[]"); rows=rows if isinstance(rows,list) else [rows]
        return {"count":len(rows),"items":[{"pid":x.get("Id"),"name":x.get("ProcessName"),"cpu":x.get("CPU"),"memory":x.get("WorkingSet64")} for x in rows]}
    if op=="session_start":
        command=c.get("command");
        if not command: raise ValueError("session_start command is empty")
        args=command if isinstance(command,list) else (powershell_args(str(command)) if os.name=="nt" else ["sh","-lc",str(command)]); sid=str(c.get("session_id") or c.get("task_id") or uuid.uuid4())
        stdout_path=PROCESS_DIR/f"{sid}.stdout"; stderr_path=PROCESS_DIR/f"{sid}.stderr"; input_path=PROCESS_DIR/f"{sid}.input"; meta_path=PROCESS_DIR/f"{sid}.json"; runner_path=PROCESS_DIR/f"{sid}.runner.py"
        meta={"pid":"pending","command":args,"stdout":str(stdout_path),"stderr":str(stderr_path),"input":str(input_path),"started_at":time.time(),"session":True}
        meta_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8"); input_path.write_text("",encoding="utf-8")
        runner=f'''import json,subprocess,os,time
from pathlib import Path
mp=Path(r'{meta_path}'); m=json.loads(mp.read_text(encoding='utf-8')); out=open(r'{stdout_path}','w',encoding='utf-8',errors='replace'); err=open(r'{stderr_path}','w',encoding='utf-8',errors='replace'); p=subprocess.Popen(m['command'],stdin=subprocess.PIPE,stdout=out,stderr=err,text=True,bufsize=1); m['pid']=os.getpid(); m['child_pid']=p.pid; mp.write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8'); ip=Path(r'{input_path}'); pos=0
while p.poll() is None:
 d=ip.read_text(encoding='utf-8',errors='replace') if ip.exists() else ''
 if len(d)>pos: p.stdin.write(d[pos:]); p.stdin.flush(); pos=len(d)
 time.sleep(0.1)
m['exitcode']=p.returncode; mp.write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8'); out.close(); err.close()
'''
        runner_path.write_text(runner,encoding="utf-8"); p=subprocess.Popen([sys.executable,str(runner_path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return {"session_id":sid,"pid":p.pid,"command":args,"started":True}
    if op=="session_send":
        sid=str(c["session_id"]); p=PROCESS_DIR/f"{sid}.input"
        if not p.exists(): raise FileNotFoundError(str(p))
        data=str(c.get("input") or ""); p.open("a",encoding="utf-8").write(data); return {"session_id":sid,"sent":len(data)}
    if op=="session_read":
        sid=str(c["session_id"]); mp=PROCESS_DIR/f"{sid}.json"
        if not mp.exists(): raise FileNotFoundError(str(mp))
        m=json.loads(mp.read_text(encoding="utf-8")); out=Path(m["stdout"]).read_text(encoding="utf-8",errors="replace") if Path(m["stdout"]).exists() else ""; err=Path(m["stderr"]).read_text(encoding="utf-8",errors="replace") if Path(m["stderr"]).exists() else ""
        return {"session_id":sid,"pid":m.get("pid"),"child_pid":m.get("child_pid"),"running":m.get("exitcode") is None,"exitcode":m.get("exitcode"),"stdout":out,"stderr":err}
    if op=="session_stop":
        sid=str(c["session_id"]); mp=PROCESS_DIR/f"{sid}.json"
        if not mp.exists(): raise FileNotFoundError(str(mp))
        m=json.loads(mp.read_text(encoding="utf-8")); ids=[int(x) for x in (m.get("pid"),m.get("child_pid")) if str(x).isdigit()]
        for tid in ids: subprocess.run(powershell_args(f"Stop-Process -Id {tid} -Force -ErrorAction SilentlyContinue"),capture_output=True,timeout=15)
        mp.unlink(missing_ok=True)
        return {"session_id":sid,"stopped":True,"pids":ids}
    if op=="process_start":
        command=c.get("command");
        if not command: raise ValueError("process_start command is empty")
        args=command if isinstance(command,list) else powershell_args(str(command)); tid=str(c.get("task_id") or uuid.uuid4())
        stdout_path=PROCESS_DIR/f"{tid}.stdout"; stderr_path=PROCESS_DIR/f"{tid}.stderr"; input_path=PROCESS_DIR/f"{tid}.input"; meta_path=PROCESS_DIR/f"{tid}.json"; runner_path=PROCESS_DIR/f"{tid}.runner.py"
        runner=f'''import json,subprocess,os,time
from pathlib import Path
mp=Path(r'{meta_path}'); m=json.loads(mp.read_text(encoding='utf-8')); out=open(r'{stdout_path}','w',encoding='utf-8',errors='replace'); err=open(r'{stderr_path}','w',encoding='utf-8',errors='replace'); p=subprocess.Popen(m['command'],stdin=subprocess.PIPE,stdout=out,stderr=err,text=True,bufsize=1); m['pid']=os.getpid(); m['child_pid']=p.pid; mp.write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8'); ip=Path(r'{input_path}'); pos=0
while p.poll() is None:
 d=ip.read_text(encoding='utf-8',errors='replace') if ip.exists() else ''
 if len(d)>pos: p.stdin.write(d[pos:]); p.stdin.flush(); pos=len(d)
 time.sleep(0.1)
m['exitcode']=p.returncode; mp.write_text(json.dumps(m,ensure_ascii=False,indent=2),encoding='utf-8'); out.close(); err.close()
'''
        meta={"pid":"pending","command":args,"stdout":str(stdout_path),"stderr":str(stderr_path),"input":str(input_path),"started_at":time.time()}; input_path.write_text("",encoding="utf-8"); meta_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8"); runner_path.write_text(runner,encoding="utf-8"); p=subprocess.Popen([sys.executable,str(runner_path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); return {"pid":p.pid,"command":args,"started":True,"task_id":tid}
    if op=="process_wait":
        pid=int(c["pid"]); timeout=max(1,int(c.get("timeout_seconds") or 30)); deadline=time.time()+timeout; meta=None
        for mf in PROCESS_DIR.glob("*.json"):
            try:
                x=json.loads(mf.read_text(encoding="utf-8"));
                if int(x.get("pid"))==pid: meta=x; break
            except Exception: continue
        while time.time()<deadline:
            ps=subprocess.run(powershell_args(f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue; if($p){{'RUNNING'}}else{{'EXITED'}}"),capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=10)
            if ps.stdout.strip()!="RUNNING": break
            time.sleep(.25)
        running=ps.stdout.strip()=="RUNNING"; stdout=""; stderr=""
        if meta:
            stdout=Path(meta["stdout"]).read_text(encoding="utf-8",errors="replace") if Path(meta["stdout"]).exists() else ""; stderr=Path(meta["stderr"]).read_text(encoding="utf-8",errors="replace") if Path(meta["stderr"]).exists() else ""
        return {"pid":pid,"running":running,"completed":not running,"timeout":running,"exitcode":meta.get("exitcode") if meta else None,"stdout":stdout,"stderr":stderr}
    if op=="process_stop":
        pid=int(c["pid"]); target=None
        for mf in PROCESS_DIR.glob("*.json"):
            try:
                x=json.loads(mf.read_text(encoding="utf-8"));
                if int(x.get("pid"))==pid: target=x; break
            except Exception: continue
        ids=[pid]+([int(target["child_pid"])] if target and target.get("child_pid") else [])
        for tid in ids: subprocess.run(powershell_args(f"Stop-Process -Id {tid} -Force -ErrorAction SilentlyContinue"),capture_output=True,timeout=15)
        time.sleep(.2); remaining=[]
        for tid in ids:
            if subprocess.run(powershell_args(f"if(Get-Process -Id {tid} -ErrorAction SilentlyContinue){{exit 1}}else{{exit 0}}"),timeout=10).returncode!=0: remaining.append(tid)
        return {"pid":pid,"stopped":not remaining,"child_pid":target.get("child_pid") if target else None,"remaining":remaining}
    if op=="system_info":
        return {"hostname":socket.gethostname(),"platform":platform.platform(),"system":platform.system(),"release":platform.release(),"version":platform.version(),"machine":platform.machine(),"python":platform.python_version(),"processor":platform.processor(),"cpu_count":os.cpu_count()}
    if op=="system_resources":
        total,used,free=shutil.disk_usage(str(Path.home().anchor or Path.home())); return {"disk":{"total":total,"used":used,"free":free},"cwd":str(Path.cwd()),"home":str(Path.home()),"pid":os.getpid()}
    if op=="environment":
        names=c.get("names")
        if names is None: return {"count":len(os.environ),"names":sorted(os.environ.keys())}
        return {"values":{str(n):os.environ.get(str(n)) for n in names}}
    raise ValueError(f"unsupported operation: {op}")
def recover_interrupted_tasks():
    recovered=[]
    for running in sorted(INBOX.glob("*.running")):
        task=running.stem
        try:
            c=json.loads(running.read_text(encoding="utf-8-sig"))
            task=str(c.get("task_id") or task)
        except Exception:
            pass
        result_file=OUTBOX/f"{task}.json"
        if not result_file.exists():
            result={"task_id":task,"status":"error","error":"worker_restarted_during_task"}
            tmp=result_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            os.replace(tmp,result_file)
        running.unlink(missing_ok=True)
        recovered.append(task)
    return recovered

def process_running(running):
    task=running.stem
    try:
        c=json.loads(running.read_text(encoding="utf-8-sig"))
        task=str(c.get("task_id") or task)
        op=str(c.get("operation") or c.get("type") or "").strip().lower()
        title=OP_RU.get(op,op or "\u0417\u0430\u0434\u0430\u0447\u0430")
        console(f"\n[\u0417\u0410\u0414\u0410\u0427\u0410 {task}] {title}")
        if op=="shell":
            console(f"[\u041a\u041e\u041c\u0410\u041d\u0414\u0410 {task}] "+safe_command_preview(c.get("command")))
        console(f"[\u0412\u042b\u041f\u041e\u041b\u041d\u042f\u0415\u0422\u0421\u042f {task}]")
        try:
            result={"task_id":task,"status":"completed","payload":execute(c)}
            console(f"[\u0413\u041e\u0422\u041e\u0412\u041e {task}] \u0412\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u043e \u0443\u0441\u043f\u0435\u0448\u043d\u043e")
            payload=result.get("payload") or {}
            out=payload.get("stdout") if isinstance(payload,dict) else None
            if out and str(out).strip():
                console(f"[\u0420\u0415\u0417\u0423\u041b\u042c\u0422\u0410\u0422 {task}] "+str(out).strip())
        except Exception as e:
            result={"task_id":task,"status":"error","error":f"{type(e).__name__}: {e}"}
            console(f"[\u041e\u0428\u0418\u0411\u041a\u0410 {task}] "+str(e))
    except Exception as e:
        result={"task_id":task,"status":"error","error":f"{type(e).__name__}: {e}"}
        console(f"[\u041e\u0428\u0418\u0411\u041a\u0410 {task}] "+str(e))
    result_file=OUTBOX/f"{task}.json"
    tmp=result_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    os.replace(tmp,result_file)
    running.unlink(missing_ok=True)
    return task,result.get("status")

def main():
    limit=max_parallel_commands()
    interrupted=recover_interrupted_tasks()
    if interrupted:
        console("[RECOVERY] interrupted tasks finalized: "+", ".join(interrupted))
    state("running",max_parallel=limit)
    last_state=None
    with ThreadPoolExecutor(max_workers=limit,thread_name_prefix="nexora-hands") as pool:
        active={}
        while True:
            for future in list(active):
                if not future.done():
                    continue
                task=active.pop(future)
                try:
                    future.result()
                except Exception as e:
                    console(f"[\u041e\u0428\u0418\u0411\u041a\u0410 {task}] worker future: {type(e).__name__}: {e}")
            capacity=limit-len(active)
            if capacity>0:
                for path in sorted(INBOX.glob("*.json")):
                    if capacity<=0:
                        break
                    running=path.with_suffix(".running")
                    try:
                        os.replace(path,running)
                    except OSError:
                        continue
                    task=running.stem
                    try:
                        c=json.loads(running.read_text(encoding="utf-8-sig"))
                        task=str(c.get("task_id") or task)
                    except Exception:
                        pass
                    future=pool.submit(process_running,running)
                    active[future]=task
                    capacity-=1
            tasks=sorted(active.values())
            current=("executing" if tasks else "running",tuple(tasks))
            if current!=last_state:
                state(current[0],task_id=(tasks[0] if tasks else ""),active_tasks=tasks,max_parallel=limit)
                last_state=current
            time.sleep(.1)

if __name__=="__main__": main()
