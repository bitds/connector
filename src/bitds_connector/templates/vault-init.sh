#!/bin/sh
# Prepares the Vault of this deployment and keeps it unsealed. Runs as a long-lived
# service (restart: unless-stopped), so Docker starts it again after a reboot.
#   - first run: initializes it and writes the unseal key and the root token to vault/keys/init.txt
#   - every start: unseals it (Vault always starts sealed), mounts KV v2 at secret/ and makes
#     sure the IdentityHub token exists. All of it is safe to run again.
#   - then: creates /tmp/ready (what the healthcheck looks for) and stays watching, unsealing
#     Vault again whenever it comes back sealed (a restart of the vault container).
set -eu

KEYS_FILE=/vault/keys/init.txt
READY=/tmp/ready
rm -f "$READY"

# The IdentityHub token is read from its own configuration, so it is defined in one place only.
EDC_TOKEN=$(grep '^edc.vault.hashicorp.token=' /run/secrets/identityhub_config_secret | cut -d= -f2-)
[ -n "$EDC_TOKEN" ] || { echo "[vault-init] ERROR: no edc.vault.hashicorp.token in the IdentityHub configuration"; exit 1; }
export VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"

wait_for_vault() {
  # 'vault status' exits 0 when unsealed, 2 when sealed, 1 when it cannot be reached.
  until vault status >/dev/null 2>&1 || [ $? -eq 2 ]; do sleep 1; done
}

is_sealed() {
  vault status 2>/dev/null | grep -q "Sealed.*true"
}

# Through the HTTP API with the body on stdin, not 'vault operator unseal <key>': process
# arguments are readable by any user of the host (ps, /proc/<pid>/cmdline), and the CLI only
# takes the key as an argument or typed on a terminal. printf is a shell builtin, so the key
# never appears on any command line.
unseal() {
  printf '{"key":"%s"}' "$UNSEAL_KEY" \
    | wget -q -O /dev/null --post-file=/dev/stdin "$VAULT_ADDR/v1/sys/unseal"
}

echo "[vault-init] waiting for vault at $VAULT_ADDR ..."
wait_for_vault

if vault status | grep -q "Initialized.*false"; then
  echo "[vault-init] not initialized yet: creating the unseal key and the root token"
  vault operator init -key-shares=1 -key-threshold=1 > "$KEYS_FILE"
  chmod 600 "$KEYS_FILE"
fi

if [ ! -f "$KEYS_FILE" ]; then
  echo "[vault-init] ERROR: vault is initialized but vault/keys/init.txt (the unseal key) is missing."
  echo "[vault-init] Restore it from a backup, or start over with 'docker compose down -v',"
  echo "[vault-init] which wipes vault and the database together."
  exit 1
fi

UNSEAL_KEY=$(grep 'Unseal Key 1:' "$KEYS_FILE" | awk '{print $NF}')
ROOT_TOKEN=$(grep 'Initial Root Token:' "$KEYS_FILE" | awk '{print $NF}')

if is_sealed; then
  echo "[vault-init] unsealing"
  unseal || { echo "[vault-init] ERROR: vault rejected the unseal key in vault/keys/init.txt"; exit 1; }
fi

export VAULT_TOKEN="$ROOT_TOKEN"

if ! vault secrets list | grep -q '^secret/'; then
  echo "[vault-init] mounting KV v2 at secret/"
  vault secrets enable -path=secret kv-v2
fi

# The IdentityHub only ever needs secret/, so that is all its policy allows.
vault policy write edc - <<'POLICY'
path "secret/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
POLICY

# Periodic: it does not expire while it is renewed, and EDC renews it.
if ! vault token lookup "$EDC_TOKEN" >/dev/null 2>&1; then
  echo "[vault-init] creating the IdentityHub token"
  vault token create -id="$EDC_TOKEN" -policy=edc -period=768h -orphan >/dev/null
fi

touch "$READY"
echo "[vault-init] done, watching vault to unseal it again if it restarts"

# From here on a failed check must not stop the service: vault may be down for a while.
set +e
while sleep 10; do
  if is_sealed; then
    echo "[vault-init] vault is sealed again: unsealing"
    unseal || echo "[vault-init] ERROR: unsealing failed, retrying in 10s"
  fi
done
