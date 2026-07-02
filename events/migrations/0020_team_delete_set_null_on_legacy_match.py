# Generated manually to allow Team deletion when legacy events_match rows reference teams.

from django.db import migrations


FORWARD_SQL = """
ALTER TABLE events_match DROP CONSTRAINT IF EXISTS events_match_team_a_id_d6c91033_fk_events_team_id;
ALTER TABLE events_match DROP CONSTRAINT IF EXISTS events_match_team_b_id_b9064376_fk_events_team_id;

ALTER TABLE events_match ALTER COLUMN team_a_id DROP NOT NULL;
ALTER TABLE events_match ALTER COLUMN team_b_id DROP NOT NULL;

ALTER TABLE events_match
    ADD CONSTRAINT events_match_team_a_id_fk_events_team_id
    FOREIGN KEY (team_a_id) REFERENCES events_team(id)
    ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE events_match
    ADD CONSTRAINT events_match_team_b_id_fk_events_team_id
    FOREIGN KEY (team_b_id) REFERENCES events_team(id)
    ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;
"""

REVERSE_SQL = """
ALTER TABLE events_match DROP CONSTRAINT IF EXISTS events_match_team_a_id_fk_events_team_id;
ALTER TABLE events_match DROP CONSTRAINT IF EXISTS events_match_team_b_id_fk_events_team_id;

ALTER TABLE events_match
    ADD CONSTRAINT events_match_team_a_id_d6c91033_fk_events_team_id
    FOREIGN KEY (team_a_id) REFERENCES events_team(id)
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE events_match
    ADD CONSTRAINT events_match_team_b_id_b9064376_fk_events_team_id
    FOREIGN KEY (team_b_id) REFERENCES events_team(id)
    ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0019_align_team_schema'),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
