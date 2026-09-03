# Contexto: herramienta `connector` (bitDS)

Vengo de la sesion en `~/Documents/DSP_Identity_SPACE/deploy-infrastructure`,
donde esto vivia en el subdirectorio `connector/`. Ahora esta en su propia
carpeta. Esto es todo lo que hay que saber.

## Que es

CLI que permite a un participante externo desplegar su propio conector EDC de
bitDS en su servidor, sin copiar ficheros de configuracion a mano.

Se distribuye como **zipapp**: un unico ejecutable de ~16 KB (`connector.pyz`).
No lleva interprete dentro, asi que el servidor necesita **Python 3.10+**, pero
no necesita pip, ni venv, ni root. **Cero dependencias de terceros** — todo es
libreria estandar, y eso es justo lo que hace posible el binario de 16 KB. No
introducir dependencias sin motivo muy fuerte: rompe la premisa del diseno.

Requisitos en el servidor destino: Docker con el plugin Compose, y Python 3.10+.

## Estructura

```
build.sh                 empaqueta el zipapp + su SHA-256
README.md                documentacion para el participante
src/__main__.py          arranque del zipapp
src/bitds_connector/
    __init__.py          solo __version__ (build.sh sella aqui el tag)
    cli.py               parser unico: generate | up | info
    generate.py          294 lineas, el nucleo
    up.py                187 lineas, arranque y verificacion
    info.py              99 lineas, lectura de estado
    ui.py                56 lineas, colores ANSI a pelo
    templates/
        provider-base-configuration.properties   base de 53 lineas
        docker-compose.yml.tmpl                  stack conector + postgres
        claims.default.json                      3 claims por defecto
```

~811 lineas de codigo en total. No hay tests automatizados; la verificacion
esta en el smoke test del CI (ver mas abajo).

## Convenciones del proyecto (importante respetarlas)

- **Comentarios y docstrings en espanol, sin acentos** (ASCII puro).
- **Toda la salida al usuario en ingles** (mensajes, ayuda de argparse, campos).
- **Identificadores internos en espanol** (`derivar_puertos`, `esperar_a_running`,
  `comprobar_puertos`, `confirmar_sobrescritura`), pero la API publica de
  argparse en ingles (`--participant-id`, `--port-base`).
- Los comentarios explican **por que**, no que. Casi todos justifican una
  decision de diseno. Mantener ese registro al tocar codigo.

## Los tres comandos

### `generate`
Escribe 4 ficheros en el **directorio actual** y no toca Docker:
`provider_config_secret.properties` (modo 600, lleva API key y password de BD),
`claims.json`, `docker-compose.yml`, y `vptoken.txt` vacio.

Argumentos obligatorios: `--participant-id`, `--host`, `--registry-url`,
`--country` (ISO alpha-3 de los 27 de la UE), `--entity-type`
(public|private|ngo). Opcionales: `--image`, `--port-base`, `--location`
(default `eu`), `--force`, `--list-countries`.

Detalles que importan:
- Rechaza `--host` de loopback (localhost/127.0.0.1/0.0.0.0): el conector
  arrancaria pero quedaria inalcanzable, fallo que se manifiesta tarde.
- DID generado: `did:web:bitds.eu:participant:<name>:public:did`.
- API key = `secrets.token_hex(32)`, password de BD = `token_hex(16)`.
- **Puertos automaticos**: bloque derivado de una base con OFFSETS
  `{http:+1, management:+3, protocol:+4, control:+5, version:+6, public:+101}`.
  Solo se publican http/management/protocol/public; control y version viven
  dentro de la red de compose. Se prueban con `bind()`; si alguno esta ocupado
  se busca la siguiente base en pasos de 200, entre 18000 y 19999.
  Prioridad: `--port-base` explicito > la base ya desplegada aqui > primera libre.
  Reusar la previa es deliberado: regenerar no debe cambiarle los puertos a un
  conector ya registrado.
- Si ya hay un despliegue, avisa y pide confirmacion explicita ('yes'), porque
  se pierde la API key y la password de BD (habria que borrar el volumen con
  `down -v`). Las claves Ed25519 **no** se tocan: la identidad se conserva.
  Sin TTY aborta salvo `--force`.
- Imagen por defecto: `ghcr.io/bitds/provider-base:latest` (paquete publico,
  sin credenciales). Es una tag movil; el valor concreto queda congelado en el
  docker-compose.yml que se escribe.

### `up`
`docker compose pull` + `up -d` + espera activa. Lo que anade sobre compose:
- Si el registry no responde pero las imagenes estan en local, continua con un
  warning; solo es fatal si ademas falta alguna.
- **Espera a que el conector siga arriba**, no a verlo 'running' una vez. Usa
  `docker inspect` (no `compose ps --status running`) porque con
  `restart: unless-stopped` un contenedor que muere y revive pasa por 'running'
  entre reinicios. `RestartCount > 0` = fallo inmediato. Exige 2 sondeos
  estables seguidos. Timeout por defecto 120s (`--timeout`).
- Si falla, imprime las ultimas 10 lineas del log del servicio `provider`.
- Termina imprimiendo la **clave publica Ed25519**, que es lo que el
  participante envia a BITDS para registrarse.

### `info`
Lee el `.properties` y `claims.json` del directorio y muestra identidad,
puertos, API key, claims, clave publica y estado del contenedor. Funciona este
el conector arrancado o no. Es tambien el modulo que usan `generate` y `up`
para imprimir su resumen, para no duplicar el formato en tres sitios.

## Modelo mental clave

**El directorio de trabajo ES el despliegue.** La herramienta no acepta ningun
argumento de ruta. Todo se lee y escribe en el cwd. Instalarla en
`/usr/local/bin` es comodo pero no cambia donde vive el despliegue. El error
tipico del usuario es hacer `cd` al directorio del `.pyz` y ejecutarlo alli:
configuraria el conector junto a la herramienta.

## El stack (docker-compose.yml.tmpl)

Tres servicios:
- **db**: `postgres:16`, volumen nombrado `db-data`, healthcheck con
  `pg_isready`. Su puerto NO se publica; el conector la alcanza como `db:5432`.
- **key-init**: `alpine/openssl:latest`, `restart: "no"`. Monta `./:/identity` y
  genera las claves Ed25519 solo si no existen. El conector depende de el con
  `condition: service_completed_successfully`.
- **provider**: la imagen del conector, monta `./:/idenity`, publica los cuatro
  puertos, y recibe la config como **secret de compose** montado en
  `/run/secrets/provider_config_secret`.

La plantilla se rellena con `str.format()`, asi que **no puede contener ninguna
llave literal** aparte de los marcadores. Los marcadores van entrecomillados
para que el YAML siga siendo valido tal cual.

Ficheros que aparecen tras `up`: `ed25519_private.pem` (600, propiedad de root
porque lo crea un contenedor) y `ed25519_public.pem` (644). La privada nunca
sale del servidor.

## Gotchas que detecte leyendo el codigo

1. **Rutas relativas en el .properties**: `generate` escribe
   `edc.participant.claims=identity/claims.json` y lo mismo para las dos claves
   (relativas), mientras que la plantilla base deja
   `edc.participant.eidas=/identity/vptoken.txt` (absoluta) y `generate` no la
   toca. Las relativas solo resuelven bien si el WORKDIR de la imagen es `/`.
   Funciona hoy, pero es fragil: si alguien cambia el WORKDIR de
   `provider-base`, se rompe sin aviso claro. Merece unificarse a absolutas.
2. **`docker compose down -v` borra el volumen** y con el todo el estado del
   conector (assets, contract definitions, policies, negociaciones). Las claves
   sobreviven porque son ficheros del directorio, no del volumen. Hace falta
   `-v` justo cuando se regenera la config de un directorio ya usado, porque la
   nueva password de BD no casa con el volumen existente.
3. **No copiar `.venv/` ni `dist/`** al mover la carpeta. En el origen habia un
   `.venv` de 16 MB (Python 3.12, solo pip) que no pinta nada: el proyecto no
   tiene dependencias. `dist/` se reconstruye siempre.

## Build

`./build.sh [version]`. Version por orden de preferencia: argumento >
`$CONNECTOR_VERSION` > `git describe --tags --match 'connector-v*' --dirty` >
`0.0.0+dev`. Se le quita el prefijo `connector-v`.

Empaqueta una **copia** del `src/` en un tmpdir (no `src/` directo) para que
sellar la version no ensucie el arbol de trabajo. Usa `python3 -m zipapp` con
shebang `/usr/bin/env python3`. Produce `dist/connector.pyz` +
`dist/connector.pyz.sha256`.

El checksum importa de verdad: el participante se descarga un ejecutable y lo
corre; es lo unico que tiene para comprobar que es el que publicamos.

**Ojo en la carpeta nueva**: si no es un repo git con esos tags, `git describe`
falla en silencio y la version sale `0.0.0+dev`.

## CI (esto se queda en deploy-infrastructure)

El workflow `.github/workflows/connector.yml` vive en el repo original y sus
rutas asumen el subdirectorio `connector/`. Si esta carpeta pasa a ser su propio
repo, hay que reescribir: `paths: connector/**`, `connector/build.sh`,
`connector/dist/...` y la URL de descarga del README
(`github.com/bitds/deploy-infrastructure/releases/...`).

Como funciona hoy:
- **push a dev/main** (con cambios en `connector/**`) o workflow_dispatch:
  job `build` en matriz py3.10 + py3.13 — construye, verifica el SHA-256 y corre
  un smoke test que hace un `generate` real en un directorio temporal y
  comprueba que salen los ficheros, que el `.properties` es 600 y que
  `docker compose config -q` valida. Sube el `.pyz` como **artifact del run**
  (solo py3.13, retencion 14 dias). Eso NO es una release.
- **release published con tag `connector-v*`**: corre `build` y luego `publish`
  (`needs: build`), que reconstruye con `CONNECTOR_VERSION` = el tag y adjunta
  `.pyz` + `.sha256` a la release con `gh release upload --clobber`.
- El trigger es `release: types: [published]`, **no** un push de tag: `git push
  --tags` por si solo no publica nada. Hay que crear la release de verdad.
- Si el build falla en cualquiera de las dos versiones de Python, `publish` se
  salta y la release queda sin binario.

## Estado en el repo original

Todo (`connector/**` y el workflow) estaba **staged pero sin commitear** en la
rama `dev`. El `.gitignore` del repo tenia reglas especificas:
`connector/dist/`, `connector/*/` con `!connector/src/` (para que ningun
directorio de despliegue con claves privadas y API keys acabe en git) y
`.venv/`. **Esas reglas hay que rehacerlas en la carpeta nueva**, porque los
paths cambian y son las que impiden commitear secretos.
