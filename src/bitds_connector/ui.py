"""Colores y formato para la salida por consola.

Sin dependencias: codigos ANSI a pelo. Se desactivan solos cuando la salida no es
un terminal (por ejemplo al redirigir a un fichero o encadenar con un pipe), y
tambien si el usuario exporta NO_COLOR, que es la convencion habitual.
"""

import os
import sys

COLOR = (
    sys.stdout.isatty()
    and not os.environ.get("NO_COLOR")
    and os.environ.get("TERM") != "dumb"
)


def _estilo(codigo: str):
    def aplicar(texto) -> str:
        return f"\033[{codigo}m{texto}\033[0m" if COLOR else str(texto)
    return aplicar


bold = _estilo("1")
dim = _estilo("2")
red = _estilo("31")
green = _estilo("32")
yellow = _estilo("33")
cyan = _estilo("36")


def step(mensaje: str) -> None:
    """Un paso en curso."""
    print(f"{cyan('>')} {mensaje}")


def ok(mensaje: str) -> None:
    print(f"{green('OK')} {mensaje}")


def warn(mensaje: str) -> None:
    print(f"{yellow('!')}  {mensaje}", file=sys.stderr)


def fail(mensaje: str) -> None:
    """Aborta con un mensaje de error."""
    raise SystemExit(f"{red('ERROR')} {mensaje}")


def title(texto: str) -> None:
    print(f"\n{bold(texto)}")


def field(etiqueta: str, valor, ancho: int = 22) -> None:
    """Una linea 'etiqueta   valor' alineada."""
    print(f"  {dim(etiqueta.ljust(ancho))}{bold(valor)}")
