#!/usr/bin/env python3
"""Paso 1: genera el fichero de configuracion del conector en el directorio actual.

Escribe cuatro ficheros y no hace nada con Docker:

    provider_config_secret.properties   la configuracion del conector
    claims.json                         los claims del participante
    docker-compose.yml                  el stack (conector + postgres)
    vptoken.txt                         vacio; lo rellena el conector

Las claves Ed25519 no se generan aqui: las crea el contenedor `key-init` al
levantar el stack en el paso 2. El .properties solo referencia sus rutas, no su
contenido, asi que puede escribirse antes de que existan.

Uso:
    python3 generate.py --participant-id inferia --host 203.0.113.10
"""

import argparse
import json
import secrets
import socket
import sys
from importlib.resources import files
from pathlib import Path

from bitds_connector import info, ui

TEMPLATES = files("bitds_connector") / "templates"
BASE_PROPERTIES = TEMPLATES / "provider-base-configuration.properties"
COMPOSE_TEMPLATE = TEMPLATES / "docker-compose.yml.tmpl"
DEFAULT_CLAIMS = TEMPLATES / "claims.default.json"

# Repo de la imagen del conector. La tag la eliges tu: no hay una por defecto,
# porque una default codificada a mano acaba quedandose vieja sin que nadie lo note.
# Imagen del conector. Es una tag movil: cada despliegue coge la ultima
# publicada, y el valor concreto queda congelado en el docker-compose.yml
# que escribe `generate`.
IMAGE = "ghcr.io/bitds/provider-base:latest"

# Como se invoca la herramienta, para los mensajes de ayuda.
PROG = "connector"

# Los puertos del conector se derivan de una base: 18000 -> 18001, 18003...
# control (+5) y version (+6) no se publican, solo los usa el conector por dentro.
OFFSETS = {"http": 1, "management": 3, "protocol": 4, "control": 5, "version": 6, "public": 101}

# Solo estos se publican en el host; control y version viven dentro de la red de compose.
PUBLICADOS = ("http", "management", "protocol", "public")

# Namespace de los claims del EDC.
NS = "https://w3id.org/edc/v0.0.1/ns/"

# Los 27 de la UE en ISO 3166-1 alfa-3, en minusculas (que es como los espera el conector).
EU_COUNTRIES = {
    "aut": "Austria", "bel": "Belgium", "bgr": "Bulgaria", "cyp": "Cyprus",
    "cze": "Czechia", "deu": "Germany", "dnk": "Denmark", "esp": "Spain",
    "est": "Estonia", "fin": "Finland", "fra": "France", "grc": "Greece",
    "hrv": "Croatia", "hun": "Hungary", "irl": "Ireland", "ita": "Italy",
    "ltu": "Lithuania", "lux": "Luxembourg", "lva": "Latvia", "mlt": "Malta",
    "nld": "Netherlands", "pol": "Poland", "prt": "Portugal", "rou": "Romania",
    "svk": "Slovakia", "svn": "Slovenia", "swe": "Sweden",
}

ENTITY_TYPES = ("public", "private", "ngo")


class ListarPaises(argparse.Action):
    """--list-countries: imprime la tabla y sale, sin exigir el resto de argumentos."""

    def __call__(self, parser, namespace, values, option_string=None):
        codigos = sorted(EU_COUNTRIES)
        filas = -(-len(codigos) // 3)  # 3 columnas
        for i in range(filas):
            columna = (f"{c}  {EU_COUNTRIES[c]:<14}" for c in codigos[i::filas])
            print("  " + "".join(columna).rstrip())
        parser.exit()


def set_properties(path: Path, overrides: dict) -> None:
    """Sustituye las claves indicadas en el .properties; las que no existan, las anade.
    El resto del fichero (comentarios y claves fijas) se conserva tal cual."""
    lines = path.read_text().splitlines()
    for key, value in overrides.items():
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                break
        else:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")


def derivar_puertos(base: int) -> dict:
    return {nombre: base + off for nombre, off in OFFSETS.items()}


def puerto_libre(puerto: int) -> bool:
    """True si nadie esta escuchando en ese puerto."""
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", puerto))
            return True
        except OSError:
            return False


def comprobar_puertos(ports: dict) -> None:
    """Aborta si algun puerto publicado esta ocupado, proponiendo una base libre.

    Sin esto el fallo no aparece hasta el `docker compose up`, como un error de
    Docker que no dice cual es el puerto en conflicto.
    """
    ocupados = [(n, ports[n]) for n in PUBLICADOS if not puerto_libre(ports[n])]
    if not ocupados:
        return
    detalle = ", ".join(f"{n} ({puerto})" for n, puerto in ocupados)
    alternativa = primera_base_libre()
    sugerencia = (f"Drop --port-base to pick one automatically, or use --port-base {alternativa}."
                  if alternativa else "No free port block left between 18000 and 19999.")
    ui.fail(f"port base {ports['http'] - OFFSETS['http']} is not usable: "
            f"{detalle} already in use.\n{sugerencia}")


def primera_base_libre(desde: int = 18000, hasta: int = 19999, paso: int = 200) -> int | None:
    """Primera base cuyo bloque publicado esta entero libre."""
    for base in range(desde, hasta, paso):
        if all(puerto_libre(base + OFFSETS[n]) for n in PUBLICADOS):
            return base
    return None


def elegir_base(pedida: int | None, previa: int | None) -> int:
    """Decide la base del bloque de puertos.

    1. Si se pide una a mano, se respeta y se valida (nadie mas debe tenerla).
    2. Si no, se reusa la del despliegue que ya hay aqui: regenerar la config
       no debe cambiarle los puertos a un conector que ya esta registrado.
    3. Si tampoco la hay, se coge la primera libre del servidor.
    """
    if pedida is not None:
        # Si coincide con la del propio despliegue, los puertos "ocupados" son suyos.
        if pedida != previa:
            comprobar_puertos(derivar_puertos(pedida))
        return pedida
    if previa is not None:
        return previa
    libre = primera_base_libre()
    if libre is None:
        ui.fail("No free port block left between 18000 and 19999.")
    return libre


def base_configurada(props: Path) -> int | None:
    """port_base del despliegue que ya hay aqui, deducida de web.http.port."""
    for linea in props.read_text().splitlines():
        if linea.startswith("web.http.port="):
            try:
                return int(linea.split("=", 1)[1]) - OFFSETS["http"]
            except ValueError:
                return None
    return None


def confirmar_sobrescritura(force: bool) -> None:
    """Avisa de lo que se pierde al regenerar y pide confirmacion explicita."""
    print(
        ui.yellow("WARNING: a connector is already configured in this directory.\n")
        + "\n"
        "If you continue:\n"
        "  - A NEW API key is generated. The one you saved will stop working.\n"
        "  - A NEW database password is generated, which will NOT match the\n"
        "    existing Postgres volume. The connector will not start until you\n"
        "    delete that volume with `docker compose down -v`, which ERASES its data.\n"
        "  - The Ed25519 keys are left untouched: the participant identity is kept.\n",
        file=sys.stderr,
    )
    if force:
        print("Continuing because of --force.\n", file=sys.stderr)
        return
    if not sys.stdin.isatty():
        ui.fail("Cancelled: no terminal to confirm on. Use --force if you are sure.")
    respuesta = input("Type 'yes' to continue: ").strip().lower()
    if respuesta not in {"yes", "y"}:
        raise SystemExit("Cancelled. Nothing was modified.")


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--participant-id", required=True, help="Short name: inferia, acme...")
    p.add_argument("--host", required=True, help="Public IP or domain of THIS server.")
    p.add_argument("--image", default=IMAGE,
                   help=f"Connector image to deploy (default: {IMAGE}).")
    p.add_argument("--force", action="store_true",
                   help="Regenerate without asking (for non-interactive use).")
    p.add_argument("--registry-url", required=True,
                   help="URL of the BITDS participant registry the connector talks to.")
    p.add_argument("--port-base", type=int, default=None,
                   help="Port block base. Defaults to the one already deployed here, "
                        "or the first free block on the server.")
    p.add_argument("--country", required=True, type=str.lower, choices=sorted(EU_COUNTRIES),
                   metavar="COD", help="Country as ISO alpha-3. List them: --list-countries")
    p.add_argument("--entity-type", required=True, choices=ENTITY_TYPES,
                   help="Participant entity type.")
    p.add_argument("--location", default="eu", type=str.lower,
                   help="Participant location claim (default: eu).")
    p.add_argument("--list-countries", action=ListarPaises, nargs=0,
                   help="Show the 27 EU countries and exit.")


def run(args: argparse.Namespace) -> None:

    # Un host de loopback deja el conector arrancado pero inalcanzable desde fuera.
    if args.host.lower() in {"localhost", "127.0.0.1", "0.0.0.0"}:
        ui.fail(f"'{args.host}' is not a valid --host: use the public IP or domain of the server.")

    name = args.participant_id
    imagen = args.image
    did = f"did:web:bitds.eu:participant:{name}:public:did"
    api_key = secrets.token_hex(32)
    db_password = secrets.token_hex(16)
    db_name = f"participant-{name}-db"
    db_user = f"participant-{name}"

    out = Path.cwd()
    props = out / "provider_config_secret.properties"

    # Antes de tocar nada: confirmar si se pisa un despliegue y decidir los puertos.
    base_previa = base_configurada(props) if props.exists() else None
    if props.exists():
        confirmar_sobrescritura(args.force)
    ports = derivar_puertos(elegir_base(args.port_base, base_previa))

    # 1. El .properties: la plantilla base (53 lineas con valores de relleno)
    #    y encima las claves concretas de este despliegue.
    props.write_text(BASE_PROPERTIES.read_text())
    set_properties(props, {
        "edc.participant.id": did,
        "edc.hostname": f"http://{args.host}",
        "web.http.port": ports["http"],
        "web.http.management.port": ports["management"],
        "web.http.protocol.port": ports["protocol"],
        "web.http.public.port": ports["public"],
        "web.http.control.port": ports["control"],
        "web.http.version.port": ports["version"],
        "web.http.management.auth.key": api_key,
        "edc.dsp.callback.address": f"http://{args.host}:{ports['protocol']}/protocol",
        "edc.dataplane.api.public.baseurl": f"http://{args.host}:{ports['public']}/public",
        # El conector se llama a si mismo: su propio host y el puerto http dinamico.
        "edc.participant.eidas.url": f"http://{args.host}:{ports['http']}/api/vp_token",
        # Postgres vive en el mismo compose: se resuelve por nombre de servicio.
        "edc.datasource.default.user": db_user,
        "edc.datasource.default.password": db_password,
        "edc.datasource.default.url": f"jdbc:postgresql://db:5432/{db_name}",
        "edc.participant.registry.url": args.registry_url,
        "edc.participant.claims": "identity/claims.json",
        "edc.participant.private.key": "identity/ed25519_private.pem",
        "edc.participant.public.key": "identity/ed25519_public.pem",
    })
    props.chmod(0o600)  # lleva la API key y la password de la BD

    # 2. Los claims del participante.
    claims = json.loads(DEFAULT_CLAIMS.read_text())
    claims[NS + "country"] = args.country
    claims[NS + "location"] = args.location
    claims[NS + "entity_type"] = args.entity_type
    (out / "claims.json").write_text(json.dumps(claims, indent=2) + "\n")

    # 3. El compose, con los puertos y la imagen ya sustituidos.
    (out / "docker-compose.yml").write_text(COMPOSE_TEMPLATE.read_text().format(
        name=name, image=imagen, db_name=db_name, db_user=db_user,
        db_password=db_password, http=ports["http"], management=ports["management"],
        protocol=ports["protocol"], public=ports["public"],
    ))

    # 4. El fichero del VP token, vacio. El conector lo espera en
    #    /identity/vptoken.txt; si ya existe se deja tal cual.
    (out / "vptoken.txt").touch()

    ui.title(f"Connector '{name}' configured in {out}")
    ui.field("Participant DID", did)
    ui.field("Image", imagen)
    ui.field("Ports", f"http={ports['http']} management={ports['management']} "
                      f"protocol={ports['protocol']} public={ports['public']}")
    ui.field("API key", api_key)
    ui.field("Claims", f"country={args.country} ({EU_COUNTRIES[args.country]}) "
                       f"location={args.location} entity_type={args.entity_type}")

    # En un despliegue nuevo aun no hay claves: las crea key-init durante el `up`.
    # Al regenerar la config si existen, porque no se tocan.
    clave = info.leer_clave_publica()
    ui.field("Public key", clave if clave else ui.dim(f"created by `{PROG} up`"))

    print(f"\nNext step: {ui.cyan(PROG + ' up')}")
