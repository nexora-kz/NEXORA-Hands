# NEXORA Hands

Standalone Windows execution layer for NEXORA.

Target flow:

ChatGPT → NEXORA transport → NEXORA Hands → Windows PC

## Remote bootstrap

The repository contains the Windows bootstrap used to prepare the local runtime.

The final public one-click flow will be:

1. Open bootstrap link.
2. Node.js LTS is installed if needed.
3. NEXORA Hands runtime is obtained.
4. PowerShell remains open as the live local runtime console.

The Supabase runtime configuration is intentionally not published in this repository.
