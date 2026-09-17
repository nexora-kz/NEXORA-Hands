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
