"""Standalone recovery worker; intentionally runs outside the web service cgroup."""
import os
import time


def main():
    if os.name != 'posix':
        raise RuntimeError('The production recovery worker requires Linux and systemd.')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
    import django
    django.setup()
    import fcntl
    from pathlib import Path
    from django.db import connections
    from events.database_recovery import read_json, root, run_recovery, set_state, write_json
    deploy_lock = Path('/srv/eventtab-deploy/deploy.lock')
    if not deploy_lock.is_file() or deploy_lock.is_symlink():
        raise RuntimeError('The deployment interlock must be available before enabling recovery.')
    with (root() / 'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('A recovery worker is already running.') from exc
        for path in root().glob('*.json'):
            job = read_json(path)
            if job and job.get('state') == 'running':
                set_state(job, 'needs_attention', 'The recovery worker was interrupted. Contact your system administrator before resuming.')
        try:
            while True:
                write_json(root() / 'worker.json', {'time': time.time(), 'pid': os.getpid()})
                for path in sorted(root().glob('*.json')):
                    job = read_json(path)
                    if job and job.get('state') == 'queued':
                        with deploy_lock.open('a') as deployment:
                            try:
                                fcntl.flock(deployment, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            except BlockingIOError:
                                continue
                            try:
                                run_recovery(job)
                            finally:
                                fcntl.flock(deployment, fcntl.LOCK_UN)
                connections.close_all()
                time.sleep(3)
        finally:
            (root() / 'worker.json').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
