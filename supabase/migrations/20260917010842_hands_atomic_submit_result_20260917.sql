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
security invoker
set search_path = public
as $$
declare
    v_status text;
    v_updated integer;
begin
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
    if v_updated <> 1 then
        return false;
    end if;

    insert into public.hands_results(task_id, result, worker_id, channel_token, lease_token)
    values (p_task_id, p_result, p_worker_id, p_channel_token, p_lease_token);

    return true;
end;
$$;

revoke all on function public.hands_submit_result_atomic(text, text, text, text, jsonb, text) from public;
grant execute on function public.hands_submit_result_atomic(text, text, text, text, jsonb, text) to anon, authenticated;;
