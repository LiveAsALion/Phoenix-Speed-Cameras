#!/usr/bin/env bash
# getspeedshield.com — one-shot VPS deploy.
# Usage:  scp web/getspeedshield/{index.html,bootstrap.sh} <vps>:/tmp/
#         ssh <vps> 'sudo bash /tmp/bootstrap.sh'
# Idempotent: safe to re-run. Stages: files -> nginx HTTP -> certbot -> nginx HTTPS.
set -euo pipefail

DOMAIN=getspeedshield.com
WEBROOT=/var/www/getspeedshield
CONF=/etc/nginx/sites-available/getspeedshield
CERT_EMAIL=liveasalion@gmail.com   # Let's Encrypt expiry notices; change if desired

echo "== [1/5] site files =="
mkdir -p "$WEBROOT" /var/www/certbot
if [ -f /tmp/index.html ]; then
  cp /tmp/index.html "$WEBROOT/index.html"
elif [ ! -f "$WEBROOT/index.html" ]; then
  echo "ERROR: /tmp/index.html not found and no existing site file"; exit 1
fi
if [ -f /tmp/icon.png ]; then cp /tmp/icon.png "$WEBROOT/icon.png"; fi
if [ -f /tmp/qr-site.png ]; then cp /tmp/qr-site.png "$WEBROOT/qr-site.png"; fi

echo "== [2/5] nginx HTTP config =="
cat > "$CONF" << 'NGINX'
# getspeedshield.com — managed by bootstrap.sh (stage 1: HTTP)
map $http_user_agent $speedshield_store {
    default                  "";
    "~*android"              "intent://details?id=com.speedshield.app#Intent;scheme=market;package=com.android.vending;S.browser_fallback_url=https%3A%2F%2Fplay.google.com%2Fstore%2Fapps%2Fdetails%3Fid%3Dcom.speedshield.app;end";
    "~*(iphone|ipad|ipod)"   "https://apps.apple.com/app/id6784553439";
}
server {
    listen 80;
    listen [::]:80;
    server_name getspeedshield.com www.getspeedshield.com;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    root /var/www/getspeedshield;
    index index.html;
    access_log /var/log/nginx/getspeedshield_access.log;
    location = /go {
        access_log /var/log/nginx/getspeedshield_qr.log;
        if ($speedshield_store != "") { return 302 $speedshield_store; }
        return 302 /?$args;
    }
    location / { try_files $uri $uri/ =404; }
}
NGINX
ln -sf "$CONF" /etc/nginx/sites-enabled/getspeedshield
nginx -t
systemctl reload nginx
echo "HTTP site live."

echo "== [3/5] certbot =="
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  certbot certonly --webroot -w /var/www/certbot \
    -d "$DOMAIN" -d "www.$DOMAIN" \
    --non-interactive --agree-tos -m "$CERT_EMAIL" \
    --deploy-hook "systemctl reload nginx"
else
  echo "certificate already present, skipping issuance"
fi

echo "== [4/5] nginx HTTPS config =="
cat > "$CONF" << 'NGINX'
# getspeedshield.com — managed by bootstrap.sh (stage 2: HTTPS)
map $http_user_agent $speedshield_store {
    default                  "";
    "~*android"              "intent://details?id=com.speedshield.app#Intent;scheme=market;package=com.android.vending;S.browser_fallback_url=https%3A%2F%2Fplay.google.com%2Fstore%2Fapps%2Fdetails%3Fid%3Dcom.speedshield.app;end";
    "~*(iphone|ipad|ipod)"   "https://apps.apple.com/app/id6784553439";
}
server {
    listen 80;
    listen [::]:80;
    server_name getspeedshield.com www.getspeedshield.com;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://getspeedshield.com$request_uri; }
}
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name getspeedshield.com www.getspeedshield.com;

    ssl_certificate     /etc/letsencrypt/live/getspeedshield.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/getspeedshield.com/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Frame-Options            DENY always;
    add_header X-Content-Type-Options     nosniff always;
    add_header Referrer-Policy            strict-origin-when-cross-origin always;

    root /var/www/getspeedshield;
    index index.html;
    access_log /var/log/nginx/getspeedshield_access.log;

    location = /go {
        access_log /var/log/nginx/getspeedshield_qr.log;
        if ($speedshield_store != "") { return 302 $speedshield_store; }
        return 302 /?$args;
    }
    location / { try_files $uri $uri/ =404; }
}
NGINX
nginx -t
systemctl reload nginx

echo "== [5/5] smoke test =="
curl -sS -o /dev/null -w "https://$DOMAIN -> %{http_code}\n"        "https://$DOMAIN/" || true
curl -sS -o /dev/null -w "/go (android UA) -> %{http_code} %{redirect_url}\n" \
     -A "Mozilla/5.0 (Linux; Android 14)" "https://$DOMAIN/go?src=test" || true
curl -sS -o /dev/null -w "/go (iphone UA)  -> %{http_code} %{redirect_url}\n" \
     -A "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)" "https://$DOMAIN/go?src=test" || true
echo "DONE. QR log: /var/log/nginx/getspeedshield_qr.log"
