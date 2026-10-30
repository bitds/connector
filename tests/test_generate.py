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

from bitds_connector import cli, generate  # noqa: E402


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
                "--country", "esp", "--entity-type", "public", "--registry-url", "https://r/",
            ])


class SetProperties(unittest.TestCase):

    def test_sustituye_anade_y_conserva(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.properties"
            path.write_text("# comentario\na=1\nb=2\n")
            generate.set_properties(path, {"b": 20, "c": 3})
            self.assertEqual(path.read_text(), "# comentario\na=1\nb=20\nc=3\n")

    def test_no_confunde_prefijos(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.properties"
            path.write_text("web.http.port.extra=1\nweb.http.port=2\n")
            generate.set_properties(path, {"web.http.port": 9})
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
                      "--country", "ESP", "--entity-type", "ngo",
                      "--registry-url", "https://registry.example.org/api/", *extra])

    def test_ficheros(self):
        self.generar()
        d = Path(self.dir.name)

        props = d / "provider_config_secret.properties"
        self.assertEqual(props.stat().st_mode & 0o777, 0o600)
        texto = props.read_text()
        self.assertIn("edc.participant.id=did:web:bitds.eu:participant:acme-labs:public:did\n", texto)
        self.assertIn("web.http.port=18001\n", texto)
        self.assertIn("edc.dsp.callback.address=http://203.0.113.10:18004/protocol\n", texto)

        claims = json.loads((d / "claims.json").read_text())
        self.assertEqual(claims[generate.NS + "country"], "esp")
        self.assertEqual(claims[generate.NS + "entity_type"], "ngo")

        compose = (d / "docker-compose.yml").read_text()
        self.assertIn('name: "acme-labs"', compose)
        self.assertIn('"18101:18101"', compose)
        self.assertIn(f'image: "{generate.IMAGE}"', compose)
        self.assertNotIn("{", compose)  # ningun marcador sin sustituir

        self.assertTrue((d / "vptoken.txt").exists())

    def test_regenerar_mantiene_puertos(self):
        self.generar("--port-base", "18400")
        self.generar("--force")
        texto = (Path(self.dir.name) / "provider_config_secret.properties").read_text()
        self.assertIn("web.http.port=18401\n", texto)

    def test_host_loopback(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main(["generate", "--participant-id", "acme", "--host", "localhost",
                          "--country", "esp", "--entity-type", "ngo", "--registry-url", "https://r/"])


if __name__ == "__main__":
    unittest.main()
