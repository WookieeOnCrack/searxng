Keep your settings.yml and limiter.toml in this directory.

Include Caido proxy info and CA certificate in settings.yml.

```yaml
# Read the documentation before extending the defaults:
# https://docs.searxng.org/admin/settings/

use_default_settings: true
server:
    secret_key: "ultrasecretkey"   # change this!
    bind_address: "[::]"

outgoing:
  verify: /workspaces/searxng/.devcontainer/certs/caido-ca.crt
  proxies:
    all://:
      - http://caido:7000  # target the `caido` container in docker
``