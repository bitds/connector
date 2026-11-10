# connector

Command-line tool to deploy your own bitDS participant on your own server, without copying configuration files by hand.

The participant uses **DCP** (Decentralized Claims Protocol): its identity is a `did:web` DID published by its own IdentityHub, and other connectors check its verifiable credentials before they share data with it. One deployment runs the whole stack:

| Service | What it is |
|---|---|
| `provider` | the EDC connector |
| `identityhub` | DID document, Credential Service and STS of the participant |
| `vault` + `vault-init` | HashiCorp Vault holding the IdentityHub keys and secrets, kept unsealed by `vault-init` (also after a reboot) |
| `db` | PostgreSQL for the connector and the IdentityHub |
| `key-init` | creates the Ed25519 key pair the connector signs its EDR tokens with |

The tool ships as a single **zipapp** with no third-party dependencies. No `pip`, virtualenv or root needed to run it.

## Requirements

- Docker with the Compose plugin (`docker compose`)
- Python 3.10 or newer

## Quick start

The DCP version is published as a **pre-release**, so it is downloaded by its tag: `releases/latest` still points to the previous version, without DCP.

```bash
# Download and verify
curl -LO https://github.com/bitds/connector/releases/download/v0.2.0-dcp.1/connector.pyz
curl -LO https://github.com/bitds/connector/releases/download/v0.2.0-dcp.1/connector.pyz.sha256
sha256sum -c connector.pyz.sha256

# Install
chmod +x connector.pyz
sudo install -m 755 connector.pyz /usr/local/bin/connector
connector --version   # connector 0.2.0-dcp.1

# Create the deployment directory and deploy
mkdir ~/connector && cd ~/connector
connector generate --participant-id inferia --host 203.0.113.10 \
  --issuer-did did:web:issuer.example.org%3A7183:issuer
connector up
```

`up` starts vault and the IdentityHub, **registers the participant in its IdentityHub**, and only then starts the connector, because the connector needs the STS client secret the IdentityHub returns at registration. It finishes by printing the participant **DID**.

> **The current directory is the deployment.** Every command reads and writes the directory it is run from: the tool takes no path argument. If you don't install it system-wide, call it by its path from inside the deployment directory (`~/downloads/connector.pyz generate ...`), never from the directory holding the `.pyz`.

## Commands

| Command | What it does |
|---|---|
| `generate` | Writes the configuration files into the current directory |
| `up` | Pulls the images, starts the stack, registers the participant and waits until the connector is actually running |
| `info` | Prints DID, ports, keys, the Credential Service and Identity API URLs, and the state of the connector and the IdentityHub |

Run `connector <command> --help` for all options.

### `generate` options

| Option | Description |
|---|---|
| `--participant-id` | Short participant name, e.g. `inferia`: 1-32 lowercase letters, digits or hyphens |
| `--host` | Public IP or domain of this server (loopback addresses are rejected) |
| `--issuer-did` | DID of the trusted issuer of `MembershipCredential` (ask the dataspace authority) |
| `--port-base` | Port block base. Default: the one already deployed here, or the first free block |
| `--image` | Connector image. Default: `ghcr.io/bitds/provider-base:dcp-ffdfe0c` |
| `--ih-image` | IdentityHub image. Default: `ghcr.io/bitds/identity-hub:dcp-ffdfe0c` |
| `--force` | Regenerate without asking |

The DID is `did:web:<host>%3A<did port>:<participant-id>`, and its document is served at `http://<host>:<did port>/<participant-id>/did.json`.

### Ports

All ports come from one block: for base 18000 they are

| Port | Use | Published |
|---|---|---|
| 18001 | connector HTTP | yes |
| 18003 | connector Management API | yes |
| 18004 | connector DSP protocol | yes |
| 18101 | connector data plane (public) | yes |
| 18013 | IdentityHub Credential Service | yes |
| 18014 | IdentityHub DID document | yes |
| 18012 | IdentityHub Identity API (administration) | yes |

If any of them is taken, the next base (+200) is tried. With an explicit `--port-base`, a busy port is an error. Vault, PostgreSQL and the IdentityHub STS are not published: they are only reachable inside the Compose network.

The Identity API accepts the IdentityHub API key shown by `info`, which can administer every participant in the IdentityHub. Restrict its port with a firewall to the machines that need it.

The DID port is part of the DID, so changing `--host` or `--port-base` changes the identity of the participant.

### `info` example

```txt
Participant
  Connector             running
  IdentityHub           running
  DID (ID de conexión)  did:web:203.0.113.10%3A18014:inferia
  DID document          http://203.0.113.10:18014/inferia/did.json
  DSP callback          http://203.0.113.10:18004/protocol
  Trusted issuer        did:web:issuer.example.org%3A7183:issuer
  STS registered        yes

Ports
  HTTP port             18001
  Management port       18003
  Protocol port         18004
  Public port           18101
  IH credentials port   18013
  IH DID port           18014
  IH identity port      18012

Credentials
  Management API key    e38feddb...
  Identity Hub API key  c3VwZXItdXNlcg==.30zp...
  Credential service    http://203.0.113.10:18013/api/credentials
  Identity API          http://203.0.113.10:18012/api/identity
```

## Stopping, restarting and logs

The deployment is a regular Compose project, so any `docker compose` command works from the deployment directory.

- `docker compose down` keeps the volumes, configuration and keys. Bring it back with `connector up` (not `docker compose up -d`) to get the readiness wait and diagnostics.
- `docker compose down -v` **also deletes the database and the vault**: assets, contract definitions, policies, negotiations, and the participant in the IdentityHub with its keys and credentials. The next `connector up` registers the participant again, with new keys.
- Running `generate` again in a used directory issues a new management API key and keeps every other secret (database password, vault token, IdentityHub super-user key, STS client secret), so the existing data keeps working. `up` recreates the containers whose configuration changed.

## Files in the deployment directory

```
provider_config_secret.properties     connector configuration (mode 600)      ← generate (+ STS secret by up)
identityhub_config_secret.properties  IdentityHub configuration (mode 600)    ← generate
docker-compose.yml                    the stack                               ← generate
db-init.sql                           creates the IdentityHub database        ← generate
vault/config/vault.hcl                vault configuration                     ← generate
vault/vault-init.sh                   initializes vault and keeps it unsealed ← generate
vault/keys/init.txt                   vault unseal key and root token (600)   ← up
keys/ed25519_private.pem              EDR token private key (600, root)       ← up
keys/ed25519_public.pem               EDR token public key                    ← up
```

**After a reboot of the server** everything comes back by itself, as long as Docker starts on boot (`sudo systemctl enable docker`). Vault always starts sealed, so `vault-init` does not exit like `key-init`: it stays running and unseals vault every time it comes back sealed. That is why `docker compose ps` shows it as `running (healthy)`.

**Back up `vault/keys/init.txt`.** Vault starts sealed every time and this file is the only way to unseal it: without it, the keys of the participant in the IdentityHub are lost. The connector container only mounts `keys/`, never the vault keys.
