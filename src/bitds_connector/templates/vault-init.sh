#!/bin/sh
# Prepares the Vault of this deployment. Runs on every `up`, and is safe to run again.
#   - first run: initializes it and writes the unseal key and the root token to vault/keys/init.txt
#   - every run: unseals it (Vault always starts sealed)
#   - makes sure KV v2 is mounted at secret/ and that the IdentityHub token exists
set -eu

KEYS_FILE=/vault/keys/init.txt
# The IdentityHub token is read from its own configuration, so it is defined in one place only.
EDC_TOKEN=$(grep '^edc.vault.hashicorp.token=' /run/secrets/identityhub_config_secret | cut -d= -f2-)
[ -n "$EDC_TOKEN" ] || { echo "[vault-init] ERROR: no edc.vault.hashicorp.token in the IdentityHub configuration"; exit 1; }
export VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"

echo "[vault-init] waiting for vault at $VAULT_ADDR ..."
# 'vault status' exits 0 when unsealed, 2 when sealed, 1 when it cannot be reached.
until vault status >/dev/null 2>&1 || [ $? -eq 2 ]; do sleep 1; done

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

if vault status | grep -q "Sealed.*true"; then
  echo "[vault-init] unsealing"
  vault operator unseal "$UNSEAL_KEY" >/dev/null
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

echo "[vault-init] done"
