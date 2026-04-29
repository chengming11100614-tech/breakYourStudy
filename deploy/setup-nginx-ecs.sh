#!/usr/bin/env bash
set -euo pipefail

# Run on the ECS as root after project is at /root/breakYourStudy with venv + .env.
# Usage: bash deploy/setup-nginx-ecs.sh
# (from repo root, or: bash /root/breakYourStudy/deploy/setup-nginx-ecs.sh)

APP_ROOT="/root/breakYourStudy"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -x "${APP_ROOT}/.venv/bin/python" ]]; then
  echo "Missing venv at ${APP_ROOT}/.venv — create it and pip install -r requirements.txt first." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y nginx openssl

# Self-signed cert for https://公网IP (no domain). Browsers warn; safe for school demo / internal.
# Override: PUBLIC_IP=1.2.3.4 bash ...
SSL_DIR=/etc/nginx/ssl
mkdir -p "$SSL_DIR"
if [[ -n "${PUBLIC_IP:-}" ]]; then
  PUBIP="$PUBLIC_IP"
else
  PUBIP=$(curl -fsS --connect-timeout 2 http://100.100.100.200/latest/meta-data/eipv4 2>/dev/null || true)
fi
[[ -z "${PUBIP}" ]] && PUBIP=$(curl -fsS --connect-timeout 3 ifconfig.me 2>/dev/null || true)
if [[ -z "${PUBIP}" ]]; then
  echo "Could not detect public IP. Set PUBLIC_IP and run again, e.g.:" >&2
  echo "  PUBLIC_IP=106.15.199.202 bash ${0}" >&2
  exit 1
fi

if [[ ! -f "$SSL_DIR/breakyourstudy.crt" ]] || [[ ! -f "$SSL_DIR/breakyourstudy.key" ]]; then
  openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
    -keyout "$SSL_DIR/breakyourstudy.key" \
    -out "$SSL_DIR/breakyourstudy.crt" \
    -subj "/CN=${PUBIP}" \
    -addext "subjectAltName=IP:${PUBIP}"
  chmod 600 "$SSL_DIR/breakyourstudy.key"
  chmod 644 "$SSL_DIR/breakyourstudy.crt"
fi

install -m 0644 "${SCRIPT_DIR}/breakyourstudy.conf" /etc/nginx/sites-available/breakyourstudy

if [[ -e /etc/nginx/sites-enabled/default ]]; then
  rm -f /etc/nginx/sites-enabled/default
fi
ln -sf /etc/nginx/sites-available/breakyourstudy /etc/nginx/sites-enabled/breakyourstudy

nginx -t
systemctl enable nginx
systemctl reload nginx

install -m 0644 "${SCRIPT_DIR}/breakyourstudy.service" /etc/systemd/system/breakyourstudy.service
systemctl daemon-reload

# Free 7860 if you still have a manual `python app.py` from SSH (otherwise bind fails)
if command -v fuser >/dev/null 2>&1; then
  fuser -k 7860/tcp 2>/dev/null || true
  sleep 1
fi

if systemctl is-active --quiet breakyourstudy 2>/dev/null; then
  systemctl restart breakyourstudy
else
  systemctl enable --now breakyourstudy
fi

echo "Done. Security group should allow TCP 80 and 443 to this ECS."
echo "HTTP:  http://${PUBIP}/"
echo "HTTPS: https://${PUBIP}/ (self-signed: browser shows a warning → Advanced → proceed)"
echo "With a domain later: apt install -y certbot python3-certbot-nginx && certbot --nginx -d your.domain"
