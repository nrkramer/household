FROM python:3.12-alpine

ARG BUILD_ARCH=amd64
ARG CLOUDFLARED_VERSION=2026.9.3
ARG CLOUDFLARED_SHA256_amd64=77e26d8d900e0b8469f416239d14b5f296525fdf79fee6f511ef55609e3fbac2
ARG CLOUDFLARED_SHA256_aarch64=aaeb2d7d0da3614634c7e03ab13487a1522c2e79165ed2929cfe23d5e95b326d

RUN set -eux; \
    case "${BUILD_ARCH}" in \
      amd64) arch=amd64; sha="${CLOUDFLARED_SHA256_amd64}" ;; \
      aarch64) arch=arm64; sha="${CLOUDFLARED_SHA256_aarch64}" ;; \
      *) echo "Unsupported arch ${BUILD_ARCH}"; exit 1 ;; \
    esac; \
    wget -q -O /usr/local/bin/cloudflared \
      "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-${arch}"; \
    echo "${sha}  /usr/local/bin/cloudflared" | sha256sum -c -; \
    chmod +x /usr/local/bin/cloudflared

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY integration/ /app/integration/
COPY app/ /app/household/

CMD ["python", "-m", "household"]
