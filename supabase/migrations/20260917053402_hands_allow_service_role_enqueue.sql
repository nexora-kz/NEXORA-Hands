create or replace function public.nexora_transport_worker_authorized()
returns boolean
language sql
stable
security definer
set search_path to 'pg_catalog', 'extensions', 'nexora_private'
as $function$
  select current_user = 'service_role'
      or exists (
        select 1
        from nexora_private.transport_worker_auth a
        where a.enabled
          and a.node_id = 'nexora-main-pc'
          and a.token_sha256 = encode(
            extensions.digest(
              coalesce((coalesce(current_setting('request.headers', true), '{}')::jsonb ->> 'x-nexora-worker-token'), ''),
              'sha256'
            ),
            'hex'
          )
      );
$function$;;
