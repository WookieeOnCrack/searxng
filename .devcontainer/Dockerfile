ARG DEBIAN_CODENAME="bookworm"
FROM mcr.microsoft.com/devcontainers/base:${DEBIAN_CODENAME:-bookworm}

ARG DEBIAN_CODENAME="bookworm"
ARG TARGETARCH
ARG CHROME_VERSION=152
# :CHROME_VERSION: Pinned to match default (latest) fingerprint chrome version in curl_cffi
# :DEFAULT_CHROME: value -> https://github.com/lexiforest/curl_cffi/blob/main/curl_cffi/requests/impersonate.py#L90

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV RESOLUTION=1920x1080x24

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
    apt-get install -y --no-install-recommends python3 python3-venv valkey-server firefox-esr graphviz imagemagick librsvg2-bin fonts-dejavu shellcheck \
    #   Utilities
    jq xvfb \
    #   Chrome for Testing
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    #   Optional (Fingerprinting) / Development / Testing
    fonts-liberation \
    libayatana-appindicator3-1 \
    libc6 \
    libcairo2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgcc-s1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcursor1 \
    libxi6 \
    libxtst6 \
    lsb-release \
    #  Cleanup
    && rm -rf /var/lib/apt/lists/*


RUN set -eux; \
    # Map Docker TARGETARCH to Chrome Platform string
    if [ "$TARGETARCH" = "amd64" ]; then \
        CHROME_PLATFORM="linux64"; \
    elif [ "$TARGETARCH" = "arm64" ]; then \
        CHROME_PLATFORM="linux-arm64"; \
    else \
        echo "Unsupported architecture: $TARGETARCH" && exit 1; \
    fi; \
    \
    # Determine if CHROME_VERSION is a text channel or a version string/milestone
    # Matches strings like "Stable", "Dev", "Beta", "Canary" (case-insensitive checks)
    case "$(echo "$CHROME_VERSION" | tr '[:upper:]' '[:lower:]')" in \
        (stable|dev|beta|canary) \
            # Normalize channel case for the Google API (e.g., Stable, Dev)
            CHANNEL_NAME=$(echo "${CHROME_VERSION:0:1}" | tr '[:lower:]' '[:upper:]')$(echo "${CHROME_VERSION:1}" | tr '[:upper:]' '[:lower:]'); \
            API_URL="https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"; \
            echo "Fetching latest URL for channel: $CHANNEL_NAME on $CHROME_PLATFORM..."; \
            DOWNLOAD_URL=$(curl -s "$API_URL" | jq -r ".channels.$CHANNEL_NAME.downloads.chrome[] | select(.platform==\"$CHROME_PLATFORM\") | .url"); \
            ;; \
        (*) \
            # If it's a version/milestone (like 150), first check if it's a short milestone or specific full version
            if [ ${#CHROME_VERSION} -le 3 ]; then \
                # It's a milestone (e.g., 150). Fetch the precise version mapping first.
                API_URL="https://googlechromelabs.github.io/chrome-for-testing/latest-versions-per-milestone-with-downloads.json"; \
                DOWNLOAD_URL=$(curl -s "$API_URL" | jq -r ".milestones.\"$CHROME_VERSION\".downloads.chrome[] | select(.platform==\"$CHROME_PLATFORM\") | .url"); \
            else \
                # It's a full pinned version string (e.g., 150.0.1234.56)
                API_URL="https://googlechromelabs.github.io/chrome-for-testing/${CHROME_VERSION}.json"; \
                DOWNLOAD_URL=$(curl -s "$API_URL" | jq -r ".downloads.chrome[] | select(.platform==\"$CHROME_PLATFORM\") | .url"); \
            fi; \
            ;; \
    esac; \
    \
    # Validate that we successfully resolved a download string
    if [ -z "$DOWNLOAD_URL" ] || [ "$DOWNLOAD_URL" = "null" ]; then \
        echo "Error: Could not resolve download URL for Version/Channel: $CHROME_VERSION on Platform: $CHROME_PLATFORM" && exit 1; \
    fi; \
    \
    # 3. Download and unpack Chrome for Testing
    echo "Downloading Chrome from: $DOWNLOAD_URL"; \
    mkdir -p /opt/chromium; \
    curl -sSL "$DOWNLOAD_URL" -o /tmp/chrome.zip; \
    unzip -q /tmp/chrome.zip -d /tmp/cft; \
    mv /tmp/cft/chrome-$CHROME_PLATFORM/* /opt/chromium/; \
    rm -rf /tmp/chrome.zip /tmp/cft; \
    chmod +x /opt/chromium/chrome; \
    ln -sf /opt/chromium/chrome /usr/local/bin/chrome; \
    ln -sf /opt/chromium/chrome /usr/local/bin/google-chrome; \
    /opt/chromium/chrome --version
