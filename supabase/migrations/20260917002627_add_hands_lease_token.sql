alter table public.hands_commands add column if not exists lease_token text;
create unique index if not exists hands_commands_lease_token_unique on public.hands_commands (lease_token) where lease_token is not null;;
