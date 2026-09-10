#!/usr/bin/env bash
# Uses the instance role. No AWS credentials are read from an application env file.
set -Eeuo pipefail
umask 077
if [[ ${EUID} != 0 ]]; then
  printf 'Run this backup as root on the OrderToWork host.\n' >&2
  exit 1
fi
cd /opt/ordertowork
install -d -m 0700 /var/backups/ordertowork
exec 9>/var/backups/ordertowork/.backup.lock
flock -n 9 || exit 0
OTW_BACKUP_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OTW_BACKUP_FILE="/var/backups/ordertowork/ordertowork-${OTW_BACKUP_STAMP}.dump"
OTW_BACKUP_PART=$(mktemp /var/backups/ordertowork/.partial.XXXXXXXX)
trap 'rm -f -- "$OTW_BACKUP_PART"' EXIT
docker compose --env-file compose.env -f deploy/budget-compose.yaml exec -T db \
  pg_dump --username=otw_migrator --dbname=ordertowork --format=custom > "$OTW_BACKUP_PART"
test -s "$OTW_BACKUP_PART"
mv "$OTW_BACKUP_PART" "$OTW_BACKUP_FILE"
read -r OTW_BACKUP_REGION OTW_BACKUP_BUCKET < <(
  python3 deploy/budget-config.py backup-settings runtime.env
)
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE AWS_DEFAULT_PROFILE
export AWS_EC2_METADATA_DISABLED=false
aws s3 cp "$OTW_BACKUP_FILE" \
  "s3://${OTW_BACKUP_BUCKET}/backups/ordertowork-${OTW_BACKUP_STAMP}.dump" \
  --region "$OTW_BACKUP_REGION" --sse AES256 --only-show-errors
# Prune only this script's complete local dumps after a successful remote copy.
python3 - <<'PY'
import time
from pathlib import Path
cutoff = time.time() - 7 * 24 * 60 * 60
for path in Path('/var/backups/ordertowork').glob('ordertowork-*.dump'):
    if path.is_file() and path.stat().st_mtime < cutoff:
        path.unlink()
PY
printf 'Database backup uploaded successfully (%s UTC).\n' "$OTW_BACKUP_STAMP"
