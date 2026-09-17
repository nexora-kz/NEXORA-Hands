# NEXORA Hands

Standalone Windows execution layer for NEXORA.

## Working flow

**Launch page → NEXORA-Hands.cmd → automatic runtime setup → NEXORA Hands → ChatGPT/NEXORA transport → Windows PC**

The end-user does not need Git or Remote Desktop Commander.

### On a new Windows PC

1. Open the public launch page.
2. Download `NEXORA-Hands.cmd`.
3. Run the downloaded file.
4. The visible PowerShell console installs missing runtime components automatically.
5. NEXORA Hands starts and registers this PC as a worker through the NEXORA transport.
6. The same PowerShell window remains the live local runtime console.

Node.js LTS and Python are installed automatically when they are missing. Git is not required.

The worker receives commands through the NEXORA transport and executes them locally on Windows.

## Direct ChatGPT control

NEXORA Hands now exposes a dedicated remote MCP control endpoint backed by the existing Supabase Hands control layer. The endpoint provides:

- `hands_list_workers` — discover registered Hands PCs.
- `hands_execute` — send a supported Hands operation to a selected PC.
- `hands_command_status` — read execution status and result.

The MCP endpoint is deployed as the `nexora-hands-mcp` Supabase Edge Function. It does not create a fourth transport: it is a control interface on top of the existing Hands/Supabase path.

The intended architecture is:

**ChatGPT → NEXORA Hands MCP → existing Hands control API → Supabase → Hands worker → Windows PC**

The local worker and launcher remain unchanged.
