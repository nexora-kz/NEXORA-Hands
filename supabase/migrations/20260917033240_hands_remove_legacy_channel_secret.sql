drop policy if exists hands_commands_insert on public.hands_commands;
drop policy if exists hands_commands_select on public.hands_commands;
drop policy if exists hands_commands_update on public.hands_commands;
drop policy if exists hands_results_insert on public.hands_results;
drop policy if exists hands_results_select on public.hands_results;

alter table public.hands_commands alter column channel_token drop default;
alter table public.hands_results alter column channel_token drop default;

create policy hands_commands_select on public.hands_commands
for select to anon, authenticated
using (channel_token = encode(extensions.digest(coalesce((coalesce(current_setting('request.headers', true),'{}')::jsonb ->> 'x-nexora-hands-token'),''),'sha256'),'hex'));

create policy hands_commands_update on public.hands_commands
for update to anon, authenticated
using (channel_token = encode(extensions.digest(coalesce((coalesce(current_setting('request.headers', true),'{}')::jsonb ->> 'x-nexora-hands-token'),''),'sha256'),'hex'))
with check (channel_token = encode(extensions.digest(coalesce((coalesce(current_setting('request.headers', true),'{}')::jsonb ->> 'x-nexora-hands-token'),''),'sha256'),'hex'));

create policy hands_results_insert on public.hands_results
for insert to anon, authenticated
with check (channel_token = encode(extensions.digest(coalesce((coalesce(current_setting('request.headers', true),'{}')::jsonb ->> 'x-nexora-hands-token'),''),'sha256'),'hex'));

create policy hands_results_select on public.hands_results
for select to anon, authenticated
using (channel_token = encode(extensions.digest(coalesce((coalesce(current_setting('request.headers', true),'{}')::jsonb ->> 'x-nexora-hands-token'),''),'sha256'),'hex'));

revoke insert on public.hands_commands from anon, authenticated;
;
