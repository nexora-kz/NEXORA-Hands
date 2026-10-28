-- NEXORA Hands: read-only retention audit. Never delete before an independently verified archive.
-- Run against Supabase PostgreSQL; no data modifications.
SELECT 'hands_results' AS table_name, count(*) AS rows_total,
       count(*) FILTER (WHERE created_at < now() - interval '7 days') AS older_7d,
       count(*) FILTER (WHERE created_at < now() - interval '30 days') AS older_30d,
       min(created_at) AS earliest, max(created_at) AS latest
  FROM public.hands_results
UNION ALL
SELECT 'hands_commands',count(*),
       count(*) FILTER (WHERE created_at < now() - interval '7 days'),
       count(*) FILTER (WHERE created_at < now() - interval '30 days'),
       min(created_at),max(created_at) FROM public.hands_commands
UNION ALL
SELECT 'transport_results',count(*),
       count(*) FILTER (WHERE created_at < now() - interval '7 days'),
       count(*) FILTER (WHERE created_at < now() - interval '30 days'),
       min(created_at),max(created_at) FROM public.transport_results;
-- Note: transport_results.command_id references transport_commands.id.
-- Do not prune rows needed by active leases, result retrieval, replay protection, or audit.
-- A backup must be independently readable and row-count/hash verified before any pruning.
