create or replace function public.hands_enqueue_command(p_task_id text, p_worker_id text, p_command jsonb)
returns uuid
language plpgsql
security definer
set search_path to 'pg_catalog','public','nexora_private'
as $$
declare v_id uuid; v_channel_token text;
begin
  if coalesce(trim(p_task_id),'')='' then raise exception 'task_id required' using errcode='22023'; end if;
  if coalesce(trim(p_worker_id),'')='' then raise exception 'worker_id required' using errcode='22023'; end if;
  select a.token_sha256 into v_channel_token from nexora_private.hands_worker_auth a where a.enabled and a.worker_id=p_worker_id;
  if v_channel_token is null then raise exception 'hands worker not found or disabled' using errcode='22023'; end if;
  insert into public.hands_commands(task_id,status,command,worker_id,channel_token) values(p_task_id,'queued',p_command,p_worker_id,v_channel_token) returning id into v_id;
  return v_id;
exception when unique_violation then raise exception 'task_id already exists' using errcode='23505';
end;
$$;

create or replace function public.hands_control_command(p_task_id text,p_worker_id text,p_command jsonb)
returns jsonb
language plpgsql
security definer
set search_path to 'public','pg_temp'
as $$
declare v_id uuid;
begin
  v_id := public.hands_enqueue_command(p_task_id,p_worker_id,p_command);
  return jsonb_build_object('ok',true,'task_id',p_task_id,'worker_id',p_worker_id,'id',v_id);
end;
$$;

create or replace function public.hands_command_status(p_task_id text)
returns jsonb
language plpgsql
security definer
set search_path to 'public','pg_temp'
as $$
declare v_cmd jsonb; v_result jsonb;
begin
  select to_jsonb(x) into v_cmd from (select id,task_id,status,worker_id,created_at,claimed_at,completed_at,error from public.hands_commands where task_id=p_task_id limit 1) x;
  select to_jsonb(x) into v_result from (select id,task_id,result,worker_id,created_at from public.hands_results where task_id=p_task_id order by created_at desc limit 1) x;
  return jsonb_build_object('command',coalesce(v_cmd,'null'::jsonb),'result',coalesce(v_result,'null'::jsonb));
end;
$$;;
