"""Disposable PostgreSQL cluster, always created here, never an existing server."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time


def postgres_bin():
    candidates = []
    if os.environ.get('EVENTTAB_TEST_POSTGRES_BIN'):
        candidates.append(Path(os.environ['EVENTTAB_TEST_POSTGRES_BIN']))
    if os.name == 'nt':
        candidates.append(Path(r'C:\Program Files\PostgreSQL\18\bin'))
    else:
        candidates.append(Path('/usr/lib/postgresql/18/bin'))
    found = shutil.which('initdb')
    if found:
        candidates.append(Path(found).parent)
    extension = '.exe' if os.name == 'nt' else ''
    for candidate in candidates:
        if all((candidate / (name + extension)).is_file() for name in ('initdb', 'postgres', 'pg_ctl', 'psql')):
            return candidate, extension
    raise RuntimeError('PostgreSQL binaries not found. Set EVENTTAB_TEST_POSTGRES_BIN to the local PostgreSQL bin directory; existing servers are never used.')


def cluster_ready(process, data, port):
    if process.poll() is not None:
        raise RuntimeError('Isolated PostgreSQL exited; inspect its postgres.log.')
    pid_file = data / 'postmaster.pid'
    lines = pid_file.read_text().splitlines() if pid_file.exists() else []
    if lines and lines[0].strip() != str(process.pid):
        raise RuntimeError('Isolated PostgreSQL process identity mismatch; no connection was attempted.')
    if len(lines) < 8 or lines[7].strip() != 'ready':
        return False
    if Path(lines[1]).resolve() != data.resolve() or lines[3].strip() != str(port) or port in {5432, 5433}:
        raise RuntimeError('Isolated PostgreSQL path/port mismatch; no connection was attempted.')
    return True


@contextmanager
def isolated_postgres(parent):
    binary, extension = postgres_bin()
    parent = Path(parent).resolve(); parent.mkdir(parents=True, exist_ok=True)
    # Keep diagnostics on failure. Cluster files are local intermediates, never packaged.
    root = Path(tempfile.mkdtemp(prefix='pg-tests-', dir=parent))
    data = root / 'data'; log = root / 'postgres.log'
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    if port in {5432, 5433}:
        raise RuntimeError('Refusing a development/production database port.')
    environment = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    environment['PGCONNECT_TIMEOUT'] = '10'
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    def run(name, *args):
        result = subprocess.run([str(binary / (name + extension)), *map(str, args)],
            cwd=root, env=environment, capture_output=True, text=True, timeout=90, creationflags=flags)
        with (root / 'cluster-setup.log').open('a', encoding='utf-8') as stream:
            stream.write(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError('Isolated PostgreSQL ' + name + ' failed; inspect ' + str(root / 'cluster-setup.log'))
        return result.stdout

    run('initdb', '-D', 'data', '--username=eventtab_tests', '--auth=trust', '--encoding=UTF8', '--locale=C')
    # Trust is restricted to loopback in this new, throwaway cluster. No secrets,
    # production role, external host, service or pre-existing data directory.
    (data / 'pg_hba.conf').write_text(
        'host all eventtab_tests 127.0.0.1/32 trust\nlocal all eventtab_tests trust\n', encoding='ascii')
    process = None
    try:
        # Direct child avoids pg_ctl's Windows restricted-token launcher and
        # gives us an owned process handle. Nothing is installed as a service.
        with log.open('w', encoding='utf-8') as stream:
            process = subprocess.Popen([str(binary / ('postgres' + extension)), '-D', 'data',
                '-h', '127.0.0.1', '-p', str(port), '-c', 'shared_buffers=16MB',
                '-c', 'max_connections=30', '-c', "unix_socket_directories="],
                cwd=root, env=environment, stdout=stream, stderr=stream, creationflags=flags)
        # A port race cannot silently attach tests to another server: verify the
        # actual data directory before allowing Django any database connection.
        deadline = time.monotonic() + 30
        while True:
            if not cluster_ready(process, data, port):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Isolated PostgreSQL did not become ready; inspect ' + str(log))
                time.sleep(0.25)
                continue
            try:
                actual = run('psql', '-X', '-h', '127.0.0.1', '-p', port, '-U', 'eventtab_tests',
                             '-d', 'postgres', '-At', '-c', 'SHOW data_directory').strip()
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)
        if Path(actual).resolve() != data.resolve():
            raise RuntimeError('Isolated PostgreSQL identity mismatch; no tests were run.')
        settings = {'ENGINE': 'django.db.backends.postgresql', 'HOST': '127.0.0.1',
            'PORT': str(port), 'USER': 'eventtab_tests', 'PASSWORD': '', 'NAME': 'postgres',
            'TEST': {'NAME': 'test_eventtab_isolated'}}
        (root / 'identity.json').write_text(json.dumps({'data': str(data), 'port': port,
            'role': settings['USER'], 'test_database': settings['TEST']['NAME']}, indent=2), encoding='utf-8')
        print(f'Isolated PostgreSQL verified: 127.0.0.1:{port}; test_eventtab_isolated; {data}')
        yield settings
    finally:
        if process is not None and process.poll() is None:
            try:
                run('pg_ctl', '-D', 'data', '-w', '-t', '30', '-m', 'fast', 'stop')
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
