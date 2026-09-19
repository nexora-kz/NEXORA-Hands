drop policy if exists hands_commands_select on public.hands_commands;
drop policy if exists hands_commands_update on public.hands_commands;
drop policy if exists hands_results_insert on public.hands_results;
drop policy if exists hands_results_select on public.hands_results;

create policy hands_commands_select on public.hands_commands
for select to anon, authenticated
using (
  channel_token = encode(extensions.digest('NEXORA_HANDS_LOCAL_CHANNEL_20260917','sha256'),'hex')
  or channel_token = encode(extensions.digest(coalesce((coalesce(current_setting('request.headers', true),'{}')::jsonb ->> 'x-nexora-hands-token'),''),'sha256'),'hex')
);

create policy hands_commands_update on public.hands_commands
for update to anon, authenticated
using (
  channel_token = encode(extensions.digest('NEXORA_HANDS_LOCAL_CHANNEL_20260917','sha256'),'hex')
  or channel_token = encode(extensions.digest(coalesce((coalesce(current_setting('request.headers', true),'{}')::jsonb ->> 'x-nexora-hands-token'),''),'sha256'),'hex')
)
with check (
  channel_token = encode(extensions.digest('NEXORA_HANDS_LOCAL_CHANNEL_20260917','sha256'),'hex')
  or channel_token = encode(extensions.digest(coalesce((coalesce(current_setting('request.headers', true),'{}')::jsonb ->> 'x-nexora-hands-token'),''),'sha256'),'hex')
);

create policy hands_results_insert on public.hands_results
for insert to anon, authenticated
with check (
  channel_token = encode(extensions.digest('NEXORA_HANDS_LOCAL_CHANNEL_20260917','sha256'),'hex')
  or channel_token = encode(extensions.digest(coalesce((coalesce(current_setting('request.headers', true),'{}')::jsonb ->> 'x-nexora-hands-token'),''),'sha256'),'hex')
);

create policy hands_results_select on public.hands_results
for select to anon, authenticated
using (
  channel_token = encode(extensions.digest('NEXORA_HANDS_LOCAL_CHANNEL_20260917','sha256'),'hex')
  or channel_token = encode(extensions.digest(coalesce((coalesce(current_setting('request.headers', true),'{}')::jsonb ->> 'x-nexora-hands-token'),''),'sha256'),'hex')
);
;
