#!/usr/bin/env bash
set -euo pipefail
image=${1:?image path required}
workspace=${2:?mount point required}
owner=${3:?WSL user required}
[[ $EUID == 0 ]] || { echo 'Run the mount helper as root.' >&2; exit 1; }
[[ $image == /mnt/* && $workspace == /home/*/mpv-build && -f $image ]] || exit 1
mkdir -p "$workspace"
if mountpoint -q "$workspace"; then
    device=$(findmnt -n -o SOURCE --target "$workspace")
    backing=$(losetup -n -O BACK-FILE "$device")
    [[ $(realpath "$backing") == "$(realpath "$image")" ]] || { echo 'Another filesystem occupies the workspace.' >&2; exit 1; }
    exit 0
fi
if [[ -n $(ls -A "$workspace") ]]; then
    echo 'Refusing to hide files beneath the workspace mount.' >&2
    exit 1
fi
# Only an explicitly marked, newly allocated image may be formatted.
if [[ -f $image.unformatted ]]; then
    [[ ! $(blkid -p -o value -s TYPE "$image" || true) ]] || exit 1
    mkfs.ext4 -q -m 0 -L mpv-build "$image"
    rm -- "$image.unformatted"
fi
[[ $(blkid -p -o value -s TYPE "$image") == ext4 ]] || exit 1
mount -o loop,noatime "$image" "$workspace"
chown "$owner:$(id -gn "$owner")" "$workspace"
