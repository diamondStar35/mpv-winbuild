#!/usr/bin/env bash
set -euo pipefail
# ExternalProject recipes invoke Ninja directly, without CMake's parallel limit.
exec /usr/bin/ninja -j "${MPV_BUILD_JOBS:-4}" "$@"
