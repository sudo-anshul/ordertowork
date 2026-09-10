#!/usr/bin/env bash
# Run on the EC2 host, from an extracted release containing deploy/budget-*.
set -Eeuo pipefail
umask 077

if [[ ${EUID} != 0 || $# != 1 ]]; then
  printf 'Usage: sudo bash deploy/budget-setup.sh /root/ordertowork-config.json\n' >&2
  exit 1
fi
OTW_CONFIG_FILE=$(realpath "$1")
OTW_DEPLOY_SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
OTW_INSTALL_DIR=/opt/ordertowork

# Only the documented Ubuntu 24.04 ARM host is supported by this bootstrap.
. /etc/os-release
if [[ ${ID} != ubuntu || ${VERSION_ID} != 24.04 || $(dpkg --print-architecture) != arm64 ]]; then
  printf 'This bootstrap requires Ubuntu 24.04 on ARM64.\n' >&2
  exit 1
fi
python3 "$OTW_DEPLOY_SOURCE/budget-config.py" validate "$OTW_CONFIG_FILE"
bash "$OTW_DEPLOY_SOURCE/budget-install-aws.sh"
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=120 update
apt-get -o DPkg::Lock::Timeout=120 install -y docker.io docker-compose-v2 ca-certificates
systemctl enable --now docker

if docker volume inspect ordertowork-budget-postgres >/dev/null 2>&1 && \
    [[ ! -f "$OTW_INSTALL_DIR/compose.env" ]]; then
  printf 'Existing database volume has no saved credentials. Restore compose.env before proceeding.\n' >&2
  exit 1
fi
install -d -m 0700 "$OTW_INSTALL_DIR" "$OTW_INSTALL_DIR/deploy" /var/backups/ordertowork
for OTW_RELEASE_FILE in budget-compose.yaml budget-Caddyfile budget-init-db.sh budget-config.py \
    budget-backup.sh budget-setup.sh budget-install-aws.sh; do
  if [[ "$OTW_DEPLOY_SOURCE/$OTW_RELEASE_FILE" != "$OTW_INSTALL_DIR/deploy/$OTW_RELEASE_FILE" ]]; then
    install -m 0600 "$OTW_DEPLOY_SOURCE/$OTW_RELEASE_FILE" "$OTW_INSTALL_DIR/deploy/$OTW_RELEASE_FILE"
  fi
done
# The PostgreSQL entrypoint reads this file as its own unprivileged OS user.
chmod 0644 "$OTW_INSTALL_DIR/deploy/budget-init-db.sh" "$OTW_INSTALL_DIR/deploy/budget-Caddyfile"
python3 "$OTW_INSTALL_DIR/deploy/budget-config.py" write "$OTW_CONFIG_FILE" "$OTW_INSTALL_DIR"

# A 1 GiB swap file on the encrypted root volume gives brief memory spikes room.
OTW_SWAP_FILE=/var/lib/ordertowork.swap
if [[ ! -f "$OTW_SWAP_FILE" ]]; then
  fallocate -l 1G "$OTW_SWAP_FILE"
  chmod 0600 "$OTW_SWAP_FILE"
  mkswap "$OTW_SWAP_FILE"
fi
if ! awk -v target="$OTW_SWAP_FILE" '$1 == target { found=1 } END { exit !found }' /proc/swaps; then
  swapon "$OTW_SWAP_FILE"
fi
if ! grep -Fq "$OTW_SWAP_FILE none swap sw 0 0" /etc/fstab; then
  printf '%s none swap sw 0 0\n' "$OTW_SWAP_FILE" >> /etc/fstab
fi

cd "$OTW_INSTALL_DIR"
OTW_COMPOSE=(docker compose --env-file compose.env -f deploy/budget-compose.yaml)
# Registry tokens are temporary and deleted after image pulls; SDKs use IMDSv2.
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE AWS_DEFAULT_PROFILE
export AWS_EC2_METADATA_DISABLED=false
OTW_DOCKER_AUTH=$(mktemp -d)
export DOCKER_CONFIG="$OTW_DOCKER_AUTH"
trap 'rm -rf -- "$OTW_DOCKER_AUTH"' EXIT
read -r OTW_ECR_REGION OTW_ECR_REGISTRY < <(
  python3 deploy/budget-config.py registry "$OTW_CONFIG_FILE"
)
aws ecr get-login-password --region "$OTW_ECR_REGION" |
  docker login --username AWS --password-stdin "$OTW_ECR_REGISTRY"
"${OTW_COMPOSE[@]}" --profile maintenance pull
"${OTW_COMPOSE[@]}" run --rm --no-deps caddy caddy validate \
  --config /etc/caddy/Caddyfile --adapter caddyfile

# Drain the only worker before changing schema; its lease protects interruptions.
"${OTW_COMPOSE[@]}" stop api worker
"${OTW_COMPOSE[@]}" up -d --wait --wait-timeout 150 db
if "${OTW_COMPOSE[@]}" exec -T db psql -U otw_migrator -d ordertowork -Atc \
    "SELECT to_regclass('public.alembic_version') IS NOT NULL" | grep -qx t; then
  bash deploy/budget-backup.sh
fi
"${OTW_COMPOSE[@]}" --profile maintenance run --rm --no-deps migrate
"${OTW_COMPOSE[@]}" up -d --wait --wait-timeout 150 api worker caddy

cat > /etc/systemd/system/ordertowork-backup.service <<'UNIT'
[Unit]
Description=OrderToWork encrypted off-instance database backup
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/bash /opt/ordertowork/deploy/budget-backup.sh
UMask=0077
UNIT
cat > /etc/systemd/system/ordertowork-backup.timer <<'UNIT'
[Unit]
Description=Back up OrderToWork twice daily

[Timer]
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=10m
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now ordertowork-backup.timer
"${OTW_COMPOSE[@]}" ps
printf 'Services started. Verify public HTTPS, Cognito sign-in, and a bounded Bedrock job before sharing.\n'
