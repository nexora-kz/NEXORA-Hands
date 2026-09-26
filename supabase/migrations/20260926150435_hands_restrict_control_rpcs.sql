-- Restrict internal control-plane RPCs to the service role.
-- Worker-facing RPCs and authenticated MCP RPCs keep their intentional grants.
revoke execute on function public.hands_control_command(text,text,jsonb) from public, anon, authenticated;
revoke execute on function public.hands_command_status(text) from public, anon, authenticated;
revoke execute on function public.hands_list_workers() from public, anon, authenticated;
grant execute on function public.hands_control_command(text,text,jsonb) to service_role;
grant execute on function public.hands_command_status(text) to service_role;
grant execute on function public.hands_list_workers() to service_role;
