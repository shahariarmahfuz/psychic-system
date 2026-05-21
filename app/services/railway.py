import json
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import RailwayAccount, Slot
from ..security import decrypt_secret, encrypt_secret
from .audit import log

settings = get_settings()


class RailwayError(RuntimeError):
    pass


def _run(cmd: list[str], token: str, cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop('RAILWAY_TOKEN', None)
    env['RAILWAY_API_TOKEN'] = token
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def railway_cmd(args: list[str], token: str, cwd: Path | None = None, timeout: int = 600) -> str:
    binary = settings.railway_cli_path
    if binary == 'railway':
        binary = shutil.which('railway') or 'railway'
    proc = _run([binary, *args], token=token, cwd=cwd, timeout=timeout)
    if proc.returncode != 0:
        raise RailwayError(proc.stdout[-3000:])
    return proc.stdout


def random_frp_token() -> str:
    return secrets.token_hex(32)


def write_slot_files(slot_dir: Path, frp_token: str) -> None:
    slot_dir.mkdir(parents=True, exist_ok=True)

    (slot_dir / 'Dockerfile').write_text(f'''FROM alpine:latest

RUN apk add --no-cache sslh wget tar ca-certificates

ENV FRP_VERSION={settings.frp_version}

RUN wget https://github.com/fatedier/frp/releases/download/v${{FRP_VERSION}}/frp_${{FRP_VERSION}}_linux_amd64.tar.gz && \\
    tar xzf frp_${{FRP_VERSION}}_linux_amd64.tar.gz && \\
    mv frp_${{FRP_VERSION}}_linux_amd64 /frp && \\
    rm frp_${{FRP_VERSION}}_linux_amd64.tar.gz

COPY frps.toml /frp/frps.toml
COPY start.sh /start.sh

RUN chmod +x /start.sh

CMD ["/start.sh"]
''')

    (slot_dir / 'frps.toml').write_text(f'''bindAddr = "127.0.0.1"
bindPort = 7000

proxyBindAddr = "127.0.0.1"

auth.method = "token"
auth.token = "{frp_token}"

[transport]
heartbeatTimeout = 90
tcpMux = true
tcpMuxKeepaliveInterval = 30
tcpKeepalive = 7200
tls.force = true

[[allowPorts]]
single = 6000
''')

    (slot_dir / 'start.sh').write_text('''#!/bin/sh
set -eu

cleanup() {
    kill -TERM "$SSLH_PID" "$FRPS_PID" 2>/dev/null || true
    wait "$SSLH_PID" 2>/dev/null || true
    wait "$FRPS_PID" 2>/dev/null || true
}

term() {
    cleanup
    exit 0
}

trap term INT TERM
trap cleanup EXIT

/frp/frps -c /frp/frps.toml &
FRPS_PID=$!

if sslh -h 2>&1 | grep -q -- '--tls'; then
    TLS_OPT="--tls"
else
    TLS_OPT="--ssl"
fi

sslh -f -u root \
  -p "0.0.0.0:${PORT:-8080}" \
  --ssh "127.0.0.1:6000" \
  "$TLS_OPT" "127.0.0.1:7000" \
  --on-timeout ssh \
  --timeout 2 &
SSLH_PID=$!

while kill -0 "$FRPS_PID" 2>/dev/null && kill -0 "$SSLH_PID" 2>/dev/null; do
    sleep 2
done

exit 1
''')
    os.chmod(slot_dir / 'start.sh', 0o755)
    (slot_dir / '.railwayignore').write_text('.git\nnode_modules\n*.log\n')


def extract_project_id(output: str) -> str | None:
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            for key in ('projectId', 'project_id', 'id'):
                if data.get(key):
                    return str(data[key])
    except Exception:
        pass
    # fallback for accidental URL/id text
    m = re.search(r'project[/=]([0-9a-fA-F-]{16,})', output)
    return m.group(1) if m else None


def collect_tcp_link(account: RailwayAccount, slot: Slot) -> tuple[str | None, int | None, str | None]:
    token = decrypt_secret(account.api_token_cipher)
    args = ['run', '-s', slot.service_name]
    if slot.project_id:
        args += ['-p', slot.project_id]
    args += ['sh', '-c', 'printf "%s:%s" "$RAILWAY_TCP_PROXY_DOMAIN" "$RAILWAY_TCP_PROXY_PORT"']
    out = railway_cmd(args, token=token, timeout=120).strip()
    m = re.match(r'^([A-Za-z0-9.-]+):([0-9]+)$', out)
    if not m:
        return None, None, out[-500:]
    return m.group(1), int(m.group(2)), None


def create_slot(db: Session, account: RailwayAccount, project_prefix: str = 'nekotunnel') -> Slot:
    token = decrypt_secret(account.api_token_cipher)
    stamp = int(time.time())
    project_name = f'{project_prefix}-{account.label.lower().replace(" ", "-")}-{stamp}'[:120]
    service_name = settings.service_name
    frp_token = random_frp_token()
    slot_dir = Path(settings.slot_work_root) / project_name

    write_slot_files(slot_dir, frp_token)

    slot = Slot(
        railway_account_id=account.id,
        project_name=project_name,
        service_name=service_name,
        frp_token_cipher=encrypt_secret(frp_token),
        status='deploying',
    )
    db.add(slot)
    db.flush()
    log(db, 'slot_create_started', 'admin', f'project={project_name}')
    db.commit()

    try:
        init_out = railway_cmd(['init', '-n', project_name, '-w', account.workspace, '--json'], token=token, cwd=slot_dir)
        slot.project_id = extract_project_id(init_out)
        try:
            railway_cmd(['add', '-s', service_name], token=token, cwd=slot_dir, timeout=180)
        except RailwayError as exc:
            # In some CLI versions init creates a linked service. Keep going if it already exists.
            if 'already' not in str(exc).lower():
                raise
        railway_cmd(['up', '-s', service_name, '--detach'], token=token, cwd=slot_dir, timeout=900)
        time.sleep(10)
        try:
            addr, port, err = collect_tcp_link(account, slot)
            if addr and port:
                slot.server_addr = addr
                slot.server_port = port
                slot.status = 'free'
                slot.last_error = None
            else:
                slot.status = 'tcp_pending'
                slot.last_error = err or f'TCP proxy missing. Enable internal port {settings.tcp_internal_port}.'
        except Exception as exc:
            slot.status = 'tcp_pending'
            slot.last_error = f'Deployed, but TCP link not found yet: {exc}'
        log(db, 'slot_create_finished', 'admin', f'project={project_name} status={slot.status}')
        db.add(slot)
        db.commit()
        return slot
    except Exception as exc:
        slot.status = 'failed'
        slot.last_error = str(exc)[-3000:]
        db.add(slot)
        log(db, 'slot_create_failed', 'admin', f'project={project_name} error={slot.last_error}')
        db.commit()
        return slot


def refresh_slot_tcp(db: Session, slot: Slot) -> Slot:
    if not slot.account:
        slot.last_error = 'No Railway account attached.'
        db.add(slot)
        db.commit()
        return slot
    try:
        addr, port, err = collect_tcp_link(slot.account, slot)
        if addr and port:
            slot.server_addr = addr
            slot.server_port = port
            if slot.status in ('tcp_pending', 'offline', 'failed'):
                slot.status = 'free'
            slot.last_error = None
            log(db, 'slot_tcp_refreshed', 'admin', f'{slot.project_name} => {addr}:{port}')
        else:
            slot.last_error = err or f'TCP proxy missing. Enable internal port {settings.tcp_internal_port}.'
            log(db, 'slot_tcp_missing', 'admin', slot.project_name)
    except Exception as exc:
        slot.last_error = str(exc)[-3000:]
    db.add(slot)
    db.commit()
    return slot
