"""El proyecto de Docker Compose del directorio actual: arrancar, vigilar y diagnosticar servicios."""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from bitds_connector import ui


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["docker", "compose", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        detalle = (r.stderr or r.stdout or "").strip()
        ui.fail(f"`docker compose {' '.join(args[:2])}` failed: {detalle}")
    return r


def _inspeccionar(servicio: str, formato: str) -> str | None:
    ids = compose("ps", "--all", "--quiet", servicio, check=False).stdout.split()
    if not ids:
        return None
    r = subprocess.run(["docker", "inspect", "-f", formato, ids[0]], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def estado(servicio: str) -> str | None:
    return _inspeccionar(servicio, "{{.State.Status}}")


def abortar_con_logs(motivo: str, servicio: str) -> None:
    logs = compose("logs", "--tail", "20", servicio, check=False).stdout
    ultimas = [linea for linea in logs.splitlines() if linea.strip()][-10:] or ["(no logs)"]
    ui.fail(f"{motivo}\nLast log lines of {servicio}:\n  " + "\n  ".join(ultimas))


def vigilar(servicio: str) -> str:
    """Devuelve el estado del servicio, y aborta si se ha caido o reiniciado.

    Se mira RestartCount porque, con restart: unless-stopped, un contenedor que
    muere y revive pasa por 'running' entre un reinicio y otro.
    """
    datos = _inspeccionar(servicio, "{{.State.Status}} {{.RestartCount}}")
    if not datos:
        return "no-container"
    actual, reinicios = datos.split()
    if int(reinicios) > 0:
        abortar_con_logs(f"{servicio} started and crashed ({reinicios} restart(s)): "
                         "this is almost always a configuration error.", servicio)
    if actual == "exited":
        abortar_con_logs(f"{servicio} stopped right after starting.", servicio)
    return actual


def recrear_si_desfasado(servicio: str, config: str) -> None:
    """Recrea el servicio si arranco antes del ultimo cambio de su configuracion.

    La configuracion va como secret de compose, y compose no se entera de que
    cambie el contenido de ese fichero.
    """
    inicio = _inspeccionar(servicio, "{{.State.StartedAt}}")
    if not inicio:
        return
    # Al segundo: fromisoformat de Python 3.10 no admite la Z ni los nanosegundos.
    arranque = datetime.fromisoformat(inicio[:19]).replace(tzinfo=timezone.utc)
    if int(arranque.timestamp()) < int(Path(config).stat().st_mtime):
        ui.step(f"Configuration changed, recreating {servicio}...")
        compose("up", "-d", "--no-deps", "--force-recreate", servicio)
