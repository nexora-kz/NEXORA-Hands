import json
import os
import base64
import difflib
import hashlib
import threading
import platform
import re
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
    return ["powershell.exe","-NoProfile","-NonInteractive","-OutputFormat","Text","-EncodedCommand",encoded]

def _kill_process_tree(pid):
    """Terminate one Windows process tree without touching unrelated processes."""
    try:
        subprocess.run(
            ["taskkill.exe", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass

def _run_powershell(command, timeout_seconds=300):
    """Run PowerShell and guarantee cleanup of its process tree on timeout."""
    timeout=max(1,min(600,int(timeout_seconds or 300)))
    p=subprocess.Popen(
        powershell_args(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    try:
        stdout,stderr=p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        stdout=e.output or ""
        stderr=e.stderr or ""
        _kill_process_tree(p.pid)
        try:
            tail_out,tail_err=p.communicate(timeout=5)
            stdout=(stdout or "")+(tail_out or "")
            stderr=(stderr or "")+(tail_err or "")
        except Exception:
            try: p.kill()
            except Exception: pass
        raise subprocess.TimeoutExpired(p.args,timeout,output=stdout,stderr=stderr)
    return subprocess.CompletedProcess(p.args,p.returncode,stdout,stderr)

def normalize_powershell_stream(text):
    text=str(text or "")
    if "#< CLIXML" not in text:
        return text
    try:
        import xml.etree.ElementTree as ET
        xml=text[text.index("<Objs"):]
        root=ET.fromstring(xml)
        parts=[]
        for el in root.iter():
            if el.tag.rsplit("}",1)[-1]=="S" and el.text:
                parts.append(re.sub(r"_x([0-9A-Fa-f]{4})_",lambda m: chr(int(m.group(1),16)),el.text))
        clean="".join(parts).strip()
        return clean or text
    except Exception:
        return text

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"; PROCESS_DIR=DATA/"processes"; PROCESS_DIR.mkdir(parents=True,exist_ok=True)
INBOX,OUTBOX=DATA/"inbox",DATA/"outbox"; STATE=DATA/"state.json"
RESULTS_DIR=DATA/"results"; RESULTS_DIR.mkdir(parents=True,exist_ok=True)
for p in (INBOX,OUTBOX): p.mkdir(parents=True,exist_ok=True)
DEFAULT_MAX_PARALLEL_COMMANDS=5
DEFAULT_INLINE_RESULT_BYTES=65536
STATE_WRITE_LOCK=threading.Lock()
EXECUTOR_STARTED_AT=time.time()
METRICS_LOCK=threading.Lock()
RUNTIME_METRICS={
    "executor_started_at":EXECUTOR_STARTED_AT,
    "last_started_at":None,
    "last_completed_at":None,
    "last_task_id":"",
    "last_operation":"",
    "completed_count":0,
    "failed_count":0,
}

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

def sanitize_transport_value(value):
    """Make JSON-compatible results safe for PostgreSQL/Supabase text/jsonb.

    PostgreSQL text cannot contain U+0000. Binary-ish command output can
    legitimately decode to NUL characters, so preserve them visibly as \x00
    instead of letting one result wedge the transport queue.
    """
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

def diagnostic_mode():
    return str(os.environ.get("NEXORA_HANDS_DIAGNOSTIC") or "").strip().lower() in ("1","true","yes","on")

SUPPORTED_OPERATIONS={
    "shell":"PowerShell command",
    "batch":"Execute up to 5 Hands operations concurrently",
    "read_file":"Read a UTF-8 text file",
    "write_file":"Write a UTF-8 text file",
    "write_binary":"Write/append Base64-decoded binary data without a shell process",
    "list_directory":"List directory entries",
    "delete":"Delete file or directory",
    "mkdir":"Create directory",
    "copy":"Copy file",
    "move":"Move file",
    "exists":"Check path existence",
    "stat":"Read file metadata",
    "read_file_chunk":"Read text file chunk",
    "search_files":"Search files by name pattern",
    "read_multiple_files":"Read multiple text files",
    "get_file_info":"Read detailed file metadata",
    "read_process_output":"Read managed process output",
    "interact_with_process":"Send input to managed process",
    "list_sessions":"List managed sessions",
    "kill_process":"Stop a managed process",
    "get_config":"Read local Hands config",
    "set_config_value":"Set local Hands config value",
    "move_file":"Move file alias",
    "start_search":"Search text across files",
    "get_more_search_results":"Page search results",
    "stop_search":"Stop search session",
    "edit_block":"Exact text replacement",
    "write_pdf":"Write simple PDF",
    "get_prompts":"List built-in prompts",
    "process_list":"List Windows processes",
    "session_start":"Start interactive session",
    "session_send":"Send session input",
    "session_read":"Read session output",
    "session_stop":"Stop session",
    "process_start":"Start background process",
    "process_wait":"Wait for background process",
    "process_stop":"Stop background process",
    "system_info":"System information",
    "system_resources":"System resources",
    "environment":"Environment variable names/selected values",
    "get_capabilities":"List Hands operations and runtime limits",
    "health":"Executor/transport health snapshot",
    "local_queue":"Local inbox/running/outbox queue snapshot",
    "cleanup_preview":"Preview old local Hands artifacts without deleting them",
    "agent_status":"Hands agent lifecycle/version/integrity status",
    "verify_agent":"Verify Hands runtime files, compile state and health",
    "file_hash":"Calculate a file cryptographic hash",
    "directory_manifest":"Create a SHA-256 directory manifest",
    "disk_info":"List local disks and free space",
    "network_info":"Network addresses and listening TCP ports",
    "process_tree":"Windows process tree with command lines",
}

def _metrics_snapshot():
    with METRICS_LOCK:
        return dict(RUNTIME_METRICS)

def _update_metrics(**kw):
    with METRICS_LOCK:
        RUNTIME_METRICS.update(kw)

def _json_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return {}

def health_snapshot():
    now=time.time()
    executor=_json_state(STATE)
    channel=_json_state(DATA/"supabase_channel_state.json")
    executor_age=max(0.0,now-float(executor.get("updated_at") or 0)) if executor.get("updated_at") else None
    heartbeat_age=max(0.0,now-float(channel.get("heartbeat_at") or 0)) if channel.get("heartbeat_at") else None
    executor_online=executor_age is not None and executor_age<=15.0
    transport_online=bool(channel.get("heartbeat_ok")) and heartbeat_age is not None and heartbeat_age<=45.0
    queue_stalled=bool(channel.get("queue_stalled"))
    return {
        "transport_online":transport_online,
        "executor_online":executor_online,
        "ready":bool(transport_online and executor_online and not queue_stalled),
        "queue_stalled":queue_stalled,
        "transport_status":channel.get("status"),
        "executor_status":executor.get("status"),
        "executor_pid":executor.get("pid"),
        "executor_active_count":executor.get("active_count",0),
        "executor_active_tasks":executor.get("active_tasks",[]),
        "max_parallel_commands":executor.get("max_parallel_commands",max_parallel_commands()),
        "last_claim_at":channel.get("last_claim_at"),
        "last_completed_at":channel.get("last_completed_at") or executor.get("last_completed_at"),
        "last_started_at":executor.get("last_started_at"),
        "executor_state_age_seconds":executor_age,
        "transport_heartbeat_age_seconds":heartbeat_age,
        "result_submit_errors":channel.get("result_submit_errors") or {},
    }

def _safe_artifact_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]+","_",str(value or "result"))[:120] or "result"

def _store_large_text(task_id,key,text):
    safe_task=_safe_artifact_name(task_id)
    safe_key=_safe_artifact_name(key)
    path=RESULTS_DIR/f"{safe_task}.{safe_key}.txt"
    path.write_text(text,encoding="utf-8",errors="replace")
    raw=path.read_bytes()
    return {
        "path":str(path),
        "bytes":len(raw),
        "sha256":hashlib.sha256(raw).hexdigest(),
    }

def compact_payload(task_id,payload,limit=DEFAULT_INLINE_RESULT_BYTES):
    if not isinstance(payload,dict):
        return payload
    out=dict(payload)
    large={}
    for key,value in list(out.items()):
        if not isinstance(value,str):
            continue
        raw=value.encode("utf-8","replace")
        if len(raw)<=limit:
            continue
        meta=_store_large_text(task_id,key,value)
        preview_chars=12000
        tail_chars=2000
        preview=value[:preview_chars]
        if len(value)>preview_chars+tail_chars:
            preview += "\\n... [FULL OUTPUT SAVED LOCALLY] ...\\n" + value[-tail_chars:]
        out[key]=preview
        large[key]=meta
    if large:
        out["large_outputs"]=large
        out["output_truncated"]=True
    return out

def state(status,task_id="",error="",active_tasks=None,max_parallel=None):
    tasks=list(active_tasks or [])
    metrics=_metrics_snapshot()
    payload=sanitize_transport_value({"name":"NEXORA Hands","status":status,"pid":os.getpid(),"task_id":task_id,"error":error,
             "active_tasks":tasks,"active_count":len(tasks),
             "max_parallel_commands":int(max_parallel or max_parallel_commands()),"updated_at":time.time(),**metrics})
    tmp=STATE.with_name(f"{STATE.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    data=json.dumps(payload,ensure_ascii=False,indent=2)
    with STATE_WRITE_LOCK:
        tmp.write_text(data,encoding="utf-8")
        last_error=None
        for attempt in range(10):
            try:
                os.replace(tmp,STATE)
                return
            except PermissionError as e:
                last_error=e
                time.sleep(0.05*(attempt+1))
        tmp.unlink(missing_ok=True)
        raise last_error
OP_RU={"shell":"Команда","batch":"Пакетное выполнение","read_file":"Чтение файла","write_file":"Запись файла","list_directory":"Просмотр папки","read_multiple_files":"Чтение файлов","search_files":"Поиск файлов","start_search":"Поиск","edit_block":"Изменение файла","copy":"Копирование","move":"Перемещение","move_file":"Перемещение","delete":"Удаление","mkdir":"Создание папки","exists":"Проверка пути","stat":"Сведения о файле","read_file_chunk":"Чтение фрагмента файла","get_file_info":"Сведения о файле","process_list":"Список процессов","process_start":"Запуск процесса","process_wait":"Ожидание процесса","session_start":"Запуск сессии","session_read":"Чтение сессии","system_info":"Информация о компьютере","system_resources":"Ресурсы компьютера","health":"Проверка состояния","get_capabilities":"Возможности","local_queue":"Очередь","cleanup_preview":"Предпросмотр очистки"}
def console(text):
    print(sanitize_transport_value(str(text)), flush=True)
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
def _human_age(value):
    try:
        seconds=max(0.0,float(value))
    except (TypeError,ValueError):
        return "Нет данных"
    if seconds<2:
        return "Только что"
    if seconds<60:
        return f"{int(round(seconds))} сек. назад"
    if seconds<3600:
        return f"{int(seconds//60)} мин. назад"
    return f"{int(seconds//3600)} ч. назад"

def _brief_operation(c):
    if not isinstance(c,dict):
        return "Некорректная задача"
    op=str(c.get("operation") or c.get("type") or "").strip().lower()
    title=OP_RU.get(op,op or "Задача")
    if op=="shell":
        return f"{title}: {safe_command_preview(c.get('command'))}"
    if op=="read_multiple_files":
        return f"{title}: {len(c.get('paths') or [])} файл(ов)"
    if op in ("search_files","start_search"):
        needle=c.get("pattern") or c.get("query") or "*"
        path=c.get("path")
        return f"{title}: {needle}" + (f" | {path}" if path else "")
    if op in ("copy","move","move_file"):
        return f"{title}: {c.get('source')} -> {c.get('destination')}"
    if op in ("process_start","session_start"):
        return f"{title}: {safe_command_preview(c.get('command'))}"
    if c.get("path"):
        return f"{title}: {c.get('path')}"
    if c.get("pid") is not None:
        return f"{title}: PID {c.get('pid')}"
    return title

def _batch_payload_summary(payload,op):
    if not isinstance(payload,dict):
        return "готово"
    if op=="shell":
        out=str(payload.get("stdout") or "").strip().replace("\n"," | ")
        if out:
            return out[:240]
        return f"код завершения {payload.get('returncode',0)}"
    if op=="health":
        return "система готова" if payload.get("ready") else "система требует проверки"
    if op=="local_queue":
        counts=payload.get("counts") or {}
        return f"в очереди {counts.get('queued_local',0)}, выполняется {counts.get('running_local',0)}, ожидает отправки {counts.get('pending_results',0)}"
    if op in ("search_files","start_search"):
        return f"найдено {payload.get('count',0)}"
    if op=="read_multiple_files":
        items=payload.get("items") or []
        errors=sum(1 for x in items if isinstance(x,dict) and x.get("error"))
        return f"прочитано {payload.get('count',len(items))}, ошибок {errors}"
    if op=="list_directory":
        return f"объектов {len(payload.get('items') or [])}"
    if op=="write_file":
        return f"записано {_human_bytes(payload.get('bytes',0))}"
    if op in ("read_file","read_file_chunk"):
        content=str(payload.get("content") or "")
        return f"прочитано {_human_bytes(len(content.encode('utf-8','replace')))}"
    if op=="edit_block":
        return f"замен {payload.get('replacements',0)}"
    if op in ("copy","move","move_file"):
        return str(payload.get("destination") or payload.get("path") or "готово")
    if op in ("process_start","session_start"):
        return f"PID {payload.get('pid')} запущен"
    if op=="process_list":
        return f"процессов {payload.get('count',0)}"
    if op=="get_capabilities":
        return f"операций {len(payload.get('operation_names') or [])}, параллельно {payload.get('max_parallel_commands','?')}"
    if payload.get("path"):
        return str(payload.get("path"))
    return "готово"

def console_task_start(c,op,title):
    if op=="batch":
        commands=c.get("commands") or []
        console(f"\n[{title}] Задач: {len(commands)}")
        for i,item in enumerate(commands,1):
            console(f"  [{i}/{len(commands)}] {_brief_operation(item)}")
        return
    if op=="shell":
        console(f"\n[Команда] {safe_command_preview(c.get('command'))}")
        return
    if op=="read_multiple_files":
        paths=list(c.get("paths") or [])
        console(f"\n[{title}] {len(paths)} файл(ов)")
        for p in paths[:4]:
            console(f"  {p}")
        if len(paths)>4:
            console(f"  ... ещё {len(paths)-4}")
        return
    if op in ("search_files","start_search"):
        needle=c.get("pattern") or c.get("query") or "*"
        console(f"\n[{title}] {needle}")
        if c.get("path"):
            console(f"  Путь: {c.get('path')}")
        return
    if op in ("copy","move","move_file"):
        console(f"\n[{title}] {c.get('source')} -> {c.get('destination')}")
        return
    if op in ("process_start","session_start"):
        console(f"\n[{title}] {safe_command_preview(c.get('command'))}")
        return
    if c.get("path"):
        console(f"\n[{title}] {c.get('path')}")
        return
    if c.get("pid") is not None:
        console(f"\n[{title}] PID {c.get('pid')}")
        return
    console(f"\n[{title}]")

def _human_bytes(value):
    try:
        n=float(value)
    except Exception:
        return str(value)
    units=("B","KB","MB","GB","TB")
    i=0
    while n>=1024 and i<len(units)-1:
        n/=1024.0
        i+=1
    return f"{n:.1f} {units[i]}" if i else f"{int(n)} B"

def console_task_result(payload,op):
    if not isinstance(payload,dict):
        return
    if op=="shell":
        out=str(payload.get("stdout") or "").strip()
        if out:
            console("[Результат] "+out[:4000])
    elif op=="health":
        console(f"[Состояние] {'ГОТОВ' if payload.get('ready') else 'ТРЕБУЕТ ВНИМАНИЯ'}")
        console(f"[Связь] {'Подключена' if payload.get('transport_online') else 'Нет связи'}")
        console(f"[Исполнитель] {'Работает' if payload.get('executor_online') else 'Недоступен'}")
        console(f"[Активные задачи] {payload.get('executor_active_count',0)} из {payload.get('max_parallel_commands','?')}")
        console(f"[Очередь] {'Требует проверки' if payload.get('queue_stalled') else 'В норме'}")
        console(f"[Последний сигнал] {_human_age(payload.get('transport_heartbeat_age_seconds'))}")
    elif op=="local_queue":
        counts=payload.get("counts") or {}
        console(f"[Результат] queued={counts.get('queued_local',0)} | running={counts.get('running_local',0)} | pending={counts.get('pending_results',0)}")
    elif op in ("search_files","start_search"):
        console(f"[Результат] Найдено: {payload.get('count',0)}")
    elif op=="read_multiple_files":
        items=payload.get("items") or []
        errors=sum(1 for x in items if isinstance(x,dict) and x.get("error"))
        console(f"[Результат] Прочитано: {payload.get('count',len(items))} | ошибок: {errors}")
    elif op=="list_directory":
        console(f"[Результат] Объектов: {len(payload.get('items') or [])}")
    elif op=="write_file":
        console(f"[Результат] Записано: {_human_bytes(payload.get('bytes',0))}")
    elif op in ("read_file","read_file_chunk"):
        content=str(payload.get("content") or "")
        console(f"[Результат] Прочитано: {_human_bytes(len(content.encode('utf-8','replace')))}")
    elif op=="edit_block":
        console(f"[Результат] Замен: {payload.get('replacements',0)}")
    elif op in ("copy","move","move_file"):
        console(f"[Результат] {payload.get('destination') or payload.get('path') or 'Готово'}")
    elif op in ("process_start","session_start"):
        console(f"[Результат] PID {payload.get('pid')} | запущено")
    elif op=="process_list":
        console(f"[Результат] Процессов: {payload.get('count',0)}")
    elif op=="get_capabilities":
        console(f"[Результат] Операций: {len(payload.get('operation_names') or [])} | параллельно: {payload.get('max_parallel_commands','?')}")
    elif op=="batch":
        items=payload.get("items") or []
        total=int(payload.get("count",len(items)) or 0)
        failed=int(payload.get("failed_count",0) or 0)
        console(f"[Результат] Задач: {total} | выполнено: {max(0,total-failed)} | ошибок: {failed}")
        for row in items:
            if not isinstance(row,dict):
                continue
            idx=int(row.get("index",0))+1
            status=row.get("status")
            child_op=str(row.get("operation") or "").strip().lower()
            child_summary=str(row.get("summary") or OP_RU.get(child_op,child_op or "Задача"))
            mark="✓" if status=="completed" else "✗"
            if status=="completed":
                summary=_batch_payload_summary(row.get("payload"),child_op)
            else:
                summary=str(row.get("error") or "ошибка")
            console(f"  [{idx}/{total}] {mark} {child_summary} — {summary[:500]}")

    for key,meta in (payload.get("large_outputs") or {}).items():
        console(f"[Полный результат:{key}] {meta.get('path')} | {_human_bytes(meta.get('bytes',0))} | SHA-256 {meta.get('sha256')}")

def execute(c):
    op=str(c.get("operation") or c.get("type") or "").strip().lower()
    if op=="get_capabilities":
        return {
            "operations":SUPPORTED_OPERATIONS,
            "operation_names":sorted(SUPPORTED_OPERATIONS),
            "max_parallel_commands":max_parallel_commands(),
            "inline_result_bytes":DEFAULT_INLINE_RESULT_BYTES,
            "diagnostic_console":diagnostic_mode(),
        }
    if op=="agent_status":
        from agent_control import agent_status
        return agent_status()
    if op=="verify_agent":
        from agent_control import verify_agent
        return verify_agent()
    if op=="file_hash":
        from agent_control import file_hash
        return file_hash(c["path"],c.get("algorithm") or "sha256")
    if op=="directory_manifest":
        from agent_control import directory_manifest
        return directory_manifest(c["path"],bool(c.get("recursive",True)),int(c.get("max_files") or 10000))
    if op=="disk_info":
        from agent_control import disk_info
        return disk_info()
    if op=="network_info":
        from agent_control import network_info
        return network_info()
    if op=="process_tree":
        from agent_control import process_tree
        return process_tree()
    if op=="service_list":
        from agent_control import service_list
        return service_list()
    if op=="service_action":
        from agent_control import service_action
        return service_action(c["name"],c["action"])
    if op=="cleanup_preview":
        hours=max(1,min(8760,int(c.get("older_than_hours") or 168)))
        cutoff=time.time()-hours*3600
        executor=_json_state(STATE)
        active=set(str(x) for x in (executor.get("active_tasks") or []) if x)
        candidates=[]
        def add_candidate(path,kind):
            try: stat=path.stat()
            except OSError: return
            if stat.st_mtime>=cutoff: return
            name=path.name
            if any(task and task in name for task in active): return
            candidates.append({"kind":kind,"path":str(path),"bytes":stat.st_size,"modified":stat.st_mtime})
        for path in OUTBOX.glob("*.json"): add_candidate(path,"outbox")
        for path in RESULTS_DIR.iterdir():
            if path.is_file(): add_candidate(path,"result")
        for path in PROCESS_DIR.iterdir():
            if not path.is_file(): continue
            if path.suffix.lower()==".json":
                try:
                    meta=json.loads(path.read_text(encoding="utf-8",errors="replace"))
                    if meta.get("exitcode") is None and meta.get("pid") not in (None,"","pending"): continue
                except Exception: pass
            add_candidate(path,"process")
        candidates.sort(key=lambda x:(x["modified"],x["path"]))
        return {"preview_only":True,"older_than_hours":hours,"count":len(candidates),"bytes":sum(int(x["bytes"]) for x in candidates),"items":candidates[:500],"truncated":len(candidates)>500}
    if op=="health":
        return health_snapshot()
    if op=="local_queue":
        self_task=str(c.get("task_id") or "")
        inbox=[p.stem for p in sorted(INBOX.glob("*.json")) if p.stem!=self_task]
        running=[p.stem for p in sorted(INBOX.glob("*.running")) if p.stem!=self_task]
        outbox=[p.stem for p in sorted(OUTBOX.glob("*.json"))]
        channel=_json_state(DATA/"supabase_channel_state.json")
        active=set(str(x) for x in (channel.get("active_tasks") or []) if str(x)!=self_task)
        pending=[x for x in outbox if x in active]
        return {
            "queued_local":inbox,
            "running_local":running,
            "pending_results":pending,
            "retained_result_files":len(outbox),
            "channel_active_tasks":sorted(active),
            "counts":{"queued_local":len(inbox),"running_local":len(running),"pending_results":len(pending)}
        }
    if op=="batch":
        commands=c.get("commands") or []
        if not isinstance(commands,list) or not commands:
            raise ValueError("batch commands is empty")
        if len(commands)>5:
            raise ValueError("batch supports at most 5 commands")
        for item in commands:
            if not isinstance(item,dict) or str(item.get("operation") or "").strip().lower()=="batch":
                raise ValueError("batch items must be operation objects and cannot contain nested batch")
        total=len(commands)
        def run_item(pair):
            idx,item=pair
            child_op=str(item.get("operation") or item.get("type") or "").strip().lower()
            child_summary=_brief_operation(item)
            console(f"[Пакет {idx+1}/{total}] Выполняется: {child_summary}")
            try:
                payload=execute(item)
                failed=bool(isinstance(payload,dict) and payload.get("failed"))
                if failed:
                    console(f"[Пакет {idx+1}/{total}] Ошибка: {child_summary}")
                else:
                    console(f"[Пакет {idx+1}/{total}] Готово: {child_summary} — {_batch_payload_summary(payload,child_op)[:500]}")
                return {"index":idx,"status":"error" if failed else "completed","operation":child_op,"summary":child_summary,"payload":payload}
            except Exception as e:
                console(f"[Пакет {idx+1}/{total}] Ошибка: {child_summary} — {type(e).__name__}: {e}")
                return {"index":idx,"status":"error","operation":child_op,"summary":child_summary,"error":f"{type(e).__name__}: {e}"}
        with ThreadPoolExecutor(max_workers=min(5,len(commands)),thread_name_prefix="nexora-batch") as pool:
            items=list(pool.map(run_item,enumerate(commands)))
        failed_count=sum(1 for x in items if x.get("status")!="completed")
        return {"count":len(items),"failed_count":failed_count,"failed":failed_count>0,"items":items}
    if op=="shell":
        cmd=str(c.get("command") or "")
        if not cmd: raise ValueError("shell command is empty")
        wrapped="$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "+cmd
        r=_run_powershell(wrapped,c.get("timeout_seconds") or 300)
        return {"returncode":int(r.returncode),"stdout":normalize_powershell_stream(r.stdout or ""),"stderr":normalize_powershell_stream(r.stderr or ""),"failed":int(r.returncode)!=0}
    if op=="read_file":
        p=Path(str(c["path"])).resolve(); return {"path":str(p),"content":p.read_text(encoding="utf-8",errors="replace")}
    if op=="write_file":
        p=Path(str(c["path"])).resolve(); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(str(c.get("content") or ""),encoding="utf-8"); return {"path":str(p),"bytes":p.stat().st_size}
    if op=="write_binary":
        p=Path(str(c["path"])).resolve(); raw=base64.b64decode(str(c.get("data_base64") or ""),validate=True); p.parent.mkdir(parents=True,exist_ok=True); mode="ab" if bool(c.get("append")) else "wb"
        with p.open(mode) as f: f.write(raw)
        data=p.read_bytes(); digest=hashlib.sha256(data).hexdigest(); expected=str(c.get("expected_sha256") or "").strip().lower()
        if expected and digest!=expected: raise ValueError(f"SHA-256 mismatch: expected {expected}, got {digest}")
        return {"path":str(p),"chunk_bytes":len(raw),"bytes":len(data),"sha256":digest,"append":mode=="ab"}
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
    if op and op.endswith("_check") and op[:-6] in SUPPORTED_OPERATIONS:
        matches=[op[:-6]]
    else:
        matches=difflib.get_close_matches(op,sorted(SUPPORTED_OPERATIONS),n=3,cutoff=0.45) if op else []
    hint=(" Did you mean: "+", ".join(matches)+"?") if matches else ""
    raise ValueError(f"unsupported operation: {op or '<empty>'}.{hint} Use get_capabilities for the full list.")
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
            result=sanitize_transport_value({"task_id":task,"status":"error","error":"worker_restarted_during_task"})
            tmp=result_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
            os.replace(tmp,result_file)
        running.unlink(missing_ok=True)
        recovered.append(task)
    return recovered

def process_running(running):
    task=running.stem
    started=time.time()
    op=""
    title="Задача"
    try:
        c=json.loads(running.read_text(encoding="utf-8-sig"))
        task=str(c.get("task_id") or task)
        op=str(c.get("operation") or c.get("type") or "").strip().lower()
        title=OP_RU.get(op,op or "Задача")
        _update_metrics(last_started_at=started,last_task_id=task,last_operation=op)
        if diagnostic_mode():
            console(f"\n[ЗАДАЧА {task}] {title}")
            if op=="shell":
                console(f"[КОМАНДА {task}] "+safe_command_preview(c.get("command")))
            console(f"[ВЫПОЛНЯЕТСЯ {task}]")
        else:
            console_task_start(c,op,title)
        try:
            payload=compact_payload(task,execute(c))
            completed=time.time()
            failed=bool(isinstance(payload,dict) and payload.get("failed"))
            if failed:
                rc=int(payload.get("returncode") or 1)
                result={"task_id":task,"status":"error","stage":"failed","payload":payload,
                        "error":f"Command exited with code {rc}",
                        "timing":{"started_at":started,"completed_at":completed,
                                  "duration_ms":int((completed-started)*1000)}}
                metrics=_metrics_snapshot()
                _update_metrics(last_completed_at=completed,failed_count=int(metrics.get("failed_count") or 0)+1)
                err=str(payload.get("stderr") or "").strip()
                out=str(payload.get("stdout") or "").strip()
                detail=err or out or f"Код возврата: {rc}"
                console((f"[ОШИБКА {task}] " if diagnostic_mode() else "[ОШИБКА] ")+detail[:4000])
            else:
                result={"task_id":task,"status":"completed","stage":"completed","payload":payload,
                        "timing":{"started_at":started,"completed_at":completed,
                                  "duration_ms":int((completed-started)*1000)}}
                metrics=_metrics_snapshot()
                _update_metrics(last_completed_at=completed,completed_count=int(metrics.get("completed_count") or 0)+1)
                out=payload.get("stdout") if isinstance(payload,dict) else None
                if diagnostic_mode():
                    console(f"[ГОТОВО {task}] Выполнено успешно")
                    if out and str(out).strip():
                        console(f"[РЕЗУЛЬТАТ {task}] "+str(out).strip()[:4000])
                else:
                    console_task_result(payload,op)
                    if op=="health":
                        if payload.get("ready"):
                            console("[ГОТОВО] Система работает нормально")
                        else:
                            console("[ВНИМАНИЕ] Система требует проверки")
                    else:
                        console("[ГОТОВО] Выполнено успешно")
        except Exception as e:
            completed=time.time()
            result={"task_id":task,"status":"error","stage":"failed","error":f"{type(e).__name__}: {e}",
                    "timing":{"started_at":started,"completed_at":completed,
                              "duration_ms":int((completed-started)*1000)}}
            metrics=_metrics_snapshot()
            _update_metrics(last_completed_at=completed,failed_count=int(metrics.get("failed_count") or 0)+1)
            console((f"[ОШИБКА {task}] " if diagnostic_mode() else "[ОШИБКА] ")+str(e))
    except Exception as e:
        completed=time.time()
        result={"task_id":task,"status":"error","stage":"failed","error":f"{type(e).__name__}: {e}",
                "timing":{"started_at":started,"completed_at":completed,
                          "duration_ms":int((completed-started)*1000)}}
        metrics=_metrics_snapshot()
        _update_metrics(last_completed_at=completed,failed_count=int(metrics.get("failed_count") or 0)+1)
        console((f"[ОШИБКА {task}] " if diagnostic_mode() else "[ОШИБКА] ")+str(e))
    result=sanitize_transport_value(result)
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
        last_heartbeat_write=0.0
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
            now=time.time()
            if current!=last_state or now-last_heartbeat_write>=2.0:
                state(current[0],task_id=(tasks[0] if tasks else ""),active_tasks=tasks,max_parallel=limit)
                last_state=current
                last_heartbeat_write=now
            time.sleep(.1)

if __name__=="__main__": main()
