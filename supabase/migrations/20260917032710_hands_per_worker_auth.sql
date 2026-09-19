create table if not exists nexora_private.hands_worker_auth (
  token_sha256 text primary key,
  worker_id text not null unique,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz
);

revoke all on nexora_private.hands_worker_auth from public, anon, authenticated;

create or replace function nexora_private.hands_request_authorized(p_worker_id text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from nexora_private.hands_worker_auth a
    where a.enabled
      and a.worker_id = p_worker_id
      and a.token_sha256 = encode(
        extensions.digest(
          coalesce((coalesce(current_setting('request.headers', true), '{}')::jsonb ->> 'x-nexora-hands-token'), ''),
          'sha256'
        ),
        'hex'
      )
  )
$$;

revoke all on function nexora_private.hands_request_authorized(text) from public, anon, authenticated;

drop policy if exists hands_commands_insert on public.hands_commands;
drop policy if exists hands_commands_select on public.hands_commands;
drop policy if exists hands_commands_update on public.hands_commands;
drop policy if exists hands_results_insert on public.hands_results;
drop policy if exists hands_results_select on public.hands_results;

create policy hands_commands_insert on public.hands_commands
for insert to anon, authenticated
with check (
  channel_token = encode(extensions.digest('NEXORA_HANDS_LOCAL_CHANNEL_20260917','sha256'),'hex')
);

create policy hands_commands_select on public.hands_commands
for select to anon, authenticated
using (
  channel_token = encode(extensions.digest('NEXORA_HANDS_LOCAL_CHANNEL_20260917','sha256'),'hex')
  or (
    channel_token = encode(extensions.digest(coalesce((coalesce(current_setting('request.headers', true),'{}')::jsonb ->> 'x-nexora-hands-token'),''),'sha256'),'hex')
    and worker_id is not null
    and (select nexora_private.hands_request_authorized(worker_id))
  )
);

create policy hands_commands_update on public.hands_commands
for update to anon, authenticated
using (
  channel_token = encode(extensions.digest('NEXORA_HANDS_LOCAL_CHANNEL_20260917','sha256'),'hex')
  or (
    worker_id is not null
    and (select nexora_private.hands_request_authorized(worker_id))
  )
)
with check (
  channel_token = encode(extensions.digest('NEXORA_HANDS_LOCAL_CHANNEL_20260917','sha256'),'hex')
  or (
    worker_id is not null
    and (select nexora_private.hands_request_authorized(worker_id))
  )
);

create policy hands_results_insert on public.hands_results
for insert to anon, authenticated
with check (
  channel_token = encode(extensions.digest('NEXORA_HANDS_LOCAL_CHANNEL_20260917','sha256'),'hex')
  or (
    worker_id is not null
    and (select nexora_private.hands_request_authorized(worker_id))
  )
);

create policy hands_results_select on public.hands_results
for select to anon, authenticated
using (
  channel_token = encode(extensions.digest('NEXORA_HANDS_LOCAL_CHANNEL_20260917','sha256'),'hex')
  or (
    worker_id is not null
    and (select nexora_private.hands_request_authorized(worker_id))
  )
);

create or replace function public.hands_register_worker()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_worker_id text;
  v_token text;
  v_hash text;
begin
  v_worker_id := 'nexora-hands-' || replace(gen_random_uuid()::text, '-', '');
  v_token := encode(extensions.gen_random_bytes(32), 'hex');
  v_hash := encode(extensions.digest(v_token, 'sha256'), 'hex');

  insert into nexora_private.hands_worker_auth(token_sha256, worker_id)
  values (v_hash, v_worker_id);

  return jsonb_build_object('worker_id', v_worker_id, 'token', v_token);
end;
$$;

revoke all on function public.hands_register_worker() from public;
grant execute on function public.hands_register_worker() to anon, authenticated;

create or replace function public.hands_submit_result_atomic(
  p_task_id text,
  p_worker_id text,
  p_lease_token text,
  p_channel_token text,
  p_result jsonb,
  p_error text default null
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_status text;
  v_updated integer;
  v_authorized boolean;
begin
  v_authorized :=
    p_channel_token = encode(extensions.digest('NEXORA_HANDS_LOCAL_CHANNEL_20260917','sha256'),'hex')
    or exists (
      select 1 from nexora_private.hands_worker_auth a
      where a.enabled and a.worker_id = p_worker_id and a.token_sha256 = p_channel_token
    );
  if not v_authorized then return false; end if;

  v_status := case when p_error is null then 'completed' else 'error' end;

  update public.hands_commands
     set status = v_status,
         completed_at = now(),
         error = p_error,
         worker_id = p_worker_id
   where task_id = p_task_id
     and status = 'claimed'
     and worker_id = p_worker_id
     and lease_token = p_lease_token
     and channel_token = p_channel_token;

  get diagnostics v_updated = row_count;
  if v_updated <> 1 then return false; end if;

  insert into public.hands_results(task_id, result, worker_id, channel_token, lease_token)
  values (p_task_id, p_result, p_worker_id, p_channel_token, p_lease_token);

  update nexora_private.hands_worker_auth
     set last_seen_at = now()
   where worker_id = p_worker_id;

  return true;
end;
$$;;
