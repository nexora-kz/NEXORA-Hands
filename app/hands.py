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

# Capture the loaded executable fingerprint once, not the mutable file on disk.
LOADED_HANDS_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

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
VISUAL_SEQUENCE_LOCK=threading.Lock()
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
    "window_list":"List visible top-level application windows (read only)",
    "screen_capture":"Capture current desktop to a bounded local PNG (read only)",
    "cursor_status":"Read cursor position and foreground window (read only)",
    "visual_sequence":"Run a short guarded sequence of desktop input with foreground checks",
    "visual_stop":"Request cancellation of a visual sequence by its run_id",
    "visual_status":"Read persisted progress of a visual sequence",
    "mouse_action":"Guarded mouse move/click/scroll on an explicitly targeted foreground window",
    "keyboard_action":"Guarded text or allowlisted key input on an explicitly targeted foreground window",
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
    "read_file_lines":"Read text file by bounded line range",
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
    "list_searches":"List persisted search sessions",
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
    "job_start":"Start a durable background job",
    "job_status":"Read durable background job status",
    "job_result":"Read durable background job output",
    "job_cancel":"Cancel a durable background job",
    "system_info":"System information",
    "system_resources":"System resources",
    "environment":"Environment variable names/selected values",
    "get_capabilities":"List Hands operations and runtime limits",
    "get_recent_tool_calls":"Read bounded, metadata-only local task history",
    "diagnose_all":"Read-only consolidated transport, executor, queue and updater diagnostics",
    "task_monitor":"Read-only summary of running and queued local tasks",
    "update_status":"Read-only installed agent release and last updater outcome",
    "usage_stats":"Aggregated metadata-only task success, failure and duration statistics",
    "auto_recovery":"Safe recovery assessment; never restarts or deletes automatically",
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
    "service_list":"List Windows services",
    "service_action":"Start, stop or restart a named Windows service",
    "update_agent":"Install a verified immutable Hands release using detached native updater",
    "rollback_agent":"Restore the previous verified Hands runtime using detached native updater",
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
    # Enforce a final bound on the entire nested JSON payload, not just
    # top-level string fields. The full payload stays on this PC.
    encoded=json.dumps(out,ensure_ascii=False,default=str)
    if len(encoded.encode("utf-8"))>49152:
        meta=_store_large_text(task_id,"full_payload",encoded)
        return {
            "output_truncated":True,
            "large_outputs":{"full_payload":meta},
            "preview":encoded[:2048],
            "reason":"nested_or_aggregate_payload_exceeded_48kib",
        }
    return out

def state(status,task_id="",error="",active_tasks=None,max_parallel=None):
    tasks=list(active_tasks or [])
    metrics=_metrics_snapshot()
    payload=sanitize_transport_value({"name":"NEXORA Hands","status":status,"pid":os.getpid(),"task_id":task_id,"error":error,
             "active_tasks":tasks,"active_count":len(tasks),
             "max_parallel_commands":int(max_parallel or max_parallel_commands()),"loaded_hands_sha256":LOADED_HANDS_SHA256,"operation_count":len(SUPPORTED_OPERATIONS),"updated_at":time.time(),**metrics})
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
def mask_encoded_payloads(value):
    """Mask only long encoded arguments in logs and metadata; preserve normal text."""
    text=str(value or "")
    text=re.sub(r"(?i)(-EncodedCommand\s+)[A-Za-z0-9+/=]{32,}",r"\1[СКРЫТО: BASE64]",text)
    text=re.sub(r"""(?i)(['"]-EncodedCommand['"]\s*,\s*['"])[A-Za-z0-9+/=]{32,}(['"])""",r"\1[СКРЫТО: BASE64]\2",text)
    text=re.sub(r"""(?i)(\b(?:base64\.)?b64decode\s*\(\s*['"])[A-Za-z0-9+/=]{64,}(['"])""",r"\1[СКРЫТО: BASE64]\2",text)
    return text

def console(text):
    from datetime import datetime
    message=sanitize_transport_value(str(text))
    import re
    message=mask_encoded_payloads(message)
    # Do not infer a timeout from arbitrary command output: source code and logs
    # may legitimately contain the words 'timed out after'.
    stamp=datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    for line in message.splitlines() or ['']:
        if not line.strip():
            print('',flush=True)
            continue
        label=line.lstrip().upper()
        if label.startswith('[ТАЙМ-АУТ]'): icon='⌛️'
        elif label.startswith(('[ОШИБКА','[ERROR')): icon='❌'
        elif label.startswith('[ОЖИДАНИЕ'): icon='⏳'
        elif label.startswith('[ВРЕМЯ'): icon='⏱️'
        elif label.startswith('[РЕЗУЛЬТАТ'): icon='📋'
        elif label.startswith(('[ВНИМАНИЕ','[WARNING')): icon='⚠️'
        elif label.startswith(('[ГОТОВО','[RECOVERY]')): icon='✅'
        elif label.startswith(('[ВЫПОЛНЯЕТСЯ','[ЗАДАЧА','[КОМАНДА','[ПАКЕТ')): icon='⚙️'
        elif any(x in label for x in ('[СОСТОЯНИЕ','[СВЯЗЬ','[ИСПОЛНИТЕЛЬ','[ОЧЕРЕДЬ','[АКТИВНЫЕ ЗАДАЧИ')): icon='🛠️'
        else: icon='🔧'
        line=re.sub(r'^(?:⏳|⌛️|⏱️|⚙️|📋|⚠️|❌|✅|🔧|🛠️)\s*','',line)
        line=re.sub(r'(?<=\])\s*(?:⏳|⌛️|⏱️|⚙️|📋|⚠️|❌|✅|🔧|🛠️)\s*',' ',line)
        if not diagnostic_mode(): line=re.sub(r';\s*задача\s+hands-mcp-[0-9a-f-]+','',line,flags=re.I)
        print(f'[{stamp}] {icon} {line}',flush=True)
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
    text=mask_encoded_payloads(text)
    text=' '.join(text.split())
    return text
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
            console("[Результат] "+out)
    elif op=="health":
        ready='ГОТОВ' if payload.get('ready') else 'ТРЕБУЕТ ВНИМАНИЯ'
        link='подключена' if payload.get('transport_online') else 'нет связи'
        executor='работает' if payload.get('executor_online') else 'недоступен'
        active=payload.get('executor_active_count',0)
        capacity=payload.get('max_parallel_commands','?')
        queue='требует проверки' if payload.get('queue_stalled') else 'в норме'
        console(f'[Состояние] Система: {ready} | Связь: {link}')
        console(f'[Результат] Исполнитель: {executor} | Задачи: {active}/{capacity}')
        console(f'[Очередь] {queue}')
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

def _record_task_history(task,op,result,started):
    # Deliberately exclude command arguments, paths, stdout, stderr and tokens.
    entry={"task_id":str(task)[:128],"operation":str(op)[:80],
           "status":str(result.get("status") or "unknown")[:24],
           "started_at":started,"duration_ms":int((time.time()-started)*1000)}
    history=DATA/"tool-history.jsonl"
    try:
        with STATE_WRITE_LOCK:
            if history.exists() and history.stat().st_size>1024*1024:
                # Keep the newest records; never grow history without a bound.
                with history.open("rb") as f:
                    f.seek(max(0,history.stat().st_size-512*1024))
                    tail=f.read().split(b"\n")[1:]
                tmp=history.with_suffix(".tmp")
                tmp.write_bytes(b"\n".join(tail).strip(b"\n")+b"\n")
                os.replace(tmp,history)
            with history.open("a",encoding="utf-8") as f:
                f.write(json.dumps(entry,ensure_ascii=False)+"\n")
    except OSError:
        pass

def execute(c):
    op=str(c.get("operation") or c.get("type") or "").strip().lower()
    if op=="get_capabilities":
        return {
            "operations":SUPPORTED_OPERATIONS,
            "operation_names":sorted(SUPPORTED_OPERATIONS),
            "operation_contracts":{"batch":{"commands":{"type":"array","min_items":1,"max_items":5,"items":"Hands operation object"},"aliases":{"operations":"commands"},"nested_batch":False}},
            "max_parallel_commands":max_parallel_commands(),
            "inline_result_bytes":DEFAULT_INLINE_RESULT_BYTES,
            "diagnostic_console":diagnostic_mode(),
        }
    if op=="get_recent_tool_calls":
        limit=max(1,min(100,int(c.get("limit") or 25)))
        history=DATA/"tool-history.jsonl"
        if not history.exists():
            return {"count":0,"items":[],"metadata_only":True}
        items=[]
        with history.open("r",encoding="utf-8",errors="replace") as f:
            for line in f:
                try:
                    row=json.loads(line)
                    if c.get("filter_operation") and row.get("operation")!=c["filter_operation"]: continue
                    items.append(row)
                    if len(items)>1000: items.pop(0)
                except (ValueError,TypeError): continue
        return {"count":min(limit,len(items)),"items":items[-limit:][::-1],"metadata_only":True}
    if op=="auto_recovery":
        health=health_snapshot()
        actions=[]
        if not health["transport_online"]: actions.append("inspect_transport_connection")
        if not health["executor_online"]: actions.append("inspect_executor_process")
        if health["queue_stalled"]: actions.append("inspect_queue_before_replay")
        return {"mode":"assessment_only","recommendations":actions,"automatic_actions_taken":0,"read_only":True}
    if op=="usage_stats":
        history=DATA/"tool-history.jsonl"
        rows=[]
        if history.exists():
            with history.open("r",encoding="utf-8",errors="replace") as f:
                for line in f:
                    try:
                        item=json.loads(line)
                        if isinstance(item,dict) and "status" in item:
                            rows.append(item)
                            if len(rows)>1000: rows.pop(0)
                    except (ValueError,TypeError): pass
        completed=sum(1 for x in rows if x.get("status")=="completed")
        errors=sum(1 for x in rows if x.get("status")=="error")
        durations=[int(x.get("duration_ms") or 0) for x in rows]
        return {"sample_count":len(rows),"completed":completed,"errors":errors,"other":len(rows)-completed-errors,"average_duration_ms":round(sum(durations)/len(durations)) if durations else None,"metadata_only":True}
    if op=="diagnose_all":
        health=health_snapshot()
        state=_json_state(STATE)
        channel=_json_state(DATA/"supabase_channel_state.json")
        issues=[]
        if not health["transport_online"]: issues.append("transport_offline")
        if not health["executor_online"]: issues.append("executor_offline")
        if health["queue_stalled"]: issues.append("queue_stalled")
        if state.get("loaded_hands_sha256")!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(): issues.append("executor_code_mismatch")
        return {"ready":not issues,"issues":issues,"health":health,"transport_active_count":channel.get("active_count",0),"read_only":True}
    if op=="update_status":
        state=_json_state(STATE)
        report=_json_state(DATA/"agent_update_report.json")
        return {"manifest":_json_state(ROOT/"manifest.json"),"loaded_hands_sha256":state.get("loaded_hands_sha256"),"disk_hands_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"operation_count":state.get("operation_count"),"last_update_ok":report.get("ok"),"read_only":True}
    if op=="task_monitor":
        state=_json_state(STATE)
        channel=_json_state(DATA/"supabase_channel_state.json")
        return {"active_tasks":state.get("active_tasks",[]),"active_count":state.get("active_count",0),"transport_active_count":channel.get("active_count",0),"queue_stalled":bool(channel.get("queue_stalled")),"read_only":True}
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
    if op=="update_agent":
        from agent_control import start_agent_update
        return start_agent_update(c["release_sha"],"update")
    if op=="rollback_agent":
        from agent_control import start_agent_update
        return start_agent_update(None,"rollback")
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
        commands=c.get("commands")
        if commands is None:
            commands=c.get("operations")
        if not isinstance(commands,list) or not commands:
            raise ValueError("batch commands/operations is empty")
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
    if op=="read_file_lines":
        p=Path(str(c["path"])).resolve()
        offset=max(0,int(c.get("offset") or 0))
        length=max(1,min(200,int(c.get("length") or 50)))
        rows=[]
        eof=True
        with p.open("r",encoding="utf-8",errors="replace") as stream:
            for line_no,line in enumerate(stream):
                if line_no<offset: continue
                if len(rows)>=length:
                    eof=False
                    break
                rows.append(line[:4096])
        return {"path":str(p),"offset":offset,"next_offset":offset+len(rows),"lines":rows,"eof":eof}
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
    if op=="list_searches":
        limit=max(1,min(100,int(c.get("limit") or 25)))
        items=[]
        for sp in sorted(PROCESS_DIR.glob("search-*.json"),key=lambda p:p.stat().st_mtime,reverse=True):
            try:
                m=json.loads(sp.read_text(encoding="utf-8"))
                items.append({"sessionId":m.get("session_id") or sp.stem[7:],"count":len(m.get("results") or []),"stopped":bool(m.get("stopped")),"modified":sp.stat().st_mtime})
                if len(items)>=limit: break
            except (OSError,ValueError,TypeError): continue
        return {"count":len(items),"items":items}
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
    if op=="job_start":
        forwarded=dict(c); forwarded["operation"]="process_start"
        return execute(forwarded)
    if op in ("job_status","job_result"):
        task_id=str(c.get("job_id") or c.get("task_id") or "").strip()
        if not task_id: raise ValueError("job_id is required")
        mp=PROCESS_DIR/f"{task_id}.json"
        if not mp.exists(): raise FileNotFoundError(f"job metadata not found: {task_id}")
        m=json.loads(mp.read_text(encoding="utf-8")); outp=Path(m["stdout"]); errp=Path(m["stderr"])
        out=outp.read_text(encoding="utf-8",errors="replace") if outp.exists() else ""; err=errp.read_text(encoding="utf-8",errors="replace") if errp.exists() else ""
        running=m.get("exitcode") is None
        base={"job_id":task_id,"pid":m.get("pid"),"child_pid":m.get("child_pid"),"running":running,"completed":not running,"exitcode":m.get("exitcode"),"started_at":m.get("started_at")}
        if op=="job_result": base.update({"stdout":out,"stderr":err})
        else: base.update({"stdout_bytes":len(out.encode("utf-8")),"stderr_bytes":len(err.encode("utf-8"))})
        return base
    if op=="job_cancel":
        task_id=str(c.get("job_id") or c.get("task_id") or "").strip()
        if not task_id: raise ValueError("job_id is required")
        mp=PROCESS_DIR/f"{task_id}.json"
        if not mp.exists(): raise FileNotFoundError(f"job metadata not found: {task_id}")
        m=json.loads(mp.read_text(encoding="utf-8")); pid=m.get("pid")
        if not str(pid).isdigit(): return {"job_id":task_id,"cancelled":False,"reason":"job_not_started"}
        result=execute({"operation":"process_stop","pid":int(pid)})
        return {"job_id":task_id,"cancelled":bool(result.get("stopped")),"process":result}
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
    if op in ("visual_stop","visual_status"):
        run_id=str(c.get("run_id") or "")
        if not re.fullmatch(r"[a-f0-9]{32}",run_id):
            raise ValueError("valid run_id required")
        folder=DATA/"visual_runs"
        folder.mkdir(parents=True,exist_ok=True)
        state_file=folder/(run_id+".json")
        if op=="visual_status":
            if not state_file.is_file():
                return {"run_id":run_id,"found":False}
            return json.loads(state_file.read_text(encoding="utf-8"))
        if not state_file.is_file():
            return {"run_id":run_id,"stop_requested":False,"reason":"run not found"}
        state=json.loads(state_file.read_text(encoding="utf-8"))
        if state.get("status") not in ("running","pending"):
            return {"run_id":run_id,"stop_requested":False,"reason":"run already finished"}
        (folder/(run_id+".stop")).write_text("stop",encoding="ascii")
        return {"run_id":run_id,"stop_requested":True}
    if op=="visual_sequence":
        if c.get("confirm") is not True:
            raise PermissionError("confirm=true required")
        steps=c.get("steps")
        if not isinstance(steps,list) or not 1<=len(steps)<=8:
            raise ValueError("steps must contain 1..8 items")
        hwnd=int(c.get("target_hwnd") or 0)
        if hwnd<=0:
            raise ValueError("target_hwnd required")
        run_id=str(c.get("run_id") or uuid.uuid4().hex)
        if not re.fullmatch(r"[a-f0-9]{32}",run_id):
            raise ValueError("run_id must be 32 lowercase hex characters")
        # Optional deterministic visual change gate (not semantic OCR).
        for step in steps:
            if "expect_change" in step:
                value=step["expect_change"]
                if type(value) is not bool:
                    raise ValueError("expect_change must be a boolean")
                threshold=step.get("change_threshold",0.005)
                if not isinstance(threshold,(float,int)) or not 0.0<float(threshold)<1.0:
                    raise ValueError("change_threshold must be between 0 and 1")
        # Validate all steps before the first input event.
        for step in steps:
            if not isinstance(step,dict) or step.get("operation") not in ("mouse_action","keyboard_action"):
                raise ValueError("only mouse_action and keyboard_action allowed")
            if step["operation"]=="mouse_action" and step.get("action") not in ("move","click","scroll"):
                raise ValueError("unsupported mouse action")
            if step["operation"]=="keyboard_action" and step.get("action") not in ("text","key"):
                raise ValueError("unsupported keyboard action")
        if not VISUAL_SEQUENCE_LOCK.acquire(blocking=False):
            raise RuntimeError("another visual sequence is active")
        folder=DATA/"visual_runs"
        folder.mkdir(parents=True,exist_ok=True)
        state_file=folder/(run_id+".json")
        marker=folder/(run_id+".stop")
        if state_file.exists():
            VISUAL_SEQUENCE_LOCK.release()
            raise ValueError("run_id already exists")
        started=time.monotonic()
        state={"run_id":run_id,"target_hwnd":hwnd,"status":"pending","completed":0,
               "total":len(steps),"steps":[],"created_at":time.time()}
        def save():
            tmp=state_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state,ensure_ascii=False),encoding="utf-8")
            tmp.replace(state_file)
        def guard_window():
            observed=execute({"operation":"cursor_status"})["foreground"]
            if observed["hwnd"]!=hwnd or not observed["title"]:
                raise PermissionError("target window lost foreground")
            if observed["pid"]!=initial["pid"]:
                raise PermissionError("target process changed")
            return observed
        try:
            save()
            initial=execute({"operation":"cursor_status"})["foreground"]
            if initial["hwnd"]!=hwnd or not initial["title"]:
                raise PermissionError("target window not foreground at start")
            state["status"]="running"
            save()
            for index,step in enumerate(steps):
                if marker.exists():
                    state["status"]="stopped"
                    break
                if time.monotonic()-started>20:
                    raise TimeoutError("visual sequence exceeded 20 seconds")
                guard_window()
                before=None
                if "expect_change" in step:
                    from PIL import ImageGrab,ImageChops,ImageStat
                    # Primary desktop coordinates; compare a stable window rectangle.
                    bounds=guard_window()["bounds"]
                    desktop=ImageGrab.grab()
                    box=(max(0,bounds[0]),max(0,bounds[1]),
                         min(desktop.width,bounds[2]),
                         min(desktop.height,bounds[3]))
                    if box[2]<=box[0] or box[3]<=box[1]:
                        raise ValueError("target window outside primary screen")
                    before=desktop.crop(box).convert("RGB")
                    before.thumbnail((640,360))
                action=step["operation"]
                command={k:v for k,v in step.items() if k not in ("confirm","target_hwnd","expect_change","change_threshold")}
                command.update({"operation":action,"confirm":True,"target_hwnd":hwnd})
                result=execute(command)
                state["completed"]+=1
                item={"index":index,"operation":action,"performed":result.get("performed"),
                      "key":result.get("key"),"characters":result.get("characters")}
                state["steps"].append(item)
                save()
                # Observe each action, but never return all images through transport.
                guard_window()
                capture=execute({"operation":"screen_capture","target_hwnd":hwnd,
                                 "max_width":int(c.get("max_width") or 960),
                                 "inline_preview":False})
                item["capture"]={"path":capture["path"],"sha256":capture["sha256"],
                                 "width":capture["width"],"height":capture["height"]}
                if before is not None:
                    after=ImageGrab.grab().crop(box).convert("RGB")
                    after=after.resize(before.size)
                    difference=ImageChops.difference(before,after)
                    # Mean RGB absolute difference normalized to [0,1].
                    score=sum(ImageStat.Stat(difference).mean)/(3*255)
                    expected=step["expect_change"]
                    threshold=float(step.get("change_threshold",0.005))
                    passed=(score>=threshold) if expected else (score<threshold)
                    item["visual_check"]={"expected_change":expected,
                                          "change_score":round(score,6),
                                          "threshold":threshold,"passed":passed}
                    save()
                    if not passed:
                        raise RuntimeError("visual change check failed at step "+str(index))
                save()
                if index<len(steps)-1:
                    time.sleep(0.08)
            if state["status"]=="running":
                state["status"]="stopped" if marker.exists() else "completed"
            state["finished_at"]=time.time()
            save()
            result={"run_id":run_id,"status":state["status"],"stopped":state["status"]=="stopped",
                    "completed":state["completed"],"steps":state["steps"]}
            if bool(c.get("inline_preview")) and state["status"]=="completed":
                guard_window()
                result["capture"]=execute({"operation":"screen_capture","target_hwnd":hwnd,
                                           "inline_preview":True,
                                           "max_width":int(c.get("max_width") or 960)})
            return result
        except Exception as exc:
            state["status"]="failed"
            state["error"]=f"{type(exc).__name__}: {exc}"
            state["finished_at"]=time.time()
            save()
            raise
        finally:
            marker.unlink(missing_ok=True)
            VISUAL_SEQUENCE_LOCK.release()
    if op in ("cursor_status","mouse_action","keyboard_action"):
        import ctypes
        from ctypes import wintypes
        user32=ctypes.WinDLL("user32",use_last_error=True)
        class POINT(ctypes.Structure):
            _fields_=[("x",wintypes.LONG),("y",wintypes.LONG)]
        class RECT(ctypes.Structure):
            _fields_=[("left",wintypes.LONG),("top",wintypes.LONG),("right",wintypes.LONG),("bottom",wintypes.LONG)]
        user32.GetForegroundWindow.restype=wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(RECT)]
        user32.GetCursorPos.argtypes=[ctypes.POINTER(POINT)]
        hwnd=user32.GetForegroundWindow()
        pid=wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        title=ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd,title,512)
        rect=RECT()
        user32.GetWindowRect(hwnd,ctypes.byref(rect))
        point=POINT()
        user32.GetCursorPos(ctypes.byref(point))
        foreground={"hwnd":int(hwnd or 0),"pid":pid.value,"title":title.value,
                    "bounds":[rect.left,rect.top,rect.right,rect.bottom]}
        if op=="cursor_status":
            return {"cursor":{"x":point.x,"y":point.y},"foreground":foreground,"read_only":True}
        # Both explicit consent and exact foreground HWND are required for every action.
        if c.get("confirm") is not True:
            raise PermissionError("confirm=true required for desktop input")
        if int(c.get("target_hwnd") or 0)!=int(hwnd or 0) or not hwnd:
            raise PermissionError("foreground window changed or target_hwnd missing")
        if not title.value:
            raise PermissionError("untitled foreground window not allowed")
        if op=="mouse_action":
            action=str(c.get("action") or "").lower()
            if action not in ("move","click","scroll"):
                raise ValueError("mouse action must be move, click or scroll")
            if action in ("move","click"):
                x=int(c["x"]); y=int(c["y"])
                if not (rect.left<=x<rect.right and rect.top<=y<rect.bottom):
                    raise ValueError("coordinates outside target window")
                if not user32.SetCursorPos(x,y):
                    raise OSError("SetCursorPos failed")
                if action=="click":
                    user32.mouse_event(0x0002,0,0,0,0)
                    user32.mouse_event(0x0004,0,0,0,0)
            else:
                delta=int(c.get("delta") or 0)
                if not -1200<=delta<=1200 or delta==0:
                    raise ValueError("scroll delta must be -1200..1200, nonzero")
                user32.mouse_event(0x0800,0,0,ctypes.c_ulong(delta).value,0)
            return {"performed":action,"foreground":foreground,"read_only":False}
        action=str(c.get("action") or "").lower()
        if action=="text":
            value=str(c.get("text") or "")
            if not value or len(value)>500:
                raise ValueError("text length must be 1..500")
            # Unicode key events; no clipboard, shell, shortcuts or passwords.
            for char in value:
                code=ord(char)
                if code>0xFFFF or code in (0,):
                    raise ValueError("unsupported Unicode character")
                user32.keybd_event(0,code,0x0004,0)
                user32.keybd_event(0,code,0x0004|0x0002,0)
            return {"performed":"text","characters":len(value),"foreground":foreground}
        if action=="key":
            keys={"enter":0x0D,"tab":0x09,"escape":0x1B,"backspace":0x08,
                  "left":0x25,"up":0x26,"right":0x27,"down":0x28}
            key=str(c.get("key") or "").lower()
            if key not in keys:
                raise ValueError("key not in safe allowlist")
            user32.keybd_event(keys[key],0,0,0)
            user32.keybd_event(keys[key],0,0x0002,0)
            return {"performed":"key","key":key,"foreground":foreground}
        raise ValueError("keyboard action must be text or key")
    if op=="screen_capture":
        from PIL import ImageGrab
        width_limit=max(320,min(1920,int(c.get("max_width") or 1280)))
        # Coordinates refer to the primary desktop, not to resized preview pixels.
        image=ImageGrab.grab(all_screens=False)
        if image.width<=0 or image.height<=0:
            raise RuntimeError("No interactive desktop available")
        original_size=[image.width,image.height]
        region=c.get("region")
        target_hwnd=c.get("target_hwnd")
        if region is not None and target_hwnd is not None:
            raise ValueError("region and target_hwnd are mutually exclusive")
        if target_hwnd is not None:
            import ctypes
            from ctypes import wintypes
            user32=ctypes.WinDLL("user32",use_last_error=True)
            class RECT(ctypes.Structure):
                _fields_=[("left",wintypes.LONG),("top",wintypes.LONG),
                          ("right",wintypes.LONG),("bottom",wintypes.LONG)]
            user32.IsWindow.argtypes=[wintypes.HWND]
            user32.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(RECT)]
            hwnd=int(target_hwnd)
            if not hwnd or not user32.IsWindow(hwnd):
                raise ValueError("invalid target window handle")
            rect=RECT()
            if not user32.GetWindowRect(hwnd,ctypes.byref(rect)):
                raise OSError("GetWindowRect failed")
            region=[rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top]
        crop_box=None
        if region is not None:
            if not isinstance(region,(list,tuple)) or len(region)!=4:
                raise ValueError("region must be [x,y,width,height]")
            x,y,width,height=[int(v) for v in region]
            if width<=0 or height<=0 or width*height>12000000:
                raise ValueError("invalid or oversized region")
            left=max(0,x);top=max(0,y)
            right=min(image.width,x+width);bottom=min(image.height,y+height)
            if right<=left or bottom<=top:
                raise ValueError("region outside primary desktop")
            crop_box=[left,top,right,bottom]
            image=image.crop(tuple(crop_box))
        if image.width>width_limit:
            height=max(1,round(image.height*width_limit/image.width))
            image=image.resize((width_limit,height))
        folder=DATA/"screenshots"
        folder.mkdir(parents=True,exist_ok=True)
        file=folder/(f"screen_{int(time.time())}_{uuid.uuid4().hex[:8]}.png")
        image.save(file,format="PNG",optimize=True)
        result={"path":str(file),"width":image.width,"height":image.height,
                "source_size":original_size,"crop_box":crop_box,
                "bytes":file.stat().st_size,"sha256":hashlib.sha256(file.read_bytes()).hexdigest(),
                "read_only":True,"image_included":False}
        if bool(c.get("inline_preview")):
            # Keep JSON result under the transport's compact payload limit.
            import io
            preview=image.convert("RGB")
            quality=68
            while True:
                buffer=io.BytesIO()
                preview.save(buffer,format="JPEG",quality=quality,optimize=True)
                if len(buffer.getvalue())<=24000 or preview.width<=320:
                    break
                preview.thumbnail((max(320,int(preview.width*.8)),max(180,int(preview.height*.8))))
            result.update({"preview_mime":"image/jpeg",
                           "preview_base64":base64.b64encode(buffer.getvalue()).decode("ascii"),
                           "preview_width":preview.width,"preview_height":preview.height,
                           "image_included":True})
        return result
    if op=="window_list":
        ps="$ErrorActionPreference='Stop'; Get-Process | Where-Object {$_.MainWindowHandle -ne 0 -and $_.MainWindowTitle} | Select-Object Id,ProcessName,MainWindowTitle | ConvertTo-Json -Compress -Depth 3"
        r=subprocess.run(powershell_args(ps),capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=20)
        if r.returncode: raise RuntimeError("window_list failed: "+r.stderr[-500:])
        raw=r.stdout.strip()
        result=json.loads(raw) if raw else []
        return {"windows":result if isinstance(result,list) else [result],"read_only":True}
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
        console(f'[ОЖИДАНИЕ] ⏳ Выполняется: {title}; задача {task}')
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
                console(f'[ОШИБКА] Код завершения: {rc}')
                if out:
                    console('[РЕЗУЛЬТАТ] '+out)
                if err:
                    console('[ОШИБКА] '+err)
                console(f'[ВРЕМЯ] ⏱️ {completed-started:.1f} сек; задача {task}')
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
                        console(f"[РЕЗУЛЬТАТ {task}] "+str(out).strip())
                else:
                    console_task_result(payload,op)
                    if op!="health":
                        console("[ГОТОВО] Выполнено успешно")
                console(f'[ВРЕМЯ] ⏱️ {completed-started:.1f} сек; задача {task}')
        except Exception as e:
            completed=time.time()
            result={"task_id":task,"status":"error","stage":"failed","error":mask_encoded_payloads(f"{type(e).__name__}: {e}"),
                    "timing":{"started_at":started,"completed_at":completed,
                              "duration_ms":int((completed-started)*1000)}}
            if isinstance(e,subprocess.TimeoutExpired):
                result["timeout_seconds"]=e.timeout
                for key,data in (("stdout",e.output),("stderr",e.stderr)):
                    if data:
                        result[key]=data.decode("utf-8","replace") if isinstance(data,bytes) else str(data)
            metrics=_metrics_snapshot()
            _update_metrics(last_completed_at=completed,failed_count=int(metrics.get("failed_count") or 0)+1)
            if isinstance(e,subprocess.TimeoutExpired):
                console(f'[ТАЙМ-АУТ] Команда превысила {e.timeout} сек; дочерний процесс остановлен')
                for label,data in (('[РЕЗУЛЬТАТ]',e.output),('[ОШИБКА]',e.stderr)):
                    if data:
                        value=data.decode('utf-8','replace') if isinstance(data,bytes) else str(data)
                        console(label+' '+normalize_powershell_stream(value))
            else:
                console(f'[ОШИБКА] {type(e).__name__}: {e}')
            console(f'[ВРЕМЯ] ⏱️ {completed-started:.1f} сек; задача {task}')
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
    _record_task_history(task,op,result,started)
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
