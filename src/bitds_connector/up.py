#!/usr/bin/env python3
"""Paso 2: levanta el conector y comprueba que ha arrancado de verdad.

Se ejecuta en el directorio que preparo generate.py:

    python3 up.py

Hace lo que `docker compose up -d` no hace por si solo:

  - traduce el error de autenticacion del registry;
  - espera a que el conector este realmente corriendo, porque `up -d` vuelve en
    cuanto Docker acepta el proyecto, no cuando el conector arranca;
  - si no arranca, ensena las ultimas lineas del log en vez de dejarte a ciegas;
  - imprime los datos del conector desplegado (identidad, puertos, API key,
    claims y clave publica), que es lo que hay que enviar para registrarlo.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

from bitds_connector import info, ui

COMPOSE_FILE = "docker-compose.yml"
PUBLIC_KEY = info.PUBLIC_KEY


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Ejecuta `docker compose ...` en el directorio actual.

    Compose coge solo el docker-compose.yml de aqui, y de el el nombre del
    proyecto, asi que no hace falta pasarle ni -f ni -p.
    """
    r = subprocess.run(["docker", "compose", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        detalle = (r.stderr or r.stdout or "").strip()
        ui.fail(f"`docker compose {' '.join(args[:2])}` failed: {detalle}")
    return r


def comprobar_entorno() -> None:
    if not Path(COMPOSE_FILE).exists():
        ui.fail(f"No {COMPOSE_FILE} in this directory.\n"
                "Run this first: connector generate --participant-id ... --host ...")
    r = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        ui.fail("Cannot talk to Docker. Check that the daemon is running and that\n"
                "your user belongs to the 'docker' group.")


def imagenes_ausentes() -> list[str] | None:
    """Imagenes del compose que NO estan en local. None si no se puede averiguar."""
    conf = compose("config", "--images", check=False)
    if conf.returncode != 0:
        return None
    ausentes = []
    for imagen in (l.strip() for l in conf.stdout.splitlines() if l.strip()):
        existe = subprocess.run(["docker", "image", "inspect", imagen],
                                capture_output=True, text=True)
        if existe.returncode != 0:
            ausentes.append(imagen)
    return ausentes


def descargar_imagenes() -> None:
    """Intenta actualizar las imagenes; si el registry no responde, tira de las locales.

    Un registry inalcanzable no tiene por que impedir el despliegue: si las
    imagenes ya estan descargadas, el conector arranca igual. Solo es fatal
    cuando ademas falta alguna.
    """
    r = compose("pull", check=False)
    if r.returncode == 0:
        return

    err = ((r.stderr or "") + (r.stdout or "")).strip()
    ausentes = imagenes_ausentes()

    if ausentes is None:
        ui.fail(f"Failed to pull the images: {err[:300]}")
    if ausentes:
        ui.fail("Failed to pull the images, and these are not available locally:\n"
                + "\n".join(f"    {i}" for i in ausentes)
                + f"\n{err[:300]}")

    ui.warn("Could not reach the registry; using the images already present locally.")


def _estado_provider() -> tuple[str, int]:
    """Devuelve (estado, numero_de_reinicios) del contenedor del conector.

    Se consulta con `docker inspect` en vez de `compose ps --status running`
    porque, con restart: unless-stopped, un contenedor que muere y revive pasa
    por 'running' entre reinicio y reinicio: mirarlo en un solo instante da
    falsos positivos. RestartCount no miente.
    """
    cid = compose("ps", "--all", "--quiet", "provider", check=False).stdout.strip()
    if not cid:
        return "no-container", 0
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}} {{.RestartCount}}", cid.splitlines()[0]],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return "no-container", 0
    estado, _, reinicios = r.stdout.strip().partition(" ")
    return estado, int(reinicios or 0)


def _fallo(motivo: str) -> None:
    """Aborta ensenando el final del log, que es lo unico que permite diagnosticar."""
    logs = compose("logs", "--tail", "20", "provider", check=False)
    ultimas = [ln for ln in logs.stdout.splitlines() if ln.strip()][-10:]
    ui.fail(f"{motivo}\nLast log lines:\n  " + "\n  ".join(ultimas or ["(no logs)"]))


def esperar_a_running(timeout: int, intervalo: int = 5, sondeos_estables: int = 2) -> None:
    """Espera a que el conector este arriba y SIGA arriba.

    No basta con verlo 'running' una vez: un conector con la configuracion mal
    arranca y muere a los pocos segundos. Se exige verlo estable en varios
    sondeos seguidos, y cualquier reinicio se considera fallo inmediato.
    """
    estables = 0
    transcurrido = 0
    while True:
        estado, reinicios = _estado_provider()

        if reinicios > 0:
            _fallo(f"The connector started and crashed ({reinicios} restart(s)): "
                   "this is almost always a configuration error.")
        if estado == "exited":
            _fallo("The connector stopped right after starting.")

        if estado == "running":
            estables += 1
            if estables >= sondeos_estables:
                return
        else:
            estables = 0

        if transcurrido >= timeout:
            _fallo(f"The connector did not start within {timeout}s (state: {estado}).")
        time.sleep(intervalo)
        transcurrido += intervalo


def esperar_clave_publica(timeout: int = 60, intervalo: int = 3) -> str | None:
    """key-init escribe los PEM en este directorio (lo monta como /identity)."""
    pem = Path(PUBLIC_KEY)
    transcurrido = 0
    while True:
        if pem.exists():
            # El registro espera solo el base64, sin las lineas BEGIN/END.
            return "".join(l for l in pem.read_text().splitlines() if not l.startswith("-----"))
        if transcurrido >= timeout:
            return None
        time.sleep(intervalo)
        transcurrido += intervalo


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--timeout", type=int, default=120,
                   help="Seconds to wait for the connector to start (default 120).")


def run(args: argparse.Namespace) -> None:

    comprobar_entorno()

    ui.step("Pulling images...")
    descargar_imagenes()

    ui.step("Starting the stack...")
    compose("up", "-d")

    ui.step("Waiting for the connector to start...")
    esperar_a_running(args.timeout)
    ui.ok("Connector is up.")

    clave = esperar_clave_publica()
    if not clave:
        ui.warn(f"{PUBLIC_KEY} not generated yet. Try again with: connector info")
    info.resumen(clave)
