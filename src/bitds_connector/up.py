"""Paso 2: levanta el stack, da de alta al participante y espera a que el conector arranque.

El orden importa: el conector necesita el secret del STS, y el IdentityHub solo
lo entrega al dar de alta al participante.
"""

import argparse
import subprocess
import time
from pathlib import Path

from bitds_connector import identityhub, info, stack, ui
from bitds_connector.stack import compose

COMPOSE_FILE = "docker-compose.yml"


def comprobar_entorno() -> None:
    for fichero in (COMPOSE_FILE, info.PROPS_FILE, info.IH_PROPS_FILE):
        if not Path(fichero).exists():
            ui.fail(f"No {fichero} in this directory.\n"
                    "Run this first: connector generate --participant-id ... --host ...")
    docker = subprocess.run(["docker", "version"], capture_output=True, text=True)
    if docker.returncode != 0:
        ui.fail("Cannot talk to Docker. Check that the daemon is running and that\n"
                "your user belongs to the 'docker' group.")


def imagenes_ausentes() -> list[str] | None:
    """Imagenes del compose que no estan en local, o None si no se puede saber."""
    conf = compose("config", "--images", check=False)
    if conf.returncode != 0:
        return None
    return [imagen for imagen in conf.stdout.split()
            if subprocess.run(["docker", "image", "inspect", imagen],
                              capture_output=True).returncode != 0]


def descargar_imagenes() -> None:
    """Si el registry no responde pero las imagenes ya estan en local, se sigue."""
    pull = compose("pull", check=False)
    if pull.returncode == 0:
        return

    error = (pull.stderr + pull.stdout).strip()[:300]
    ausentes = imagenes_ausentes()
    if ausentes is None:
        ui.fail(f"Failed to pull the images: {error}")
    if ausentes:
        lista = "\n".join(f"    {imagen}" for imagen in ausentes)
        ui.fail(f"Failed to pull the images, and these are not available locally:\n{lista}\n{error}")
    ui.warn("Could not reach the registry; using the images already present locally.")


def esperar_conector(timeout: int, intervalo: int = 5, sondeos_estables: int = 2) -> None:
    """Espera a ver el conector arriba varias veces seguidas: con la configuracion
    mal arranca y muere a los pocos segundos."""
    estado, estables = "no-container", 0
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        estado = stack.vigilar("provider")
        estables = estables + 1 if estado == "running" else 0
        if estables >= sondeos_estables:
            return
        time.sleep(intervalo)
    stack.abortar_con_logs(f"The connector did not start within {timeout}s (state: {estado}).",
                           "provider")


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--timeout", type=int, default=120,
                   help="Seconds to wait for each component to start (default 120).")


def run(args: argparse.Namespace) -> None:
    comprobar_entorno()

    ui.step("Pulling images...")
    descargar_imagenes()

    ui.step("Starting vault and IdentityHub...")
    r = compose("up", "-d", "identityhub", check=False)
    if r.returncode != 0 and "vault-init" in r.stderr:
        # Compose solo dice "is unhealthy"; el motivo esta en el log de vault-init.
        stack.abortar_con_logs("Vault could not be prepared.", "vault-init")
    elif r.returncode != 0:
        ui.fail(f"`docker compose up` failed: {(r.stderr or r.stdout).strip()}")
    stack.recrear_si_desfasado("identityhub", info.IH_PROPS_FILE)

    ui.step("Registering the participant in the IdentityHub...")
    identityhub.registrar(args.timeout)

    ui.step("Starting the connector...")
    compose("up", "-d")
    stack.recrear_si_desfasado("provider", info.PROPS_FILE)

    ui.step("Waiting for the connector to start...")
    esperar_conector(args.timeout)
    ui.ok("Connector is up.")

    info.resumen(info.estados())
