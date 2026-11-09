"""Tests de generate.py. Solo libreria estandar, como el resto del proyecto.

    python3 -m unittest discover -s tests

Cubren las funciones puras y un `generate` completo en un directorio temporal.
Los puertos se simulan: los tests no deben depender de lo que este escuchando
en la maquina donde se ejecutan.
"""

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bitds_connector import cli, generate, info, propiedades  # noqa: E402


class ParticipantId(unittest.TestCase):

    def test_validos(self):
        for valor in ("inferia", "acme-labs", "a", "x1", "a" * 32):
            with self.subTest(valor=valor):
                self.assertEqual(generate.participant_id(valor), valor)

    def test_invalidos(self):
        # Cada uno rompe en un sitio distinto: mayusculas en compose, ':' en el
        # DID, espacios y comillas en el YAML, longitud en postgres.
        for valor in ("", "Inferia", "acme labs", "foo:bar", "a_b", "-acme", "acme-",
                      'x"y', "{name}", "a" * 33):
            with self.subTest(valor=valor), self.assertRaises(argparse.ArgumentTypeError):
                generate.participant_id(valor)

    def test_parser_lo_rechaza(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args([
                "generate", "--participant-id", "Bad Name", "--host", "203.0.113.10",
            ])


class Propiedades(unittest.TestCase):

    def test_sustituye_anade_y_conserva(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.properties"
            path.write_text("# comentario\na=1\nb=2\n")
            propiedades.actualizar(path, {"b": 20, "c": 3})
            self.assertEqual(path.read_text(), "# comentario\na=1\nb=20\nc=3\n")

    def test_no_confunde_prefijos(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.properties"
            path.write_text("web.http.port.extra=1\nweb.http.port=2\n")
            propiedades.actualizar(path, {"web.http.port": 9})
            self.assertEqual(path.read_text(), "web.http.port.extra=1\nweb.http.port=9\n")


class Puertos(unittest.TestCase):

    def test_derivar_puertos(self):
        ports = generate.derivar_puertos(18000)
        self.assertEqual((ports["http"], ports["management"], ports["protocol"], ports["public"]),
                         (18001, 18003, 18004, 18101))

    def test_base_configurada(self):
        with tempfile.TemporaryDirectory() as d:
            props = Path(d) / "p.properties"
            props.write_text("x=1\nweb.http.port=18401\n")
            self.assertEqual(generate.base_configurada(props), 18400)
            props.write_text("web.http.port=nope\n")
            self.assertIsNone(generate.base_configurada(props))
            props.write_text("x=1\n")
            self.assertIsNone(generate.base_configurada(props))

    def test_primera_base_libre_salta_bloques_ocupados(self):
        ocupados = {18001, 18204}  # uno en el bloque 18000 y otro en el 18200
        with mock.patch.object(generate, "puerto_libre", lambda p: p not in ocupados):
            self.assertEqual(generate.primera_base_libre(), 18400)

    def test_primera_base_libre_sin_hueco(self):
        with mock.patch.object(generate, "puerto_libre", lambda p: False):
            self.assertIsNone(generate.primera_base_libre())

    def test_elegir_base(self):
        with mock.patch.object(generate, "puerto_libre", lambda p: True):
            # Pedida a mano: se respeta.
            self.assertEqual(generate.elegir_base(18600, None), 18600)
            # Sin pedir: se reusa la del despliegue existente.
            self.assertEqual(generate.elegir_base(None, 18400), 18400)
            # Ni pedida ni previa: la primera libre.
            self.assertEqual(generate.elegir_base(None, None), 18000)

    def test_elegir_base_pedida_ocupada(self):
        with mock.patch.object(generate, "puerto_libre", lambda p: False), \
                self.assertRaises(SystemExit):
            generate.elegir_base(18600, None)

    def test_elegir_base_pedida_igual_a_la_previa(self):
        # Los puertos "ocupados" son los del propio conector: no es un error.
        with mock.patch.object(generate, "puerto_libre", lambda p: False):
            self.assertEqual(generate.elegir_base(18400, 18400), 18400)


class Identidad(unittest.TestCase):

    def test_issuer_did(self):
        self.assertEqual(generate.did_valido("did:web:x%3A1:issuer"), "did:web:x%3A1:issuer")
        for valor in ("issuer", "http://issuer", "did:web:a b"):
            with self.subTest(valor=valor), self.assertRaises(argparse.ArgumentTypeError):
                generate.did_valido(valor)

    def test_did_y_su_url(self):
        did = generate.did_de("203.0.113.10", 18014, "acme")
        self.assertEqual(did, "did:web:203.0.113.10%3A18014:acme")
        self.assertEqual(info.url_did(did), "http://203.0.113.10:18014/acme/did.json")

    def test_clave_super_user(self):
        prefijo, _, secreto = generate.clave_super_user().partition(".")
        self.assertEqual(prefijo, "c3VwZXItdXNlcg==")  # base64("super-user")
        self.assertTrue(secreto)


class GenerateCompleto(unittest.TestCase):
    """Un `generate` de verdad: valida tambien las plantillas."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        cwd = os.getcwd()
        os.chdir(self.dir.name)
        self.addCleanup(os.chdir, cwd)
        parche = mock.patch.object(generate, "puerto_libre", lambda p: True)
        parche.start()
        self.addCleanup(parche.stop)

    def generar(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["generate", "--participant-id", "acme-labs", "--host", "203.0.113.10",
                      "--issuer-did", "did:web:issuer.example.org%3A7183:issuer", *extra])

    def test_ficheros(self):
        self.generar()
        d = Path(self.dir.name)
        did = "did:web:203.0.113.10%3A18014:acme-labs"

        props = d / "provider_config_secret.properties"
        self.assertEqual(props.stat().st_mode & 0o777, 0o600)
        texto = props.read_text()
        self.assertIn(f"edc.participant.id={did}\n", texto)
        self.assertIn(f"edc.iam.sts.oauth.client.id={did}\n", texto)
        self.assertIn("edc.iam.sts.oauth.token.url=http://identityhub:18015/api/sts/token\n", texto)
        self.assertIn("web.http.port=18001\n", texto)
        self.assertIn("edc.dsp.callback.address=http://203.0.113.10:18004/protocol\n", texto)
        self.assertIn("bitds.sts.client.secret=\n", texto)  # lo rellena `up`
        self.assertIn("edc.iam.trustedissuer.issuerservice.id="
                      "did:web:issuer.example.org%3A7183:issuer\n", texto)
        self.assertIn('edc.iam.trustedissuer.issuerservice.supportedtypes=["MembershipCredential"]\n',
                      texto)

        ih = d / "identityhub_config_secret.properties"
        self.assertEqual(ih.stat().st_mode & 0o777, 0o600)
        ih_texto = ih.read_text()
        self.assertIn("web.http.did.port=18014\n", ih_texto)
        self.assertIn("edc.datasource.default.url=jdbc:postgresql://db:5432/identityhub\n", ih_texto)
        self.assertNotIn("=changeme", ih_texto)  # ningun secreto de relleno

        compose = (d / "docker-compose.yml").read_text()
        self.assertIn('name: "acme-labs"', compose)
        self.assertIn('"18101:18101"', compose)
        self.assertIn('"18012:18012"', compose)
        self.assertIn(f'image: "{generate.IMAGE}"', compose)
        self.assertIn(f'image: "{generate.IH_IMAGE}"', compose)
        self.assertNotIn("{", compose)  # ningun marcador sin sustituir

        self.assertTrue((d / "db-init.sql").exists())
        self.assertTrue((d / "vault" / "vault-init.sh").exists())
        self.assertTrue((d / "vault" / "config" / "vault.hcl").exists())
        self.assertTrue((d / "vault" / "keys").is_dir())
        self.assertTrue((d / "keys").is_dir())

    def test_regenerar_conserva_secretos(self):
        # Van atados al volumen de postgres, al vault y al alta en el IdentityHub.
        self.generar()
        d = Path(self.dir.name)
        propiedades.actualizar(d / "provider_config_secret.properties",
                                {"bitds.sts.client.secret": "secreto-sts"})
        antes = propiedades.leer(d / "provider_config_secret.properties")
        ih_antes = propiedades.leer(d / "identityhub_config_secret.properties")
        self.generar("--force")
        despues = propiedades.leer(d / "provider_config_secret.properties")
        ih_despues = propiedades.leer(d / "identityhub_config_secret.properties")
        for clave in ("edc.datasource.default.password", "bitds.sts.client.secret"):
            self.assertEqual(antes[clave], despues[clave])
        for clave in ("edc.ih.api.superuser.key", "edc.vault.hashicorp.token"):
            self.assertEqual(ih_antes[clave], ih_despues[clave])
        self.assertNotEqual(antes["web.http.management.auth.key"],
                            despues["web.http.management.auth.key"])

    def test_regenerar_mantiene_puertos(self):
        self.generar("--port-base", "18400")
        self.generar("--force")
        texto = (Path(self.dir.name) / "provider_config_secret.properties").read_text()
        self.assertIn("web.http.port=18401\n", texto)

    def test_host_loopback(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main(["generate", "--participant-id", "acme", "--host", "localhost",
                          "--issuer-did", "did:web:issuer.example.org%3A7183:issuer"])


if __name__ == "__main__":
    unittest.main()
