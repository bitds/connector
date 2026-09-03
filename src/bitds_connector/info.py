#!/usr/bin/env python3
"""Datos del conector desplegado en este directorio.

Se ejecuta en el directorio del despliegue, en cualquier momento y este el
conector arrancado o no:

    python3 info.py

Todo sale de los ficheros que dejo generate.py (el .properties y claims.json) y
de la clave publica que genero key-init, asi que la informacion siempre refleja
lo que hay ahora mismo en disco. Es tambien el modulo que usan generate.py y
up.py para imprimir su resumen, para no tener el formato duplicado en tres sitios.
"""

import json
import subprocess
from pathlib import Path

from bitds_connector import ui

PROPS_FILE = "provider_config_secret.properties"
CLAIMS_FILE = "claims.json"
PUBLIC_KEY = "ed25519_public.pem"

# Prefijo de los claims del EDC; se recorta para mostrarlos legibles.
NS = "https://w3id.org/edc/v0.0.1/ns/"


def leer_propiedades() -> dict:
    """El .properties del conector, como diccionario clave -> valor."""
    props = {}
    for linea in Path(PROPS_FILE).read_text().splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, _, valor = linea.partition("=")
            props[clave.strip()] = valor.strip()
    return props


def leer_clave_publica() -> str | None:
    """La clave publica en base64, sin las lineas BEGIN/END, o None si aun no existe."""
    pem = Path(PUBLIC_KEY)
    if not pem.exists():
        return None
    return "".join(l for l in pem.read_text().splitlines() if not l.startswith("-----"))


def estado_contenedor() -> str | None:
    """Estado del contenedor del conector, o None si no se puede averiguar
    (sin Docker, sin proyecto levantado o sin permisos)."""
    ps = subprocess.run(["docker", "compose", "ps", "--all", "--quiet", "provider"],
                        capture_output=True, text=True)
    if ps.returncode != 0 or not ps.stdout.strip():
        return None
    cid = ps.stdout.strip().splitlines()[0]
    inspect = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", cid],
                             capture_output=True, text=True)
    return inspect.stdout.strip() or None


def resumen(clave_publica: str | None = None, estado: str | None = None) -> None:
    """Los datos del conector: lo que hay que enviar para registrarlo."""
    props = leer_propiedades()

    ui.title("Connector details")
    if estado:
        ui.field("Status", ui.green(estado) if estado == "running" else ui.yellow(estado))
    ui.field("Participant DID", props.get("edc.participant.id", "?"))
    ui.field("Hostname (URL)", props.get("edc.hostname", "?"))
    ui.field("Callback address DSP", props.get("edc.dsp.callback.address", "?"))

    ui.title("Ports")
    ui.field("HTTP port", props.get("web.http.port", "?"))
    ui.field("Management port", props.get("web.http.management.port", "?"))
    ui.field("Protocol port", props.get("web.http.protocol.port", "?"))
    ui.field("Public port", props.get("web.http.public.port", "?"))

    ui.title("Credentials")
    ui.field("API key", props.get("web.http.management.auth.key", "?"))

    claims_path = Path(CLAIMS_FILE)
    if claims_path.exists():
        ui.title("Claims")
        for clave, valor in json.loads(claims_path.read_text()).items():
            ui.field(clave.removeprefix(NS), valor)

    ui.title("Public key")
    if clave_publica:
        print(f"  {ui.dim('send this to BITDS to register the participant')}")
        print(f"  {clave_publica}")
    else:
        print(f"  {ui.dim('not generated yet; it is created by')} {ui.cyan('connector up')}")


def run(args=None) -> None:
    if not Path(PROPS_FILE).exists():
        ui.fail(f"No {PROPS_FILE} in this directory.\n"
                "Run this first: connector generate --participant-id ... --host ...")
    resumen(leer_clave_publica(), estado_contenedor())
