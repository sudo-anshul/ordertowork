#!/usr/bin/env bash
# Ubuntu 24.04 does not provide an awscli apt package. Install the official ARM CLI.
set -Eeuo pipefail
umask 077
if command -v aws >/dev/null 2>&1 && aws --version 2>&1 | grep -q '^aws-cli/2\.'; then
  exit 0
fi
if [[ ${EUID} != 0 || $(dpkg --print-architecture) != arm64 ]]; then
  printf 'AWS CLI bootstrap requires root on an ARM64 Ubuntu host.\n' >&2
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=120 update
apt-get -o DPkg::Lock::Timeout=120 install -y curl unzip ca-certificates
OTW_CLI_TEMP=$(mktemp -d)
trap 'rm -rf -- "$OTW_CLI_TEMP"' EXIT
curl --fail --silent --show-error --location --connect-timeout 10 --max-time 180 \
  https://awscli.amazonaws.com/awscli-exe-linux-aarch64-2.36.42.zip \
  --output "$OTW_CLI_TEMP/awscliv2.zip"
# SHA-256 verified from the official HTTPS release on 10 September 2026.
printf '%s  %s\n' cf64084aafa091b68392ca585c87325c67137ab6d740f9d43f12c55f7fd6b297 \
  "$OTW_CLI_TEMP/awscliv2.zip" | sha256sum --check -
unzip -q "$OTW_CLI_TEMP/awscliv2.zip" -d "$OTW_CLI_TEMP"
"$OTW_CLI_TEMP/aws/install" --install-dir /usr/local/aws-cli --bin-dir /usr/local/bin --update
aws --version
