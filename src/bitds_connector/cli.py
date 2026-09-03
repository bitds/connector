"""Punto de entrada unico: connector generate | up | info."""

import argparse

from bitds_connector import __version__, generate, info, up

DESCRIPCION = "Deploy and inspect a BITDS EDC participant connector."


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=generate.PROG, description=DESCRIPCION)
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    subs = p.add_subparsers(dest="comando", metavar="COMMAND")

    gen = subs.add_parser("generate", help="Write the connector configuration in this directory.")
    generate.add_arguments(gen)
    gen.set_defaults(func=generate.run)

    arriba = subs.add_parser("up", help="Start the connector and wait until it is really running.")
    up.add_arguments(arriba)
    arriba.set_defaults(func=up.run)

    datos = subs.add_parser("info", help="Show the details of the connector in this directory.")
    datos.set_defaults(func=info.run)

    return p


def main(argv: list[str] | None = None) -> None:
    p = build_parser()
    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        raise SystemExit(1)
    args.func(args)
