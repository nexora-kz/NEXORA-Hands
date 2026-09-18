# NEXORA Hands

Standalone Windows remote-control software for NEXORA.

## Working flow

**Launch page → NEXORA-Hands.cmd → automatic runtime setup → NEXORA Hands → NEXORA control plane → Windows PC**

The end-user does not need Git or Remote Desktop Commander.

### On a new Windows PC

1. Open the public launch page.
2. Download `NEXORA-Hands.cmd`.
3. Run the downloaded file.
4. The visible PowerShell console installs missing runtime components automatically.
5. NEXORA Hands starts and registers this PC as a worker through the NEXORA transport.
6. The same PowerShell window remains the live local runtime console.

Node.js and Python are installed automatically when they are missing. Git is not required.

The worker receives commands through the NEXORA control plane and executes them locally on Windows.

## Control Center

`control.html` is the browser-based NEXORA Hands Control Center. It discovers registered Hands workers, lets the operator select a PC, sends supported Hands operations, and displays execution results.

The Control Center uses the existing Supabase-backed Hands control layer. It does not use OpenAI, ChatGPT, MCP, Remote Desktop Commander, or a fourth worker transport.

## Architecture

**Control Center → Hands control API → Supabase Hands control layer → Hands worker → Windows PC**

The worker and launcher are standalone components and do not depend on OpenAI services.
## Friendly worker names

hands_list_workers returns a permanent friendly name for every PC.

Known aliases:
- NEXORA-LAPTOP -> LAPTOP-PJ1VRBPC
- NEXORA-DESKTOP -> DESKTOP-PQU4USG

Every other worker automatically receives a stable fallback name in the form NEXORA-PC-XXXXXXXX, derived from its persistent worker ID.

hands_execute.worker_id accepts either the real worker ID or any returned friendly name/alias. This lets a chat say, for example, work only on NEXORA-LAPTOP without copying the UUID.

## Health and reliability

The Windows runtime keeps transport and executor health separate. A server heartbeat is refreshed only while the local executor state is fresh. The launcher watchdog restarts a dead/stale executor or transport automatically.

The executor supports lightweight `health`, `get_capabilities`, and `local_queue` operations. Large text results are saved in `data/results`; the chat receives a bounded preview plus local path, byte size, and SHA-256.

Normal console output is intentionally compact and does not show internal task IDs. Set `NEXORA_HANDS_DIAGNOSTIC=1` before launch to enable task IDs, command previews, and full diagnostic console output.