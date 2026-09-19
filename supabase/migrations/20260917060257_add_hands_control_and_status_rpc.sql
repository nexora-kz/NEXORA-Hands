create or replace function public.hands_control_command(p_task_id text, p_worker_id text, p_command jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare v_id uuid;
begin
  if not public.nexora_transport_worker_authorized() then
    raise exception 'unauthorized';
  end if;
  v_id := public.hands_enqueue_command(p_task_id, p_worker_id, p_command);
  return jsonb_build_object('ok', true, 'task_id', p_task_id, 'worker_id', p_worker_id, 'id', v_id);
end;
$$;

create or replace function public.hands_command_status(p_task_id text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare v_cmd jsonb; v_result jsonb;
begin
  if not public.nexora_transport_worker_authorized() then
    raise exception 'unauthorized';
  end if;
  select to_jsonb(x) into v_cmd from (select id, task_id, status, worker_id, created_at, claimed_at, completed_at, error from public.hands_commands where task_id=p_task_id limit 1) x;
  select to_jsonb(x) into v_result from (select id, task_id, result, worker_id, created_at from public.hands_results where task_id=p_task_id order by created_at desc limit 1) x;
  return jsonb_build_object('command', coalesce(v_cmd,'null'::jsonb), 'result', coalesce(v_result,'null'::jsonb));
end;
$$;

revoke all on function public.hands_control_command(text,text,jsonb) from public;
grant execute on function public.hands_control_command(text,text,jsonb) to anon, authenticated, service_role;
revoke all on function public.hands_command_status(text) from public;
grant execute on function public.hands_command_status(text) to anon, authenticated, service_role;;
