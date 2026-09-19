create or replace function public.hands_worker_heartbeat(p_worker_id text, p_channel_token text)
returns boolean
language plpgsql
security definer
set search_path = public, nexora_private, extensions
as $$
begin
  update nexora_private.hands_worker_auth
     set last_seen_at = now()
   where enabled = true
     and worker_id = p_worker_id
     and token_sha256 = p_channel_token;
  return found;
end;
$$;
revoke all on function public.hands_worker_heartbeat(text,text) from public;
grant execute on function public.hands_worker_heartbeat(text,text) to anon, authenticated, service_role;;
