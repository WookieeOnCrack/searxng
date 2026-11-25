ARG DEBIAN_CODENAME="bookworm"

FROM mcr.microsoft.com/devcontainers/base:$DEBIAN_CODENAME

ARG DEBIAN_CODENAME="bookworm"

RUN cat <<EOF > /etc/apt/sources.list.d/debian.sources
Types: deb
URIs: http://deb.debian.org/debian
Suites: $DEBIAN_CODENAME $DEBIAN_CODENAME-updates $DEBIAN_CODENAME-backports
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://security.debian.org/debian-security
Suites: $DEBIAN_CODENAME-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF

RUN apt-get update && \
    apt-get -y install python3 python3-venv valkey-server firefox-esr graphviz imagemagick librsvg2-bin fonts-dejavu shellcheck

# uv ffs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/workspaces/searxng/local/py3

RUN mkdir -p /home/vscode/.cache/uv && chown -R vscode:vscode /home/vscode/.cache
