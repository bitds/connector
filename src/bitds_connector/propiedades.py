"""Ficheros .properties: se leen como diccionario y se actualizan sin perder comentarios ni orden."""

from pathlib import Path


def leer(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    pares = (linea.partition("=") for linea in path.read_text().splitlines()
             if "=" in linea and not linea.lstrip().startswith("#"))
    return {clave.strip(): valor.strip() for clave, _, valor in pares}


def actualizar(path: str | Path, cambios: dict) -> None:
    """Sustituye las claves indicadas; las que no existan se anaden al final."""
    path = Path(path)
    lineas = path.read_text().splitlines()
    for clave, valor in cambios.items():
        nueva = f"{clave}={valor}"
        for i, linea in enumerate(lineas):
            if linea.startswith(f"{clave}="):
                lineas[i] = nueva
                break
        else:
            lineas.append(nueva)
    path.write_text("\n".join(lineas) + "\n")
