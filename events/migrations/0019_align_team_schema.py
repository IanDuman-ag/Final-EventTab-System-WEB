# Generated manually to align legacy events_team schema with Team model.

from django.db import migrations


FORWARD_SQL = """
ALTER TABLE events_team RENAME COLUMN abbreviation TO code;
ALTER TABLE events_team ADD COLUMN IF NOT EXISTS department_id bigint NULL;
ALTER TABLE events_team ADD COLUMN IF NOT EXISTS members text NOT NULL DEFAULT '';
ALTER TABLE events_team ADD COLUMN IF NOT EXISTS coach varchar(200) NOT NULL DEFAULT '';
ALTER TABLE events_team ADD COLUMN IF NOT EXISTS status varchar(20) NOT NULL DEFAULT 'active';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'events_team_department_id_fkey'
    ) THEN
        ALTER TABLE events_team
            ADD CONSTRAINT events_team_department_id_fkey
            FOREIGN KEY (department_id)
            REFERENCES events_department(id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

ALTER TABLE events_team DROP COLUMN IF EXISTS logo_icon;
ALTER TABLE events_team DROP COLUMN IF EXISTS color;
ALTER TABLE events_team DROP COLUMN IF EXISTS description;

CREATE UNIQUE INDEX IF NOT EXISTS events_team_code_uniq ON events_team(code);
"""

REVERSE_SQL = """
DROP INDEX IF EXISTS events_team_code_uniq;
ALTER TABLE events_team DROP CONSTRAINT IF EXISTS events_team_department_id_fkey;
ALTER TABLE events_team DROP COLUMN IF EXISTS department_id;
ALTER TABLE events_team DROP COLUMN IF EXISTS members;
ALTER TABLE events_team DROP COLUMN IF EXISTS coach;
ALTER TABLE events_team DROP COLUMN IF EXISTS status;
ALTER TABLE events_team ADD COLUMN IF NOT EXISTS logo_icon varchar(50) NOT NULL DEFAULT 'A';
ALTER TABLE events_team ADD COLUMN IF NOT EXISTS color varchar(20) NOT NULL DEFAULT '#00C5D9';
ALTER TABLE events_team ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '';
ALTER TABLE events_team RENAME COLUMN code TO abbreviation;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0018_team_model'),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
