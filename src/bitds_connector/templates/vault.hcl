# Everything written here is encrypted: the directory is useless without the unseal key.
storage "file" {
  path = "/vault/file"
}

# Only reachable inside the compose network, so no TLS.
listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

ui = false
disable_mlock = true
api_addr = "http://vault:8200"
