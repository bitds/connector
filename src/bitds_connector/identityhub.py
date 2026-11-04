"""Alta del participante en el IdentityHub de este despliegue.

El IdentityHub solo entrega el client secret del STS en la respuesta del alta, y
el conector lo necesita para pedir sus tokens DCP. Por eso el alta se hace antes
de arrancar el conector, y el secret se guarda en su configuracion.
"""

import json
import time
import urllib.error
import urllib.request

from bitds_connector import info, propiedades, stack, ui


class IdentityApi:
    """La Identity API, como super-user. Solo escucha en 127.0.0.1."""

    def __init__(self, puerto: str, clave: str):
        self.base = f"http://127.0.0.1:{puerto}/api/identity/v1alpha"
        self.clave = clave

    def get(self, ruta: str) -> tuple[int, dict | None]:
        return self._llamar("GET", ruta)

    def post(self, ruta: str, cuerpo: dict) -> tuple[int, dict | None]:
        return self._llamar("POST", ruta, cuerpo)

    def _llamar(self, metodo: str, ruta: str, cuerpo: dict | None = None) -> tuple[int, dict | None]:
        peticion = urllib.request.Request(
            self.base + ruta, method=metodo,
            data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
            headers={"x-api-key": self.clave, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(peticion, timeout=10) as r:
                return r.status, _json(r.read())
        except urllib.error.HTTPError as e:
            return e.code, _json(e.read())


def _json(datos: bytes) -> dict | None:
    texto = datos.decode(errors="replace")
    try:
        return json.loads(texto) if texto.strip() else None
    except ValueError:
        return {"raw": texto}


def esperar(api: IdentityApi, timeout: int, intervalo: int = 3) -> None:
    """Espera a que la API este lista de verdad.

    Jetty escucha antes de tener registradas las rutas y, mientras tanto,
    contesta 404 a todo. Solo cuando el listado de participantes da 200 se puede
    creer un 404 sobre un participante concreto.
    """
    ultimo = "no answer"
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        stack.vigilar("identityhub")
        try:
            codigo, _ = api.get("/participants")
            if codigo == 200:
                return
            ultimo = f"HTTP {codigo}"
        except OSError as e:
            ultimo = str(getattr(e, "reason", e))
        time.sleep(intervalo)

    if ultimo in ("HTTP 401", "HTTP 403"):
        ui.fail(f"The IdentityHub rejects the super-user key of this directory ({ultimo}).\n"
                "Its database was probably created with another configuration: check "
                f"edc.ih.api.superuser.key in {info.IH_PROPS_FILE}.")
    stack.abortar_con_logs(f"The IdentityHub did not answer within {timeout}s ({ultimo}).",
                           "identityhub")


def manifiesto(props: dict, ih: dict) -> dict:
    """El participante tal como se da de alta. Sus dos endpoints acaban en el did.json."""
    contexto = props["edc.participant.context.id"]
    did = props["edc.participant.id"]
    credential_service = (f"http://{props['edc.hostname']}:{ih['web.http.credentials.port']}"
                          f"/api/credentials/v1/participants/{contexto}")
    return {
        "participantContextId": contexto,
        "did": did,
        "active": True,
        "roles": [],
        "serviceEndpoints": [
            {"id": "credential-service", "type": "CredentialService",
             "serviceEndpoint": credential_service},
            {"id": "dsp", "type": "ProtocolEndpoint",
             "serviceEndpoint": props["edc.dsp.callback.address"]},
        ],
        "key": {
            "keyId": f"{did}#key-1",
            "privateKeyAlias": f"{contexto}-key-1",
            "keyGeneratorParams": {"algorithm": "EC"},
            "usage": ["sign_token", "sign_presentation"],
        },
    }


def registrar(timeout: int) -> None:
    """Da de alta al participante si aun no existe y guarda su secret del STS."""
    props = propiedades.leer(info.PROPS_FILE)
    ih = propiedades.leer(info.IH_PROPS_FILE)
    contexto = props["edc.participant.context.id"]
    api = IdentityApi(ih["web.http.identity.port"], ih["edc.ih.api.superuser.key"])

    esperar(api, timeout)

    codigo, _ = api.get(f"/participants/{contexto}")
    if codigo == 200:
        if not props.get("bitds.sts.client.secret"):
            ui.fail(f"The participant '{contexto}' already exists in the IdentityHub, but "
                    f"{info.PROPS_FILE} has no bitds.sts.client.secret,\nand the IdentityHub "
                    "only returns it when the participant is created. Put it back, or start "
                    "over with\n`docker compose down -v` (this ERASES the connector and "
                    "IdentityHub data).")
        ui.ok(f"Participant '{contexto}' already registered in the IdentityHub.")
        return

    codigo, respuesta = api.post("/participants", manifiesto(props, ih))
    secreto = (respuesta or {}).get("clientSecret")
    if not secreto:
        stack.abortar_con_logs(f"Registering the participant in the IdentityHub failed "
                               f"(HTTP {codigo}): {json.dumps(respuesta)[:300]}", "identityhub")

    propiedades.actualizar(info.PROPS_FILE, {"bitds.sts.client.secret": secreto})
    ui.ok(f"Participant '{contexto}' registered in the IdentityHub.")
