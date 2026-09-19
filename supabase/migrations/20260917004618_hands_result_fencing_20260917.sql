alter table hands_results add column if not exists lease_token text;
create unique index if not exists hands_results_task_id_unique on hands_results(task_id);;
