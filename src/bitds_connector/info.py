"""Datos del participante desplegado en este directorio, este el stack arrancado o no.

Lo usan tambien generate y up para su resumen final.
"""

from pathlib import Path

from bitds_connector import propiedades, stack, ui

PROPS_FILE = "provider_config_secret.properties"
IH_PROPS_FILE = "identityhub_config_secret.properties"


def url_did(did: str) -> str | None:
    """URL del did.json de un did:web. El puerto va codificado como %3A."""
    if not did.startswith("did:web:"):
        return None
    host, *ruta = did.removeprefix("did:web:").split(":")
    return f"http://{host.replace('%3A', ':')}/{'/'.join(ruta)}/did.json"


def estados() -> dict:
    return {servicio: stack.estado(servicio) for servicio in ("provider", "identityhub")}


def _pintar_estado(estado: str | None) -> str:
    if not estado:
        return ui.dim("not started")
    return ui.green(estado) if estado == "running" else ui.yellow(estado)


def resumen(estados: dict | None = None) -> None:
    props = propiedades.leer(PROPS_FILE)
    ih = propiedades.leer(IH_PROPS_FILE)
    did = props.get("edc.participant.id", "?")
    registrado = props.get("bitds.sts.client.secret")

    ui.title("Participant")
    if estados is not None:
        ui.field("Connector", _pintar_estado(estados.get("provider")))
        ui.field("IdentityHub", _pintar_estado(estados.get("identityhub")))
    ui.field("Connector URL", f"http://{props.get('edc.hostname', '?')}")
    ui.field("DID (ID de conexión)", did)
    ui.field("DID document", url_did(did) or "?")
    ui.field("DSP callback", props.get("edc.dsp.callback.address", "?"))
    ui.field("Trusted issuer", props.get("edc.iam.trustedissuer.issuerservice.id", "?"))
    ui.field("STS registered", ui.green("yes") if registrado else ui.yellow("no, run `connector up`"))

    ui.title("Ports")
    ui.field("HTTP port", props.get("web.http.port", "?"))
    ui.field("Management port", props.get("web.http.management.port", "?"))
    ui.field("Protocol port", props.get("web.http.protocol.port", "?"))
    ui.field("Public port", props.get("web.http.public.port", "?"))
    ui.field("IH credentials port", ih.get("web.http.credentials.port", "?"))
    ui.field("IH DID port", ih.get("web.http.did.port", "?"))
    ui.field("IH identity port", ih.get("web.http.identity.port", "?"))

    ui.title("Credentials")
    ui.field("Management API key", props.get("web.http.management.auth.key", "?"))
    ui.field("Identity Hub API key", ih.get("edc.ih.api.superuser.key", "?"))
    ui.field("Credential service", f"http://{props.get('edc.hostname', '?')}:"
                                   f"{ih.get('web.http.credentials.port', '?')}/api/credentials")
    ui.field("Identity API", f"http://{props.get('edc.hostname', '?')}:"
                             f"{ih.get('web.http.identity.port', '?')}/api/identity")


def run(args=None) -> None:
    if not Path(PROPS_FILE).exists():
        ui.fail(f"No {PROPS_FILE} in this directory.\n"
                "Run this first: connector generate --participant-id ... --host ...")
    resumen(estados())
