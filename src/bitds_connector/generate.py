"""Paso 1: escribe la configuracion del participante en el directorio actual.

No toca Docker. El alta en el IdentityHub la hace `up`, porque necesita el
IdentityHub arrancado.
"""

import argparse
import base64
import os
import re
import secrets
import socket
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from bitds_connector import info, propiedades, ui

PLANTILLAS = files("bitds_connector") / "templates"

# Fijadas al mismo commit de base-bit-ds: conector e IdentityHub tienen que ir a la par.
IMAGE = "ghcr.io/bitds/provider-base:dcp-ffdfe0c"
IH_IMAGE = "ghcr.io/bitds/identity-hub:dcp-ffdfe0c"
VAULT_IMAGE = "hashicorp/vault:latest"

PROG = "connector"
SUPER_USER = "super-user"
IH_DB = "identityhub"

OFFSETS = {"http": 1, "management": 3, "protocol": 4, "control": 5, "version": 6,
           "signaling": 7, "ih_http": 11, "ih_identity": 12, "ih_credentials": 13,
           "ih_did": 14, "ih_sts": 15, "public": 101}

# Los que se abren en el host. Todos deben estar libres.
PUBLICADOS = ("http", "management", "protocol", "public",
              "ih_credentials", "ih_did", "ih_identity")

# Valido a la vez en el DID, en compose, en postgres y en el YAML generado, sin escapar nada.
PARTICIPANT_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?")


@dataclass
class Participante:
    nombre: str
    host: str
    issuer_did: str
    puertos: dict
    secretos: dict

    @property
    def did(self) -> str:
        return did_de(self.host, self.puertos["ih_did"], self.nombre)

    @property
    def db_user(self) -> str:
        return f"participant-{self.nombre}"

    @property
    def db_name(self) -> str:
        return f"participant-{self.nombre}-db"

    def url(self, puerto: str, ruta: str) -> str:
        return f"http://{self.host}:{self.puertos[puerto]}/{ruta}"


def participant_id(valor: str) -> str:
    if not PARTICIPANT_ID.fullmatch(valor):
        raise argparse.ArgumentTypeError(
            f"'{valor}' is not valid: use 1-32 lowercase letters, digits or hyphens, "
            "not starting or ending with a hyphen (e.g. inferia, acme-labs).")
    return valor


def did_valido(valor: str) -> str:
    if not valor.startswith("did:") or any(c.isspace() for c in valor):
        raise argparse.ArgumentTypeError(
            f"'{valor}' is not a DID (e.g. did:web:issuer.example.org%3A7183:issuer).")
    return valor


def did_de(host: str, puerto_did: int, nombre: str) -> str:
    """did:web:h%3A18014:x se resuelve en http://h:18014/x/did.json, que es lo que publica el IdentityHub."""
    return f"did:web:{host}%3A{puerto_did}:{nombre}"


def clave_super_user() -> str:
    """Formato del IdentityHub: base64(id) + "." + secreto."""
    usuario = base64.b64encode(SUPER_USER.encode()).decode()
    secreto = base64.b64encode(secrets.token_bytes(32)).decode()
    return f"{usuario}.{secreto}"


def generar_secretos(previos: dict, ih_previos: dict) -> dict:
    """Solo cambia la API key. El resto esta atado a lo que ya hay guardado (volumen
    de postgres, vault, alta en el IdentityHub) y uno nuevo dejaria el stack roto."""
    return {
        "api_key": secrets.token_hex(32),
        "db_password": previos.get("edc.datasource.default.password") or secrets.token_hex(16),
        "sts_secret": previos.get("bitds.sts.client.secret", ""),
        "super_user_key": ih_previos.get("edc.ih.api.superuser.key") or clave_super_user(),
        "vault_token": ih_previos.get("edc.vault.hashicorp.token") or secrets.token_hex(24),
    }


# --- Puertos ---

def derivar_puertos(base: int) -> dict:
    return {nombre: base + offset for nombre, offset in OFFSETS.items()}


def puerto_libre(puerto: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", puerto))
            return True
        except OSError:
            return False


def primera_base_libre(desde: int = 18000, hasta: int = 19999, paso: int = 200) -> int | None:
    for base in range(desde, hasta, paso):
        if all(puerto_libre(base + OFFSETS[n]) for n in PUBLICADOS):
            return base
    return None


def comprobar_puertos(puertos: dict) -> None:
    """Falla aqui, diciendo cual, en vez de en el `docker compose up`."""
    ocupados = [(n, puertos[n]) for n in PUBLICADOS if not puerto_libre(puertos[n])]
    if not ocupados:
        return
    detalle = ", ".join(f"{n} ({p})" for n, p in ocupados)
    alternativa = primera_base_libre()
    sugerencia = (f"Drop --port-base to pick one automatically, or use --port-base {alternativa}."
                  if alternativa else "No free port block left between 18000 and 19999.")
    ui.fail(f"port base {puertos['http'] - OFFSETS['http']} is not usable: "
            f"{detalle} already in use.\n{sugerencia}")


def elegir_base(pedida: int | None, previa: int | None) -> int:
    """La pedida a mano, si no la del despliegue de aqui, y si no la primera libre.

    Reusar la previa importa: el puerto del DID va dentro del propio DID.
    """
    if pedida is not None:
        if pedida != previa:  # si es la previa, los puertos "ocupados" son los suyos
            comprobar_puertos(derivar_puertos(pedida))
        return pedida
    if previa is not None:
        return previa
    libre = primera_base_libre()
    if libre is None:
        ui.fail("No free port block left between 18000 and 19999.")
    return libre


def base_configurada(props: Path) -> int | None:
    puerto = propiedades.leer(props).get("web.http.port", "")
    return int(puerto) - OFFSETS["http"] if puerto.isdigit() else None


# --- Ficheros ---

def _desde_plantilla(destino: Path, plantilla: str, cambios: dict) -> None:
    destino.write_text((PLANTILLAS / plantilla).read_text())
    destino.chmod(0o600)
    propiedades.actualizar(destino, cambios)


def escribir_conector(p: Participante, destino: Path) -> None:
    _desde_plantilla(destino, "provider-base-configuration.properties", {
        "edc.participant.id": p.did,
        "edc.participant.context.id": p.nombre,
        "edc.hostname": p.host,
        "web.http.port": p.puertos["http"],
        "web.http.management.port": p.puertos["management"],
        "web.http.protocol.port": p.puertos["protocol"],
        "web.http.public.port": p.puertos["public"],
        "web.http.control.port": p.puertos["control"],
        "web.http.version.port": p.puertos["version"],
        "web.http.signaling.port": p.puertos["signaling"],
        "web.http.management.auth.key": p.secretos["api_key"],
        "edc.dsp.callback.address": p.url("protocol", "protocol"),
        "edc.dataplane.api.public.baseurl": p.url("public", "public"),
        "edc.dataplane.proxy.public.endpoint": p.url("public", "public"),
        "edc.datasource.default.user": p.db_user,
        "edc.datasource.default.password": p.secretos["db_password"],
        "edc.datasource.default.url": f"jdbc:postgresql://db:5432/{p.db_name}",
        "edc.iam.sts.oauth.token.url": f"http://identityhub:{p.puertos['ih_sts']}/api/sts/token",
        "edc.iam.sts.oauth.client.id": p.did,
        "edc.iam.sts.oauth.client.secret.alias": f"{p.nombre}-sts-client-secret",
        "bitds.sts.client.secret": p.secretos["sts_secret"],
        "edc.iam.trustedissuer.issuerservice.id": p.issuer_did,
    })


def escribir_identityhub(p: Participante, destino: Path) -> None:
    _desde_plantilla(destino, "identity-hub-configuration.properties", {
        "web.http.port": p.puertos["ih_http"],
        "web.http.identity.port": p.puertos["ih_identity"],
        "web.http.credentials.port": p.puertos["ih_credentials"],
        "web.http.did.port": p.puertos["ih_did"],
        "web.http.sts.port": p.puertos["ih_sts"],
        "edc.ih.api.superuser.key": p.secretos["super_user_key"],
        "edc.datasource.default.user": p.db_user,
        "edc.datasource.default.password": p.secretos["db_password"],
        "edc.datasource.default.url": f"jdbc:postgresql://db:5432/{IH_DB}",
        "edc.vault.hashicorp.token": p.secretos["vault_token"],
    })


def escribir_stack(p: Participante, out: Path, image: str, ih_image: str) -> None:
    # Se crean aqui para que sean del usuario: si los crea Docker al montarlos, son de root.
    for carpeta in ("keys", "vault/config", "vault/keys"):
        (out / carpeta).mkdir(parents=True, exist_ok=True)
    (out / "vault" / "keys").chmod(0o700)

    (out / "db-init.sql").write_text(f'CREATE DATABASE "{IH_DB}";\n')
    (out / "vault" / "config" / "vault.hcl").write_text((PLANTILLAS / "vault.hcl").read_text())
    (out / "vault" / "vault-init.sh").write_text((PLANTILLAS / "vault-init.sh").read_text())

    plantilla = (PLANTILLAS / "docker-compose.yml.tmpl").read_text()
    (out / "docker-compose.yml").write_text(plantilla.format(
        name=p.nombre, image=image, ih_image=ih_image, vault_image=VAULT_IMAGE,
        db_name=p.db_name, db_user=p.db_user, db_password=p.secretos["db_password"],
        uid=os.getuid(), gid=os.getgid(), **p.puertos,
    ))


# --- Comando ---

def confirmar_sobrescritura(force: bool) -> None:
    print(
        ui.yellow("WARNING: a connector is already configured in this directory.\n")
        + "\n"
        "If you continue:\n"
        "  - A NEW management API key is generated. The one you saved will stop working.\n"
        "  - The database password, the vault token, the IdentityHub super-user key\n"
        "    and the STS client secret are kept, so the existing data still works.\n"
        "  - Changing --host or --port-base changes the participant DID: the\n"
        "    participant would have to be registered again (`docker compose down -v`).\n",
        file=sys.stderr,
    )
    if force:
        print("Continuing because of --force.\n", file=sys.stderr)
        return
    if not sys.stdin.isatty():
        ui.fail("Cancelled: no terminal to confirm on. Use --force if you are sure.")
    if input("Type 'yes' to continue: ").strip().lower() not in {"yes", "y"}:
        raise SystemExit("Cancelled. Nothing was modified.")


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--participant-id", required=True, type=participant_id,
                   help="Short name: lowercase letters, digits and hyphens (inferia, acme-labs...).")
    p.add_argument("--host", required=True, help="Public IP or domain of THIS server.")
    p.add_argument("--issuer-did", required=True, type=did_valido,
                   help="DID of the trusted credential issuer (ask the dataspace authority).")
    p.add_argument("--image", default=IMAGE,
                   help=f"Connector image to deploy (default: {IMAGE}).")
    p.add_argument("--ih-image", default=IH_IMAGE,
                   help=f"IdentityHub image to deploy (default: {IH_IMAGE}).")
    p.add_argument("--force", action="store_true",
                   help="Regenerate without asking (for non-interactive use).")
    p.add_argument("--port-base", type=int, default=None,
                   help="Port block base. Defaults to the one already deployed here, "
                        "or the first free block on the server.")


def mostrar(p: Participante, args: argparse.Namespace, out: Path) -> None:
    ui.title(f"Connector '{p.nombre}' configured in {out}")
    ui.field("Participant DID", p.did)
    ui.field("Trusted issuer", p.issuer_did)
    ui.field("Connector image", args.image)
    ui.field("IdentityHub image", args.ih_image)
    ui.field("Ports", f"http={p.puertos['http']} management={p.puertos['management']} "
                      f"protocol={p.puertos['protocol']} public={p.puertos['public']}")
    ui.field("IdentityHub ports", f"credentials={p.puertos['ih_credentials']} "
                                  f"did={p.puertos['ih_did']} "
                                  f"identity={p.puertos['ih_identity']}")
    ui.field("API key", p.secretos["api_key"])
    print(f"\nNext step: {ui.cyan(PROG + ' up')}")


def run(args: argparse.Namespace) -> None:
    if args.host.lower() in {"localhost", "127.0.0.1", "0.0.0.0"}:
        ui.fail(f"'{args.host}' is not a valid --host: use the public IP or domain of the server.")

    out = Path.cwd()
    props = out / info.PROPS_FILE
    ih_props = out / info.IH_PROPS_FILE

    if props.exists():
        confirmar_sobrescritura(args.force)

    participante = Participante(
        nombre=args.participant_id,
        host=args.host,
        issuer_did=args.issuer_did,
        puertos=derivar_puertos(elegir_base(args.port_base, base_configurada(props))),
        secretos=generar_secretos(propiedades.leer(props), propiedades.leer(ih_props)),
    )

    escribir_conector(participante, props)
    escribir_identityhub(participante, ih_props)
    escribir_stack(participante, out, args.image, args.ih_image)
    mostrar(participante, args, out)
