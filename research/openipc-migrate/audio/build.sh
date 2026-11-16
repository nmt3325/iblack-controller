#!/bin/sh
# Builds libmajaudiofix.so from majaudiofix.S with GNU binutils for ARM (no C compiler needed).
# Usage: audio/build.sh BINUTILS_ROOT OPENIPC_ROOTFS [OUT]
#   BINUTILS_ROOT   directory with usr/bin/arm-linux-gnueabi-{as,ld,strip}, e.g. the Debian package
#                   binutils-arm-linux-gnueabi 2.40-2 unpacked with dpkg-deb -x (the checked-in
#                   libmajaudiofix.so was built with it; use / for an installed package)
#   OPENIPC_ROOTFS  the unpacked OpenIPC rootfs of the camera (its lib/libc.so is musl)
#   OUT             default: libmajaudiofix.so in the current directory
set -eu
[ $# -ge 2 ] || {
	echo "usage: $0 BINUTILS_ROOT OPENIPC_ROOTFS [OUT]" >&2
	exit 2
}
B=$1 ROOTFS=$2 OUT=${3:-libmajaudiofix.so}
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$ROOTFS/lib/libc.so" ] || {
	echo "$0: $ROOTFS/lib/libc.so not found" >&2
	exit 2
}
if [ -d "$B/usr/lib/x86_64-linux-gnu" ]; then
	LD_LIBRARY_PATH="$B/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
	export LD_LIBRARY_PATH
fi
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
"$B/usr/bin/arm-linux-gnueabi-as" -march=armv5te -mfloat-abi=soft -meabi=5 -o "$T/fix.o" "$HERE/majaudiofix.S"
"$B/usr/bin/arm-linux-gnueabi-ld" -shared -soname libmajaudiofix.so --hash-style=both \
	-z noexecstack -z now --build-id=none -o "$T/out.so" "$T/fix.o" -L"$ROOTFS/lib" -lc
"$B/usr/bin/arm-linux-gnueabi-strip" --strip-unneeded "$T/out.so"
cp "$T/out.so" "$OUT"
