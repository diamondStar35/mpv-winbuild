# Local Windows builds in WSL

Run these commands in PowerShell from `D:\mpv-winbuild`. The GitHub workflows
are unchanged. Setup supports the installed `Ubuntu-22.04` WSL2 distribution.

```powershell
.\scripts\local\build.ps1 -Action setup
.\scripts\local\build.ps1 -Action toolchain
.\scripts\local\build.ps1 -Action mpv
.\scripts\local\build.ps1 -Action package
```

`setup` installs Linux build prerequisites, LLVM/Clang/LLD 23 from the signed
apt.llvm.org repository, modern CMake/Meson in a Python virtual environment,
and workspace-local Rust nightly with the Windows GNU and GNU LLVM targets. It also prepares
an editable MPV checkout with the four native patches. MSVC is not used
by this cross build. Setup does not upgrade the entire Ubuntu distribution.

The default storage is `E:\MPVBuild\build.ext4`, a new 48 GiB sparse ext4 image,
mounted at `/home/diamond/mpv-build`. It allocates host space as files are written.
This keeps compiler work off the nearly full C: drive and avoids building over
DrvFs. Existing images are never reformatted; an interrupted image creation or
recipe setup requires inspection. Each invocation mounts the image if needed.
Use `-Storage`, `-Distribution`, and `-WslUser` consistently to override defaults.
Do not move or modify the image while it is mounted. WSL shutdown unmounts it.
After a WSL restart, run `-Action status` to mount the workspace before opening
its files in Explorer. Only one storage image occupies this mount point at a
time; finish all work and terminate the distribution before switching storage.

`toolchain` downloads LLVM runtime sources and builds MinGW headers, CRT,
compiler-rt, libc++, and supporting tools once. It uses the installed compiler
instead of compiling LLVM itself. `mpv` also performs this bootstrap if necessary,
builds the approved dependencies once, and then invokes Ninja directly in MPV's
persistent build directory. The first complete build takes considerably longer
than later MPV-only builds. Four concurrent jobs are the default for this machine's
approximately 8 GiB of WSL RAM; dependency packages build one at a time, with
up to four compiler jobs inside each. Use `-Jobs 6` to override when memory permits.

The local profile is **GPL/shared FFmpeg, libmpv only, x86-64-v2**. It applies the
existing recipe layers and every `mpv-patches/mpv-*.patch`. The approved codecs,
protocols, disc readers, and other
features remain unchanged. Dependency ThinLTO remains enabled. MPV ThinLTO is
disabled to speed up edit/build cycles; this is a development build, not an
identical reproduction of CI's release compiler and link settings.

## Edit and rebuild MPV

After setup, edit the Linux source from Windows Explorer at:

```text
\\wsl.localhost\Ubuntu-22.04\home\diamond\mpv-build\src_packages\mpv
```

Then run:

```powershell
.\scripts\local\build.ps1 -Action mpv
.\scripts\local\build.ps1 -Action package
```

These commands do not fetch, reset, clean, or reapply patches to your MPV source,
including when building dependencies for the first time.
They retain the Meson/Ninja configuration and compiled objects. `package` copies
the development DLL, FFmpeg executable and DLLs, headers, available PDBs, revisions,
working-tree diff, profile, and hashes into a new timestamped directory beneath
`E:\MPVBuild\artifacts`. It does not overwrite installed client files.
Keep all seven FFmpeg DLL families alongside libmpv when testing.

To carry a fix back into this repository, commit it in the Linux MPV checkout
and use `git format-patch` to produce a new patch in `mpv-patches/`. Preserve the
existing patch order. Uncommitted changes are recorded by `package`; untracked
files must be added to Git to be included in that diff.

## Other actions and maintenance

```powershell
.\scripts\local\build.ps1 -Action status
.\scripts\local\build.ps1 -Action dependencies
.\scripts\local\build.ps1 -Action ffmpeg
```

`dependencies` completes dependency installation and configures MPV without
compiling it. `ffmpeg` rebuilds and installs the persistent FFmpeg tree after
source edits; rebuild MPV afterward if FFmpeg's ABI changes. Logs are appended
under `/home/diamond/mpv-build/logs`. Upstream ExternalProject failure details
are in `build64-v2/**/src/*-stamp/*-err.log` and `*-out.log`.

Recipes start at upstream commit `1f8d2e76255abc48f0456d9be99b4cab9c6566e6`
and keep their own Git history. `prepare` validates recipe fingerprints and
regenerates CMake wrappers; routine `mpv` builds do not reconfigure everything.
Native patches are a snapshot taken during initial setup. Later edits to patch
files do not silently reset or replace an existing development source tree.
Apply those changes explicitly to that checkout, or use a fresh `-Storage`
directory. A changed recipe profile also requires a fresh workspace to avoid
mixing incompatible installed dependencies. There is no automatic source update,
full clean, distribution, or publishing step.
