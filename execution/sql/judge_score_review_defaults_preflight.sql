\set ON_ERROR_STOP on
BEGIN TRANSACTION READ ONLY;
SELECT current_database(), current_user, inet_server_addr(), inet_server_port(), current_schema();
SELECT column_name, data_type, character_maximum_length, is_nullable, column_default
FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'events_judgescore'
ORDER BY ordinal_position;
SELECT conname, contype, convalidated, pg_get_constraintdef(oid)
FROM pg_constraint WHERE conrelid = 'public.events_judgescore'::regclass;
SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger
WHERE tgrelid = 'public.events_judgescore'::regclass AND NOT tgisinternal;
SELECT count(*) AS orphan_judge_references FROM public.events_judgescore s
LEFT JOIN public.auth_user u ON u.id = s.judge_id WHERE u.id IS NULL;
SELECT s.judge_id, count(*) AS score_count,
       count(s.submitted_at) AS submitted_count, count(*) FILTER (WHERE s.is_locked) AS locked_count
FROM public.events_judgescore s JOIN public.events_candidate c ON c.id = s.candidate_id
WHERE c.event_id = 63 AND s.judge_id IN (16, 21, 22) GROUP BY s.judge_id;
COMMIT;
