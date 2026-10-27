#!/usr/bin/env python3
"""Reject unintended feature or linkage changes in the generated build recipes."""

import argparse
from pathlib import Path
import re


def require(condition, message):
    if not condition:
        raise SystemExit(f"Profile validation failed: {message}")


def check(root, variant, build_dir=None):
    gpl = variant.startswith("gpl")
    shared = variant.endswith("-shared")
    mpv = (root / "packages/mpv.cmake").read_text(encoding="utf-8")
    ffmpeg = (root / "packages/ffmpeg.cmake").read_text(encoding="utf-8")
    archive = (root / "packages/libarchive.cmake").read_text(encoding="utf-8")
    options = dict(re.findall(r"-D([\w-]+)=([^\s]+)", mpv))
    expected = {
        "gpl": "true" if gpl else "false", "cplayer": "false", "libmpv": "true",
        "pdf-build": "disabled", "direct3d": "disabled", "vulkan": "disabled",
        "dvda": "enabled" if gpl else "disabled", "dvdnav": "enabled" if gpl else "disabled",
        "libarchive": "enabled", "libbluray": "enabled", "subrandr": "enabled",
        "lcms2": "enabled", "spirv-cross": "enabled", "libcurl": "enabled",
    }
    expected.update(dict.fromkeys(
        ["lua", "javascript", "sdl2-gamepad", "sdl2-audio", "sdl2-video", "openal", "vapoursynth", "sixel", "rubberband"],
        "disabled"))
    for name, value in expected.items():
        require(options.get(name) == value, f"mpv {name} must be {value}")
    require("--default-library=shared" in mpv, "libmpv must remain a shared library")
    require("mpv-package" not in mpv and "mpv-debug" not in mpv, "standalone player packaging remains")
    require(mpv.count("COMMAND ${RENAME}") == 1, "unexpected library package rename commands")
    require("${MPV_PACKAGE_SUFFIX}" in mpv, "missing variant package suffix")

    # The final occurrence wins, matching FFmpeg's configure argument handling.
    flags = {name: mode for mode, name in re.findall(r"--(enable|disable)-([\w-]+)", ffmpeg)}
    expected_ffmpeg = {
        "gpl": "enable" if gpl else "disable", "version3": "enable",
        "libbs2b": "enable", "libmysofa": "enable", "libsvtav1": "enable",
        "libdav1d": "enable", "libvpx": "enable", "libjxl": "enable",
        "libmp3lame": "enable", "libopus": "enable", "libvorbis": "enable",
        "libzimg": "enable", "libass": "enable", "libaribcaption": "enable",
        "libbluray": "enable", "openssl": "enable", "libxml2": "enable",
        "runtime-cpudetect": "enable", "vulkan": "disable", "libplacebo": "disable",
    }
    for name in ["avisynth", "vapoursynth", "librubberband", "libaom", "openal", "sdl2"]:
        expected_ffmpeg[name] = "disable"
    for name in ["libdvdnav", "libdvdread", "libx264", "libx265", "libssh", "libsrt", "libzvbi"]:
        expected_ffmpeg[name] = "enable" if gpl else "disable"
    for name, value in expected_ffmpeg.items():
        require(flags.get(name) == value, f"FFmpeg {name} must be {value}")
    require(("--enable-shared" in ffmpeg) == shared, "wrong FFmpeg linkage")
    require(("--disable-static" in ffmpeg) == shared, "wrong FFmpeg archive policy")
    require("-DENABLE_LZO=" + ("ON" if gpl else "OFF") in archive, "wrong LZO licence profile")
    for name in ["decoders", "encoders", "demuxers", "muxers", "parsers", "bsfs", "protocols", "filters", "network", "hwaccels"]:
        require(f"--disable-{name}" not in ffmpeg, f"unapproved blanket removal: {name}")
    removed = {"luajit", "mujs", "libsdl2", "libsixel", "openal-soft", "vapoursynth", "avisynth-headers", "aom", "rubberband"}
    for name, recipe in [("MPV", mpv), ("FFmpeg", ffmpeg)]:
        dependencies = set(recipe.split("DEPENDS", 1)[1].split("GIT_REPOSITORY", 1)[0].split())
        require(not dependencies & removed, f"{name} still depends on {dependencies & removed}")
    require(len(re.findall(r"--disable-filter=", ffmpeg)) >= 30, "missing LGPL-native filter policy")

    if build_dir:
        # Upstream deletes build directories after installation. The tracked source configure
        # remains available, so check newly introduced GPL-only filters against this revision too.
        configure = (root / "src_packages/ffmpeg/configure").read_text(encoding="utf-8")
        for name, dependencies in re.findall(r'^(\w+)_filter_deps(?:_any)?="([^"]*)"', configure, re.M):
            if "gpl" in dependencies.split():
                require(f"--disable-filter={name}\n" in ffmpeg, f"new GPL-only filter requires review: {name}")
        if shared:
            installed = build_dir / "install"
            for library in ["avcodec", "avdevice", "avfilter", "avformat", "avutil", "swresample", "swscale"]:
                require(not list(installed.glob(f"**/lib/lib{library}.a")), f"stale static archive: {library}")
                require(list(installed.glob(f"**/bin/{library}-*.dll")) or list(installed.glob(f"**/bin/lib{library}-*.dll")), f"missing shared library: {library}")
    print(f"Validated {variant}: approved features, licence boundaries and {'shared' if shared else 'static'} FFmpeg linkage")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("variant", choices=["gpl", "gpl-shared", "lgpl", "lgpl-shared"])
    parser.add_argument("--build-dir", type=Path)
    args = parser.parse_args()
    check(args.root, args.variant, args.build_dir)
