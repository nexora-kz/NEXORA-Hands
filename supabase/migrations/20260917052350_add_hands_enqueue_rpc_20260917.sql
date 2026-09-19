create or replace function public.hands_enqueue_command(
  p_task_id text,
  p_worker_id text,
  p_command jsonb
)
returns uuid
language plpgsql
security definer
set search_path = 'pg_catalog', 'public', 'nexora_private'
as $function$
declare
  v_id uuid;
  v_channel_token text;
begin
  if not public.nexora_transport_worker_authorized() then
    raise exception 'unauthorized transport worker' using errcode = '42501';
  end if;

  if coalesce(trim(p_task_id), '') = '' then
    raise exception 'task_id required' using errcode = '22023';
  end if;

  if coalesce(trim(p_worker_id), '') = '' then
    raise exception 'worker_id required' using errcode = '22023';
  end if;

  select a.token_sha256
    into v_channel_token
  from nexora_private.hands_worker_auth a
  where a.enabled
    and a.worker_id = p_worker_id;

  if v_channel_token is null then
    raise exception 'hands worker not found or disabled' using errcode = '22023';
  end if;

  insert into public.hands_commands(
    task_id,
    status,
    command,
    worker_id,
    channel_token
  )
  values (
    p_task_id,
    'queued',
    p_command,
    p_worker_id,
    v_channel_token
  )
  returning id into v_id;

  return v_id;
exception
  when unique_violation then
    raise exception 'task_id already exists' using errcode = '23505';
end;
$function$;

grant execute on function public.hands_enqueue_command(text, text, jsonb) to anon, authenticated;;
