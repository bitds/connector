# connector

A command-line tool that lets an external participant deploy their own bitDS connector on their own server, without having to copy configuration files  by hand.

It ships as a **zipapp**: a single 16 KB executable file. It does not bundle a Python interpreter, so the server needs Python 3.10 or newer, but it does not requires `pip`, or virtualenv an or root privileges.


**No third-party dependencies.** Everything is standard library. This is what  makes the 16 KB single-file build possible.

## Requirements

- Docker with the Compose plugin (`docker compose`)
- Python 3.10 or newer

## Quick start

Install the tool once, so it can be called from anywhere:

```bash
chmod +x connector.pyz
sudo install -m 755 connector.pyz /usr/local/bin/connector
```

Then create the deployment directory and work from inside it:

```bash
mkdir ~/connector && cd ~/connector    # this directory IS the deployment
```

This directory is the deployment: every file the connector needs is written here, and every later command must be run from inside it.

```bash
connector generate --participant-id inferia --host 203.0.113.10 \
  --country esp --entity-type public \
  --registry-url https://registry.bitds.eu/api/
```

`generate` writes the connector configuration into the current directory, using the identity of the participant (`--participant-id`), the public address of the server (`--host`), the country and the entity type of the organisation, and the registry the connector will talk to. Run `connector generate --help` to see every accepted argument.

```bash
connector up
```

`up` pulls the images, starts the stack and waits until the connector is genuinely running. It finishes by printing the participant public key.



Every command reads and writes the current working directory, that is where the configuration
is generated, where the keys end up, and which deployment `up` and `info` act on. **The tool itself takes no path argument.**

**Note** :  If you prefer not install it system-wide, keep the file wherever you like and call it by its path from inside the deployment directory:

```bash
cd ~/connector
~/downloads/connector.pyz generate --participant-id inferia ...
```

What you should not do is `cd` into the directory holding the `.pyz` and run it
there: the connector would be configured next to the tool instead of in your deployment directory.



`--host` must be the public IP address or domain name of the server. A loopback address is rejected: the connector would start normally but stay unreachable from the outside, which is a failure that surfaces late and is hard to diagnose.

`up` finishes by printing the **Ed25519 public key**. That key is what you send back to BITDS to register the participant.

## Commands

| Command | What it does |
|---|---|
| `generate` | Writes `provider_config_secret.properties`, `docker-compose.yml`, `claims.json` and an empty `vptoken.txt` into the current directory |
| `up` | Pulls the images, starts the stack and waits until the connector is genuinely running |
| `info` | Prints the connector details: identity, ports, API key, claims and public key |

To see the accepted country codes:

```bash
./connector.pyz generate --list-countries   # the 27 EU countries, ISO alpha-3
```

The connector image defaults to `ghcr.io/bitds/provider-base:latest`, a public
package that needs no registry credentials. `--image` overrides it if you ever need to deploy a specific build.

Run `./connector.pyz <command> --help` for the full set of options.

## Stopping, restarting and logs

The tool does not wrap Docker, and it does not need to because  the deployment is an ordinary Compose project so every `docker compose` command works from inside the deployment directory.



`down` removes the containers but not the named volume, nor the configuration, nor the keys, they are all still in the directory. To bring the connector back after a `down`, use `connector up` rather than `docker compose up -d`, so you get the wait and the diagnosis again instead of a command that returns before the connector has actually started.

There is one command that loses data:

```bash
docker compose down -v              # ALSO deletes the PostgreSQL volume
```

`-v` deletes the database volume, and with it the whole state of the connector assets, contract definitions, policies and negotiations. The Ed25519 keys are not in the volume, they are files in the deployment directory, so the participant identity survives even this. 

You need `-v`  when regenerating the configuration of a directory that has already run, because `generate` issues a new database password and the existing volume would keep rejecting it.

## What ends up in the directory

`generate` writes four files:

```
provider_config_secret.properties   connector configuration (mode 600)
claims.json                         participant claims
docker-compose.yml                  the stack: connector + PostgreSQL
vptoken.txt                         empty; filled in by the connector
```

`up` adds two more, created inside a container by the `key-init` service:

```
ed25519_private.pem                 participant private key (mode 600, owned by root)
ed25519_public.pem                  participant public key
```

The private key never leaves the server. Only the public key is sent to BITDS.

## How it works

The tool carries a base `.properties` file with placeholder values and rewrites about twenty keys with the config values for this particular deployment. 

`--host` value fills four of them
at once: `edc.hostname`, `edc.dsp.callback.address`,
`edc.dataplane.api.public.baseurl` and `edc.participant.eidas.url`.

**Ports are chosen automatically.** A block is derived from a base
(`18001`/`18003`/`18004`/`18101` for base 18000) and each published port is probed with `bind()`. If any is taken, the next base is tried, in steps of 200. Passing `--port-base` explicitly overrides this, and then an occupied block is an error rather than a reason to move on.


**PostgreSQL runs inside the same Compose project**, on a named volume. The connector reaches it as `db:5432` over the internal network, and its port is not published to the host.

## Distribution

Building produces two files:

```bash
./build.sh
# dist/connector.pyz          16K
# dist/connector.pyz.sha256
```

Publish both somewhere the participant can reach, then have them download it.
The checksum matters here: the participant is downloading an executable and running it, so they need a way to confirm it is the file you published.



### Downloading it on the target server

```bash
BASE=https://github.com/bitds/deploy-infrastructure/releases/download/connector-v1.0.0

curl -LO $BASE/connector.pyz
curl -LO $BASE/connector.pyz.sha256
sha256sum -c connector.pyz.sha256      # must print: connector.pyz: OK
chmod +x connector.pyz
```

To make it available as a normal command instead of a file in one directory:

```bash
sudo install -m 755 connector.pyz /usr/local/bin/connector
connector --help
```

Note that `up` and `info` act on the current working directory, so installing it
system-wide is convenient but does not change where the deployment lives.

