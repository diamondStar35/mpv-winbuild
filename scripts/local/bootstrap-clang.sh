#!/usr/bin/env bash
set -euo pipefail
workspace=${1:?workspace required}
root=$workspace/clang_root
system=/usr/lib/llvm-23
[[ -x $system/bin/clang && -x $system/bin/ld.lld ]] || { echo 'Run setup first.' >&2; exit 1; }
mkdir -p "$root/bin" "$root/lib/clang/23/lib"
# Preserve a private resource directory for the Windows compiler-rt builtins.
ln -sfn "$system/lib/clang/23/include" "$root/lib/clang/23/include"
if [[ -d $system/lib/clang/23/lib/linux ]]; then
    ln -sfn "$system/lib/clang/23/lib/linux" "$root/lib/clang/23/lib/linux"
fi
for tool in "$system"/bin/*; do
    name=$(basename "$tool")
    [[ $name == clang || $name == clang++ ]] && continue
    [[ -x $tool && ! -d $tool ]] && ln -sfn "$tool" "$root/bin/$name"
done
for compiler in clang clang++; do
    cat > "$root/bin/$compiler" <<EOF
#!/usr/bin/env bash
exec "$system/bin/$compiler" -resource-dir="$root/lib/clang/23" --rtlib=compiler-rt --unwindlib=libunwind -stdlib=libc++ "\$@"
EOF
    chmod +x "$root/bin/$compiler"
done
