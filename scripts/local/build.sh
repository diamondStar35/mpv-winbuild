#!/usr/bin/env bash
set -euo pipefail
action=${1:-status}
workspace=${2:?workspace required}
repo=${3:?repository required}
jobs=${4:-4}
artifacts=${5:?Windows-accessible artifact directory required}
scripts=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ $EUID != 0 ]] || { echo 'Run builds as your regular WSL user.' >&2; exit 1; }
mountpoint -q "$workspace" || { echo 'Workspace is not mounted. Use build.ps1.' >&2; exit 1; }
[[ $workspace != *[[:space:]]* && $repo != *[[:space:]]* ]] || { echo 'The upstream recipes require paths without whitespace.' >&2; exit 1; }
[[ $jobs =~ ^[1-9][0-9]*$ ]] || exit 1
mkdir -p "$workspace/logs" "$workspace/tmp"
exec 9>"$workspace/.build.lock"
flock -n 9 || { echo 'Another local build is running in this workspace.' >&2; exit 1; }
export TMPDIR=$workspace/tmp
export PATH="$workspace/tools/bin:/usr/lib/llvm-23/bin:$PATH"
export RUSTUP_HOME=$workspace/install_rustup/.rustup
export CARGO_HOME=$workspace/install_rustup/.cargo
export PATH="$CARGO_HOME/bin:$PATH"
export CCACHE_DIR=$workspace/ccache
export CCACHE_MAXSIZE=2G
export CMAKE_BUILD_PARALLEL_LEVEL=$jobs
export CARGO_BUILD_JOBS=$jobs
export MAKEFLAGS=-j$jobs
export MPV_BUILD_JOBS=$jobs
build=$workspace/build64-v2
prefix=$build/install/x86_64-w64-mingw32
mpv_source='' mpv_build='' ffmpeg_source='' ffmpeg_build=''
exec > >(tee -a "$workspace/logs/$action.log") 2>&1
printf '\n%s: %s (%s jobs)\n' "$(date -Iseconds)" "$action" "$jobs"

prepare() {
    [[ -x $workspace/tools/bin/meson && -x $CARGO_HOME/bin/rustup ]] || { echo 'Run setup first.' >&2; exit 1; }
    install -m 755 "$scripts/ninja.sh" "$workspace/tools/bin/ninja"
    python3 "$scripts/prepare.py" "$repo" "$workspace"
    bash "$scripts/bootstrap-clang.sh" "$workspace"
    cmake -S "$workspace/recipes" -B "$build" -G Ninja \
        -DTARGET_ARCH=x86_64-w64-mingw32 -DGCC_ARCH=x86-64-v2 -DCOMPILER_TOOLCHAIN=clang \
        -DCMAKE_INSTALL_PREFIX="$workspace/clang_root" -DMINGW_INSTALL_PREFIX="$prefix" \
        -DSINGLE_SOURCE_LOCATION="$workspace/src_packages" -DRUSTUP_LOCATION="$workspace/install_rustup" \
        -DMPV_PACKAGE_SUFFIX=-gpl-shared -DENABLE_CCACHE=ON -DCLANG_PACKAGES_LTO=ON \
        -DLLD_LTO_ALL_THREADS=OFF -DLLD_FLAGS="--threads=$jobs" -DMAKEJOBS="$jobs"
    printf '%s\n' "$jobs" > "$build/local-jobs"
}
toolchain() {
    # Download runtime sources only; do not build the LLVM compiler itself.
    ninja -C "$build" -j 1 llvm-download
    ninja -C "$build" -j 1 llvm-clang
    printf '#include <iostream>\nint main(){std::cout << "MPV cross compiler";}\n' > "$workspace/tmp/compiler-check.cpp"
    "$build/exec" x86_64-w64-mingw32-g++ "$workspace/tmp/compiler-check.cpp" -o "$workspace/tmp/compiler-check.exe"
    file "$workspace/tmp/compiler-check.exe"
}
case "$action" in
    setup)
        if [[ ! -x $workspace/tools/bin/python ]]; then python3 -m venv "$workspace/tools"; fi
        "$workspace/tools/bin/python" -m pip install --upgrade 'cmake>=3.31,<4' 'meson>=1.9,<2' packaging
        if [[ ! -x $CARGO_HOME/bin/rustup ]]; then
            curl --fail --location --retry 3 https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init -o "$TMPDIR/rustup-init"
            chmod +x "$TMPDIR/rustup-init"
            "$TMPDIR/rustup-init" -y --no-modify-path --profile minimal --default-toolchain nightly --target x86_64-pc-windows-gnu,x86_64-pc-windows-gnullvm
        else
            rustup toolchain install nightly --profile minimal
            rustup target add --toolchain nightly x86_64-pc-windows-gnu x86_64-pc-windows-gnullvm
        fi
        rustup default nightly
        prepare
        ;;
    prepare) prepare ;;
    toolchain|dependencies|mpv|ffmpeg)
        if [[ ! -f $build/local-paths.sh || ! -f $build/local-jobs ]] || [[ $(cat "$build/local-jobs") != "$jobs" ]]; then prepare; fi
        # shellcheck source=/dev/null
        source "$build/local-paths.sh"
        : "${mpv_source:?}" "${mpv_build:?}" "${ffmpeg_source:?}" "${ffmpeg_build:?}"
        if [[ ! -f $workspace/.toolchain-ready ]]; then
            toolchain
            touch "$workspace/.toolchain-ready"
        fi
        if [[ $action == toolchain ]]; then exit 0; fi
        if [[ $action == dependencies || ! -f $mpv_build/build.ninja ]]; then
            # Serialize packages; each package may use up to $jobs compiler tasks.
            ninja -C "$build" -j 1 mpv-configure
        fi
        if [[ $action == ffmpeg ]]; then
            (cd "$ffmpeg_build"; "$build/exec" make -j "$jobs"; "$build/exec" make install)
        elif [[ $action == mpv ]]; then
            # Direct Ninja invocation notices edits, without resetting or cleaning sources.
            "$build/exec" LTO_JOB=1 PDB=1 ninja -C "$mpv_build" -j "$jobs"
        fi
        ;;
    package)
        # shellcheck source=/dev/null
        source "$build/local-paths.sh"
        [[ -f $mpv_build/libmpv-2.dll ]] || { echo 'Build MPV first.' >&2; exit 1; }
        destination=$artifacts/$(date +%Y%m%d-%H%M%S)-$(git -C "$mpv_source" rev-parse --short HEAD)
        mkdir -p "$destination/include/mpv"
        cp "$mpv_build/libmpv-2.dll" "$mpv_build/libmpv.dll.a" "$destination/"
        cp "$prefix/bin/ffmpeg.exe" "$destination/"
        for library in avcodec avdevice avfilter avformat avutil swresample swscale; do
            matches=("$prefix/bin/$library"-*.dll "$prefix/bin/lib$library"-*.dll)
            found=false
            for dll in "${matches[@]}"; do
                [[ -f $dll ]] || continue
                cp "$dll" "$destination/"
                found=true
            done
            $found || { echo "Missing FFmpeg DLL: $library" >&2; exit 1; }
        done
        for symbols in "$mpv_build"/*.pdb; do [[ ! -f $symbols ]] || cp "$symbols" "$destination/"; done
        cp "$mpv_source/include/mpv/"*.h "$destination/include/mpv/"
        cp "$workspace/recipes/.local-profile.json" "$destination/profile.json"
        git -C "$mpv_source" rev-parse HEAD > "$destination/mpv-revision.txt"
        git -C "$mpv_source" diff --binary HEAD > "$destination/mpv-working-tree.patch"
        git -C "$mpv_source" status --short > "$destination/mpv-status.txt"
        git -C "$workspace/src_packages/ffmpeg" rev-parse HEAD > "$destination/ffmpeg-revision.txt"
        clang --version > "$destination/compiler.txt"
        sha256sum "$destination/"*.dll "$destination/ffmpeg.exe" > "$destination/SHA256SUMS"
        echo "Artifacts: $destination"
        ;;
    status)
        df -h "$workspace"
        for tool in clang cmake meson ninja nasm ccache; do
            if command -v "$tool" > /dev/null; then "$tool" --version | head -n 1; else echo "$tool: missing"; fi
        done
        [[ ! -x $CARGO_HOME/bin/rustup ]] || rustup run nightly rustc --version
        echo "Workspace: $workspace"
        if [[ -f $build/local-paths.sh ]]; then
            cat "$build/local-paths.sh"
            # shellcheck source=/dev/null
            source "$build/local-paths.sh"
            [[ ! -d $mpv_source/.git ]] || git -C "$mpv_source" status --short --branch
        fi
        ;;
    *) echo "Unknown action: $action" >&2; exit 1 ;;
esac
