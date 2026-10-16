-- Shared guarded repair. Execute only through one of the transaction wrappers.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
LOCK TABLE public.events_judgescore IN ACCESS EXCLUSIVE MODE;
DO $repair$
DECLARE
    repair_mode text := current_setting('eventtab.review_defaults_mode');
    column_name text;
    desired text;
    definition record;
    old_count bigint;
    new_count bigint;
    old_digest text;
    new_digest text;
BEGIN
    IF repair_mode NOT IN ('apply', 'rollback') THEN
        RAISE EXCEPTION 'Unsupported repair mode';
    END IF;
    IF to_regclass('events_judgescore') IS DISTINCT FROM 'public.events_judgescore'::regclass
       OR (SELECT relkind FROM pg_class WHERE oid = 'public.events_judgescore'::regclass) <> 'r' THEN
        RAISE EXCEPTION 'Unexpected table or search path; review schema manually';
    END IF;
    FOREACH column_name IN ARRAY ARRAY['review_note', 'approval_status'] LOOP
        desired := CASE column_name WHEN 'review_note' THEN '' ELSE 'pending' END;
        SELECT a.attnum, a.atttypid, a.atttypmod, a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid) AS default_expression
        INTO definition
        FROM pg_attribute a LEFT JOIN pg_attrdef d
          ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = 'public.events_judgescore'::regclass
          AND a.attname = column_name AND NOT a.attisdropped;
        IF NOT FOUND OR NOT definition.attnotnull
           OR definition.atttypid NOT IN ('text'::regtype, 'varchar'::regtype)
           OR (definition.atttypmod <> -1 AND definition.atttypmod - 4 < length(desired)) THEN
            RAISE EXCEPTION 'Unexpected definition for %; review manually', column_name;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.events_judgescore'::regclass
                   AND contype = 'c' AND definition.attnum = ANY(conkey)) THEN
            RAISE EXCEPTION 'Check constraint involves %; review permitted defaults manually', column_name;
        END IF;
        IF definition.default_expression IS NOT NULL AND definition.default_expression NOT IN
             (quote_literal(desired) || '::text', quote_literal(desired) || '::character varying') THEN
            RAISE EXCEPTION 'Unexpected existing default for %; refusing replacement', column_name;
        END IF;
        IF repair_mode = 'rollback' AND definition.default_expression IS NULL THEN
            RAISE EXCEPTION 'Repair default absent for %; refusing rollback', column_name;
        END IF;
    END LOOP;
    SELECT count(*), md5(coalesce(string_agg(md5(to_jsonb(s)::text), '' ORDER BY id), ''))
      INTO old_count, old_digest FROM public.events_judgescore s;
    IF repair_mode = 'apply' THEN
        ALTER TABLE public.events_judgescore ALTER COLUMN review_note SET DEFAULT '';
        ALTER TABLE public.events_judgescore ALTER COLUMN approval_status SET DEFAULT 'pending';
    ELSE
        ALTER TABLE public.events_judgescore ALTER COLUMN review_note DROP DEFAULT;
        ALTER TABLE public.events_judgescore ALTER COLUMN approval_status DROP DEFAULT;
    END IF;
    SELECT count(*), md5(coalesce(string_agg(md5(to_jsonb(s)::text), '' ORDER BY id), ''))
      INTO new_count, new_digest FROM public.events_judgescore s;
    IF old_count <> new_count OR old_digest <> new_digest THEN
        RAISE EXCEPTION 'Row preservation check failed; rolling back';
    END IF;
    RAISE NOTICE 'Defaults %; all % existing rows unchanged', repair_mode, old_count;
END
$repair$;
