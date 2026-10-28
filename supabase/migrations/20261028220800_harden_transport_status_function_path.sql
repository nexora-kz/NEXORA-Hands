-- No table or RPC changes. Trigger body references only PL/pgSQL variables.
-- Empty search_path prevents caller-controlled object resolution.
ALTER FUNCTION public.normalize_transport_command_status() SET search_path = '';
