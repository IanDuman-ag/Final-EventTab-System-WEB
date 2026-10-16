FROM postgres:18
# Test-only Python; no package/dependency changes to the deployed backend.
RUN apt-get update && apt-get install -y --no-install-recommends python3
COPY execution/vocal_duet_simple_owner.py execution/test_vocal_duet_owner_linux.py /tests/execution/
COPY events/vocal_duet_owner_evidence.py /tests/events/
ENTRYPOINT ["/bin/bash", "-c"]
CMD ["set -eu; install -d -o postgres -g postgres /tmp/owner-pg; runuser -u postgres -- initdb -D /tmp/owner-pg --auth=trust >/tmp/init.log; runuser -u postgres -- pg_ctl -D /tmp/owner-pg -l /tmp/owner-pg.log -o '-h 127.0.0.1 -p 55449' -w start; trap 'runuser -u postgres -- pg_ctl -D /tmp/owner-pg -m fast -w stop' EXIT; runuser -u postgres -- createdb -h 127.0.0.1 -p 55449 eventtab_owner_evidence_test; python3 /tests/execution/test_vocal_duet_owner_linux.py"]
