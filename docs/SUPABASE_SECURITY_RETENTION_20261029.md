# Supabase security and retention audit — 2026-10-29

## Verified findings
- `hands_mcp_command`, `hands_mcp_list_workers`, `hands_mcp_status`: SECURITY DEFINER, EXECUTE granted to `authenticated`. Their SQL bodies do not verify user ownership of the target worker or task. Any appropriately authenticated API caller may be able to enqueue remote commands and read results. This is a high-priority authorization gap.
- The live MCP Edge Function uses a user-scoped Supabase client for these RPCs. Blindly revoking authenticated EXECUTE would break the existing connector.
- `hands_register_worker()` grants `anon` registration of a new worker credential. The installed Windows client intentionally uses this on first setup. A future enrollment control must preserve first-install UX while preventing arbitrary registration/spam.
- `hands_monitor_pair()` has an explicit token-and-machine identity check before reading a remote worker's events; review it separately.
- `hands_submit_result_atomic` and `hands_worker_heartbeat` verify worker/channel tokens. Their grants are part of the client protocol.
- Some legacy RPC paths and static shared-token fallback still exist; review migration away from the legacy fallback before changing production.
- Do not put worker secrets, command payloads or stdout in this report.

## Production change completed
- Fixed `normalize_transport_command_status()` mutable search_path by applying `ALTER FUNCTION ... SET search_path = ''`.
- Supabase migration name: `harden_transport_status_function_path`, version `20261028220800`. Matching SQL file committed in the repository.
- Verified `pg_proc.proconfig` now reports an empty fixed search_path; Supabase security advisor no longer reports the mutable-path warning.
- No change to any worker authorization grants, RLS, command/result rows, or Edge Functions.

## Storage and archival assessment (read-only)
- `hands_results`: 56,363 rows, 43,919 older than 7 days; ~141 MB total.
- `hands_commands`: 55,611 rows, 43,148 older than 7 days; ~69 MB total.
- `transport_results`: 11,181 rows, all older than 30 days; ~133 MB total.
- `transport_results.command_id` references `transport_commands.id`. Archive/delete must preserve referential integrity and replay/audit semantics.
- No database rows deleted or copied. No archive was claimed complete. No large data exported without an established secure backup channel.
- Added `tools/supabase-retention-audit.sql` for repeatable read-only age counts.

## Unified release improvement
- `tools/unified-release.ps1` now detects `supabase/migrations` changes against the published source HEAD, not just changed Edge Functions.
- Check mode fails closed with REVIEW_REQUIRED when migrations changed and not verified. Verified negative test exited 1.
- Apply mode requires explicit `-MigrationsVerified` after independent Supabase MCP verification; it does NOT automatically execute migrations. Verified Apply PASS with unchanged installed runtime SHA and 62 loaded operations.
- Source commit `a5a94d990f7e1e45f0ece14d4f4cfde2f9e8154e` published to GitHub. Runtime remains `60db12cd51d38c20c6d551ebaac122819c030ee6`, bootstrap pin `f55cf5510cd9fdd4539ed43cd31f51c2d0815d1a`.

## Next
1. Establish explicit owner-to-worker authorization using authenticated OAuth identity and private ACL, then test direct authenticated RPC calls and the live MCP connector before changing grants.
2. Introduce controlled first-install enrollment (one-time invitation / short-lived pairing) to replace unrestricted anon worker registration.
3. Obtain a verifiable encrypted backup/archive route for historic result rows before defining any destructive retention policy.
