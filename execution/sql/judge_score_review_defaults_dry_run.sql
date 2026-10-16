\set ON_ERROR_STOP on
BEGIN;
SET LOCAL eventtab.review_defaults_mode = 'apply';
\ir judge_score_review_defaults_body.sql
ROLLBACK;
