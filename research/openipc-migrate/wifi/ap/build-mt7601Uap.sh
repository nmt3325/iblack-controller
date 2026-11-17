#!/bin/sh
# build-mt7601Uap.sh: rebuild mt7601Uap.ko.xz, the access point driver that wifi-ap loads, from
# source: github.com/openipc/mt7601u (the MT7601U station driver of OpenIPC) with
# mt7601Uap.patch, built against OpenIPC's kernel for hi3518ev100 with OpenIPC's toolchain.
#
#   sh build-mt7601Uap.sh [WORKDIR]     (default ./mt7601Uap-build; needs about 1 GB)
#
# Needs an x86_64 Linux host with curl or wget, tar, gzip, xz, patch, make, a C compiler and
# sha256sum. All downloads are pinned (commit and sha256). The result, WORKDIR/mt7601Uap.ko.xz,
# is compared with the copy next to this script. A second run reuses the downloads and the
# prepared kernel tree. GPL-2.0, like the driver.
set -eu

FW=9d95c3582126a22543018346b16043cbca3515fb      # github.com/OpenIPC/firmware
KERNEL=790c5c90fecf07ae1325bdbcecc4c8f31471aff7  # github.com/openipc/linux, branch hisilicon-hi3516cv100
KERNEL_SHA=4f20ea19382bd21024bd8ede84f2b702ff55d0eaad061cb5add7222331d644e7
DRIVER=0ac46553f3190d788b01c15cbeaa14f2951c55a3  # github.com/openipc/mt7601u (package mt7601u-openipc)
DRIVER_SHA=cc143361e74d797ebb447222fc8e356d7de9c5ae4b425089dd0f4fb495f67a95
TC_URL=https://github.com/openipc/firmware/releases/download/toolchain/toolchain.hisilicon-hi3516cv100.tgz
TC_SHA=466683e215a493144ba87b3d57a733864b6db318aad847bf0fd0616dda39729e
RAW=https://raw.githubusercontent.com/OpenIPC/firmware/$FW
CONFIG=br-ext-chip-hisilicon/board/hi3516cv100/hi3518ev100.generic.config
PATCHES="0010-set-correct-gcc-version 0011-add-compiler-gcc8 0012-add-compiler-gcc12
	0013-add-compiler-gcc13 0014-fix-unifdef-constexpr-gcc15"

HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "${1:-mt7601Uap-build}"
W=$(cd "${1:-mt7601Uap-build}" && pwd)
DL=$W/dl
TC=$W/arm-openipc-linux-musleabi_sdk-buildroot
CROSS=$TC/bin/arm-openipc-linux-musleabi-
mkdir -p "$DL"

say() { echo "build-mt7601Uap: $*"; }
die() { echo "build-mt7601Uap: $*" >&2; exit 1; }

fetch() { # URL FILE
	[ -s "$2" ] && return 0
	say "downloading $1"
	if command -v curl >/dev/null 2>&1; then
		curl -fsSL --retry 3 -o "$2.part" "$1"
	else
		wget -q -O "$2.part" "$1"
	fi || die "download failed: $1"
	mv "$2.part" "$2"
}

sha_ok() { # FILE SHA256
	[ "$(sha256sum "$1" | cut -d' ' -f1)" = "$2" ]
}

run() { # LOG COMMAND...: run quietly, show the end of the log if it fails
	log=$1
	shift
	"$@" > "$log" 2>&1 || { tail -n 30 "$log" >&2; die "failed, see $log"; }
}

fetch "$TC_URL" "$DL/toolchain.tgz"
fetch "https://github.com/openipc/linux/archive/$KERNEL.tar.gz" "$DL/linux.tar.gz"
fetch "https://github.com/openipc/mt7601u/archive/$DRIVER.tar.gz" "$DL/mt7601u.tar.gz"
fetch "$RAW/$CONFIG" "$DL/kernel.config"
for p in $PATCHES; do
	fetch "$RAW/general/package/all-patches/linux/$p.patch" "$DL/$p.patch"
done
sha_ok "$DL/linux.tar.gz" "$KERNEL_SHA" || die "$DL/linux.tar.gz: unexpected sha256"
sha_ok "$DL/mt7601u.tar.gz" "$DRIVER_SHA" || die "$DL/mt7601u.tar.gz: unexpected sha256"
if ! sha_ok "$DL/toolchain.tgz" "$TC_SHA"; then
	[ "${ANY_TOOLCHAIN:-}" = 1 ] || die "$DL/toolchain.tgz: unexpected sha256. OpenIPC rebuilds" \
		"its toolchain from time to time; ANY_TOOLCHAIN=1 uses it anyway (the module then" \
		"differs from the one in this repository but should work the same)."
	say "toolchain with another sha256 (ANY_TOOLCHAIN=1)"
fi

if [ ! -f "$TC/.ready" ]; then
	say "unpacking the toolchain"
	rm -rf "$TC"
	tar -xzf "$DL/toolchain.tgz" -C "$W"
	(cd "$TC" && ./relocate-sdk.sh >/dev/null)
	touch "$TC/.ready"
fi

if [ ! -f "$W/linux/.ready" ]; then
	say "preparing the kernel tree (Linux 3.0.8, OpenIPC's hi3518ev100 configuration)"
	rm -rf "$W/linux"
	mkdir "$W/linux"
	tar -xzf "$DL/linux.tar.gz" -C "$W/linux" --strip-components=1
	cp "$DL/kernel.config" "$W/linux/.config"
	for p in $PATCHES; do
		patch -d "$W/linux" -p1 -s < "$DL/$p.patch"
	done
	run "$W/linux.log" make -C "$W/linux" ARCH=arm CROSS_COMPILE="$CROSS" modules_prepare
	touch "$W/linux/.ready"
fi

say "building the driver"
rm -rf "$W/mt7601u"
mkdir "$W/mt7601u"
tar -xzf "$DL/mt7601u.tar.gz" -C "$W/mt7601u" --strip-components=1
patch -d "$W/mt7601u" -p1 -s < "$HERE/mt7601Uap.patch"
# the prefix maps keep the build directory out of __FILE__ (assert messages)
run "$W/mt7601u.log" make -C "$W/mt7601u" ARCH=arm CROSS_COMPILE="$CROSS" KSRC="$W/linux" \
	PWD="$W/mt7601u" KCFLAGS="-fmacro-prefix-map=$W/mt7601u/= -fmacro-prefix-map=$W/linux/=" \
	-j"$(nproc 2>/dev/null || echo 2)" modules
"${CROSS}strip" --strip-debug -o "$W/mt7601Uap.ko" "$W/mt7601u/mt7601Uap.ko"
# CRC32 and a small dictionary, so that BusyBox on the camera unpacks it with little memory
xz -kf --check=crc32 --lzma2=preset=9e,dict=768KiB "$W/mt7601Uap.ko"
say "done: $(sha256sum "$W/mt7601Uap.ko.xz")"
if [ -f "$HERE/mt7601Uap.ko.xz" ]; then
	if cmp -s "$W/mt7601Uap.ko.xz" "$HERE/mt7601Uap.ko.xz"; then
		say "identical with $HERE/mt7601Uap.ko.xz"
	elif xz -dc "$HERE/mt7601Uap.ko.xz" | cmp -s - "$W/mt7601Uap.ko"; then
		say "same module as $HERE/mt7601Uap.ko.xz, compressed differently"
	else
		say "differs from $HERE/mt7601Uap.ko.xz"
	fi
fi
