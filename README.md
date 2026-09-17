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
