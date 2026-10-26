# connector

Command-line tool to deploy your own bitDS connector on your own server, without copying configuration files by hand.

It ships as a single 16 KB **zipapp** with no third-party dependencies. No `pip`, virtualenv or root needed to run it.

## Requirements

- Docker with the Compose plugin (`docker compose`)
- Python 3.10 or newer

## Quick start

```bash
# Download and verify
curl -LO https://github.com/bitds/connector/releases/latest/download/connector.pyz
curl -LO https://github.com/bitds/connector/releases/latest/download/connector.pyz.sha256
sha256sum -c connector.pyz.sha256

# Install
chmod +x connector.pyz
sudo install -m 755 connector.pyz /usr/local/bin/connector

# Create the deployment directory and deploy
mkdir ~/connector && cd ~/connector
connector generate --participant-id inferia --host 203.0.113.10 \
  --country esp --entity-type public \
  --registry-url https://registry.bitds.eu/api/
connector up
```

`up` finishes by printing the **Ed25519 public key**: send it to BITDS to register the participant.

> **The current directory is the deployment.** Every command reads and writes the directory it is run from; the tool takes no path argument. If you don't install it system-wide, call it by its path from inside the deployment directory (`~/downloads/connector.pyz generate ...`), never from the directory holding the `.pyz`.

## Commands

| Command | What it does |
|---|---|
| `generate` | Writes the configuration files into the current directory |
| `up` | Pulls the images, starts the stack and waits until the connector is actually running |
| `info` | Prints identity, ports, API key, claims and public key |

Run `connector <command> --help` for all options.

### `generate` options

| Option | Description |
|---|---|
| `--participant-id` | Short participant name, e.g. `inferia` |
| `--host` | Public IP or domain of this server (loopback addresses are rejected) |
| `--country` | ISO alpha-3 code; `--list-countries` shows the 27 EU values |
| `--entity-type` | `public`, `private` or `ngo` |
| `--registry-url` | BITDS participant registry URL (ask the dataspace authority) |
| `--location` | Location claim. Default: `eu` |
| `--port-base` | Port block base. Default: the one already deployed here, or the first free block |
| `--image` | Connector image. Default: `ghcr.io/bitds/provider-base:latest` (public) |
| `--force` | Regenerate without asking |

Ports are picked automatically: for base 18000 the block is `18001/18003/18004/18101`; if any is taken, the next base (+200) is tried. With an explicit `--port-base`, a busy port is an error.

### `info` example

```txt
connector details
  Status                running
  Participant DID       did:web:bitds.eu:participant:paris:public:did
  Hostname (URL)        http://152.89.166.16
  Callback address DSP  http://152.89.166.16:18404/protocol

Ports
  HTTP port             18401
  Management port       18403
  Protocol port         18404
  Public port           18501

Credentials
  API key               4d98a32b...

Claims
  country               fra
  location              eu
  entity_type           private

Public key
  send this to BITDS to register the participant
  MCowBQYDK2VwAyEAVxaUv5WjsjQldSda67EEp4RFhU+uEL6osYRYWRE80sI=
```

## Stopping, restarting and logs

The deployment is a regular Compose project, so any `docker compose` command works from the deployment directory. PostgreSQL runs in the same project on a named volume (reachable as `db:5432`, not published to the host).

- `docker compose down` keeps the volume, configuration and keys. Bring it back with `connector up` (not `docker compose up -d`) to get the readiness wait and diagnostics.
- `docker compose down -v` **also deletes the database**: assets, contract definitions, policies and negotiations. Only needed when regenerating an already-run directory, since `generate` issues a new DB password.

## Files in the deployment directory

```
provider_config_secret.properties   connector configuration (mode 600)   ← generate
claims.json                         participant claims                   ← generate
docker-compose.yml                  connector + PostgreSQL               ← generate
vptoken.txt                         empty; filled in by the connector    ← generate
ed25519_private.pem                 private key (mode 600, root)         ← up
ed25519_public.pem                  public key                           ← up
```

The private key never leaves the server; only the public key is sent to BITDS.
