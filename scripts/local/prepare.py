#!/usr/bin/env python3
"""Create an isolated, pinned copy of the existing GPL/shared recipes for WSL."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

UPSTREAM = '1f8d2e76255abc48f0456d9be99b4cab9c6566e6'


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def main():
    repo, workspace = map(Path, sys.argv[1:3])
    recipe = workspace / 'recipes'
    patches = [repo / 'patch_pr/0000-mpv-add-patch.patch',
               *sorted((repo / 'patch').glob('*.patch')),
               repo / 'update-openssl.patch', repo / 'fix-ffmpeg-opengl.patch',
               repo / 'compile-library-mpv.patch', repo / 'build-shared-ffmpeg.patch']
    fingerprint = hashlib.sha256()
    for path in [*patches, repo / 'scripts/check-profile.py', Path(__file__)]:
        fingerprint.update(path.name.encode())
        fingerprint.update(path.read_bytes().replace(b'\r\n', b'\n'))
    signature = fingerprint.hexdigest()
    marker = recipe / '.local-profile.json'
    if recipe.exists():
        if not marker.exists() or json.loads(marker.read_text())['recipes'] != signature:
            raise SystemExit('Recipe setup differs or was interrupted. Keep this workspace for edits; use another Storage directory for a fresh setup.')
        print(f'Using existing recipes: {recipe}')
        return
    # Windows Git checkouts can use CRLF. Linux git apply expects LF context.
    staged = workspace / 'recipe-patches'
    staged.mkdir(exist_ok=True)
    for patch in patches:
        (staged / patch.name).write_text(patch.read_text(encoding='utf-8'), encoding='utf-8')
    run('git', 'init', str(recipe))
    run('git', 'remote', 'add', 'origin', 'https://github.com/shinchiro/mpv-winbuild-cmake.git', cwd=recipe)
    run('git', 'fetch', '--depth=1', 'origin', UPSTREAM, cwd=recipe)
    run('git', 'checkout', '--detach', 'FETCH_HEAD', cwd=recipe)
    run('git', 'config', 'user.name', 'Local MPV build', cwd=recipe)
    run('git', 'config', 'user.email', 'local-build@localhost', cwd=recipe)
    for name, pattern in [('CMakeLists.txt', 'ccache_conf.in'), ('exec.in', 'ccache')]:
        path = recipe / name
        path.write_text(''.join(line for line in path.read_text().splitlines(True) if pattern not in line))
    run('git', 'apply', '--recount', str(staged / patches[0].name), cwd=recipe)
    run('git', 'commit', '-am', 'build: prepare local patch registration', cwd=recipe)
    for patch in patches[1:]:
        run('git', 'am', '--3way', str(staged / patch.name), cwd=recipe)
    for patch in sorted((repo / 'mpv-patches').glob('mpv-*.patch')):
        (recipe / 'packages' / patch.name).write_text(patch.read_text(encoding='utf-8'), encoding='utf-8')
    run(sys.executable, str(repo / 'scripts/check-profile.py'), str(recipe), 'gpl-shared')

    # Only local build mechanics change. Keep the original feature/linkage options.
    path = recipe / 'CMakeLists.txt'
    text = path.read_text().replace('set(MAKEJOBS ${CPU_COUNT})',
                                   'if(NOT DEFINED MAKEJOBS)\n    set(MAKEJOBS ${CPU_COUNT})\nendif()')
    text += '''
# Expose persistent build locations without guessing ExternalProject paths.
ExternalProject_Add_StepTargets(mpv configure)
ExternalProject_Add_StepTargets(ffmpeg configure)
ExternalProject_Get_Property(mpv SOURCE_DIR BINARY_DIR)
file(WRITE "${CMAKE_BINARY_DIR}/local-paths.sh" "mpv_source='${SOURCE_DIR}'\\nmpv_build='${BINARY_DIR}'\\n")
ExternalProject_Get_Property(ffmpeg SOURCE_DIR BINARY_DIR)
file(APPEND "${CMAKE_BINARY_DIR}/local-paths.sh" "ffmpeg_source='${SOURCE_DIR}'\\nffmpeg_build='${BINARY_DIR}'\\n")
'''
    path.write_text(text)
    # Never expose MPV's cleanup/reset/packaging targets in a development build.
    path = recipe / 'packages/mpv.cmake'
    text = path.read_text().split('ExternalProject_Add_Step(mpv strip-binary', 1)[0]
    text = text.replace('-Db_lto=true', '-Db_lto=false').replace('${mpv_lto_mode}', '')
    text = text.replace('GIT_REPOSITORY https://github.com/mpv-player/mpv.git', 'DOWNLOAD_COMMAND ""')
    text = text.replace('    GIT_CLONE_FLAGS ""\n', '')
    text = text.replace('    PATCH_COMMAND ${EXEC} git am --3way ${CMAKE_CURRENT_SOURCE_DIR}/mpv-*.patch\n', '')
    path.write_text(text)
    path = recipe / 'packages/ffmpeg.cmake'
    path.write_text(path.read_text().replace('cleanup(ffmpeg install)', '# Retain FFmpeg objects for incremental local builds.'))
    path = recipe / 'toolchain/rustup.cmake'
    # Rust is installed once by setup, scoped to this workspace, for this target.
    path.write_text('''ExternalProject_Add(rustup
    DOWNLOAD_COMMAND "" UPDATE_COMMAND "" CONFIGURE_COMMAND ""
    BUILD_COMMAND ${EXEC} rustup run nightly rustc --version
    INSTALL_COMMAND "")
''')
    # The runtime sources are large. A shallow, filtered checkout avoids LLVM history.
    path = recipe / 'toolchain/llvm/llvm.cmake'
    text = path.read_text().replace('GIT_CLONE_FLAGS "--sparse --filter=tree:0"',
                                   'GIT_SHALLOW TRUE\n    GIT_CLONE_FLAGS "--sparse --filter=blob:none"')
    path.write_text(text)
    run('git', 'add', '.', cwd=recipe)
    run('git', 'commit', '-m', 'build: retain local objects and use installed development tools', cwd=recipe)
    source = workspace / 'src_packages/mpv'
    if source.exists() and any(source.iterdir()):
        raise SystemExit(f'Existing MPV sources at {source}; refusing to replace or patch them automatically.')
    run('git', 'clone', '--depth=1', 'https://github.com/mpv-player/mpv.git', str(source))
    run('git', 'config', 'user.name', 'Local MPV build', cwd=source)
    run('git', 'config', 'user.email', 'local-build@localhost', cwd=source)
    upstream_mpv = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    for patch in sorted((recipe / 'packages').glob('mpv-*.patch')):
        run('git', 'am', '--3way', str(patch), cwd=source)
    marker.write_text(json.dumps({'recipes': signature, 'upstream': UPSTREAM,
                                 'profile': 'gpl-shared', 'cpu': 'x86-64-v2',
                                 'mpv_lto': False, 'mpv_upstream': upstream_mpv,
                                 'native_patches': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                    for p in sorted((recipe / 'packages').glob('mpv-*.patch'))}}, indent=2) + '\n')


if __name__ == '__main__':
    main()
