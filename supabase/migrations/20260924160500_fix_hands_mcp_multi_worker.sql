-- Replace manually-created production MCP RPCs with versioned canonical definitions.
drop function if exists public.hands_mcp_list_workers();
drop function if exists public.hands_mcp_command(text,text,jsonb);
drop function if exists public.hands_mcp_status(text);

-- Canonical OAuth MCP bridge for NEXORA Hands multi-worker registration.
-- Never exposes worker token hashes.

create or replace function public.hands_mcp_list_workers()
returns table(worker_id text, enabled boolean, created_at timestamptz, last_seen_at timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
  select a.worker_id, a.enabled, a.created_at, a.last_seen_at
  from nexora_private.hands_worker_auth a
  where a.enabled = true
  order by a.last_seen_at desc nulls last, a.created_at desc;
$$;

create or replace function public.hands_mcp_command(p_task_id text, p_worker_id text, p_command jsonb)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_channel_token text;
  v_id uuid;
begin
  if coalesce(trim(p_task_id), '') = '' then raise exception 'task_id required' using errcode='22023'; end if;
  if coalesce(trim(p_worker_id), '') = '' then raise exception 'worker_id required' using errcode='22023'; end if;
  select a.token_sha256 into v_channel_token
  from nexora_private.hands_worker_auth a
  where a.worker_id = p_worker_id and a.enabled = true;
  if v_channel_token is null then raise exception 'hands worker not found or disabled' using errcode='22023'; end if;
  insert into public.hands_commands(task_id,status,command,worker_id,channel_token)
  values(p_task_id,'queued',coalesce(p_command,'{}'::jsonb),p_worker_id,v_channel_token)
  returning id into v_id;
  return jsonb_build_object('ok',true,'id',v_id,'task_id',p_task_id,'worker_id',p_worker_id);
exception when unique_violation then
  raise exception 'task_id already exists' using errcode='23505';
end;
$$;

create or replace function public.hands_mcp_status(p_task_id text)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare v_cmd jsonb; v_result jsonb;
begin
  select to_jsonb(x) into v_cmd from (
    select id,task_id,status,worker_id,created_at,claimed_at,completed_at,error
    from public.hands_commands where task_id=p_task_id limit 1
  ) x;
  select to_jsonb(x) into v_result from (
    select id,task_id,result,worker_id,created_at
    from public.hands_results where task_id=p_task_id order by created_at desc limit 1
  ) x;
  return jsonb_build_object('command',coalesce(v_cmd,'null'::jsonb),'result',coalesce(v_result,'null'::jsonb));
end;
$$;

revoke all on function public.hands_mcp_list_workers() from public, anon;
revoke all on function public.hands_mcp_command(text,text,jsonb) from public, anon;
revoke all on function public.hands_mcp_status(text) from public, anon;
grant execute on function public.hands_mcp_list_workers() to authenticated, service_role;
grant execute on function public.hands_mcp_command(text,text,jsonb) to authenticated, service_role;
grant execute on function public.hands_mcp_status(text) to authenticated, service_role;

