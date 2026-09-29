alter table nexora_private.hands_worker_auth
  add column if not exists machine_identity text;

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
  v_machine text;
begin
  v_machine := upper(nullif(coalesce(current_setting('request.headers', true), '{}')::jsonb ->> 'x-nexora-machine-id', ''));
  v_worker_id := 'nexora-hands-' || replace(gen_random_uuid()::text, '-', '');
  v_token := encode(extensions.gen_random_bytes(32), 'hex');
  v_hash := encode(extensions.digest(v_token, 'sha256'), 'hex');
  insert into nexora_private.hands_worker_auth(token_sha256, worker_id, machine_identity)
  values (v_hash, v_worker_id, v_machine);
  return jsonb_build_object('worker_id', v_worker_id, 'token', v_token);
end;
$$;

create or replace function public.hands_worker_heartbeat(p_worker_id text, p_channel_token text)
returns boolean
language plpgsql
security definer
set search_path = 'public', 'nexora_private', 'extensions'
as $$
declare
  v_machine text;
begin
  v_machine := upper(nullif(coalesce(current_setting('request.headers', true), '{}')::jsonb ->> 'x-nexora-machine-id', ''));
  update nexora_private.hands_worker_auth
     set last_seen_at = now(),
         machine_identity = coalesce(machine_identity, v_machine)
   where enabled = true
     and worker_id = p_worker_id
     and token_sha256 = p_channel_token
     and v_machine is not null
     and (machine_identity is null or upper(machine_identity) = v_machine);
  return found;
end;
$$;
