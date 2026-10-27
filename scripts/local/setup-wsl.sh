#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run setup as root.' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
# shellcheck source=/dev/null
. /etc/os-release
[[ $ID == ubuntu && $VERSION_CODENAME == jammy ]] || { echo 'This setup currently supports Ubuntu 22.04 (Jammy).' >&2; exit 1; }
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl wget gnupg git build-essential \
    autoconf automake libtool libtool-bin pkgconf ninja-build nasm yasm \
    python3 python3-venv python3-pip texinfo bison flex gperf gettext autopoint \
    patch diffutils file unzip zip p7zip-full xz-utils zstd ccache shellcheck \
    libssl-dev libxml2-dev libzstd-dev libtinfo-dev libncurses-dev zlib1g-dev \
    libgmp-dev libmpfr-dev libmpc-dev libisl-dev
key=$(mktemp)
trap 'rm -f "$key"' EXIT
curl --fail --location --retry 3 https://apt.llvm.org/llvm-snapshot.gpg.key -o "$key"
gpg --batch --yes --dearmor -o /usr/share/keyrings/mpv-winbuild-llvm.gpg "$key"
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/mpv-winbuild-llvm.gpg] https://apt.llvm.org/jammy/ llvm-toolchain-jammy-23 main' > /etc/apt/sources.list.d/mpv-winbuild-llvm.list
apt-get update
apt-get install -y --no-install-recommends clang-23 clang-tools-23 lld-23 llvm-23 llvm-23-dev \
    libc++-23-dev libc++abi-23-dev libunwind-23-dev
apt-get clean
