#!/bin/sh
# sysupgrade-guard: keeps OpenIPC's updater inside the fixed flash slots of an iBlack/iErmu camera
# (Hi3518EV100, 8 MiB NOR) that was moved to OpenIPC over the network and still boots its vendor U-Boot.
#
# The vendor U-Boot passes these slots in its bootargs; Linux cannot change them:
#   boot 0x000000 384K | env 0x060000 64K | kernel 0x070000 1856K | rootfs 0x240000 4992K | rootfs_data 0x720000 896K
# OpenIPC's online update (-k/-r, --url, --channel, --build, the Web UI's GitHub source, fw-autoupdate) fetches
# the split package and writes its firmware.bin from 0x070000 in one piece, through the rootfs slot into
# rootfs_data; the rootfs of current nightlies is also larger than 4992K. The updater's self-update replaces it
# with OpenIPC master before it runs. Any of these can leave the camera unbootable, and it has no serial console.
# This guard passes on only:
#   --kernel=FILE --rootfs=FILE   a uImage of this SoC <= 1856K and a squashfs <= 4992K
#   --archive=FILE                a plain package (uImage.* and rootfs.squashfs.*) whose images fit; never firmware.bin
#   -k -r                         only together with --archive (the Web UI's upload passes them)
#   -n -x -s -z --web --insecure  overlay wipe (factory reset), no reboot, quiet, no self-update, Web UI, TLS
#   -h --help --history --list-builds[=N]   read-only
# and always adds -z (no self-update). Everything else is refused before the updater runs. The updater itself
# still runs its own checks (checksums, SoC, version, sizes) on what the guard lets through.
#
# Installed as /usr/sbin/sysupgrade in the overlay; the original stays at /rom/usr/sbin/sysupgrade.
# Status: sysupgrade --guard-status. Remove: rm /overlay/usr/sbin/sysupgrade.
# A factory reset (sysupgrade -n, firstboot, the Web UI's reset) wipes the overlay and this guard with it;
# install it again afterwards (easy/post_setup.py does).

GUARD_VERSION=1
REAL=${SYSUPGRADE_GUARD_REAL:-/rom/usr/sbin/sysupgrade}
PROC_MTD=${SYSUPGRADE_GUARD_PROC_MTD:-/proc/mtd}
# Updater versions whose plain --kernel/--rootfs path was read and found to keep the slots.
KNOWN_UPDATERS=${SYSUPGRADE_GUARD_KNOWN:-1.0.84}
SOC=hi3518ev100
KERNEL_SLOT=1900544 # 0x1d0000
ROOTFS_SLOT=5111808 # 0x4e0000

say() {
	echo "sysupgrade-guard: $*"
}

refuse() {
	logger -t sysupgrade-guard "refused: $*" 2>/dev/null # first: a closed stdout must not lose the record
	say "refused, nothing was written: $*"
	say "this camera boots fixed slots through its vendor U-Boot (kernel 1856K, rootfs 4992K);"
	say "allowed: --kernel=FILE --rootfs=FILE, --archive=PLAIN.tgz, -n, --help, --history, --list-builds"
	exit 1
}

layout_ok() {
	[ -r "$PROC_MTD" ] || return 1
	awk '
		$4 == "\"boot\"" { b = $1 $2 }
		$4 == "\"env\"" { e = $1 $2 }
		$4 == "\"kernel\"" { k = $1 $2 }
		$4 == "\"rootfs\"" { r = $1 $2 }
		$4 == "\"rootfs_data\"" { d = $1 $2 }
		END {
			ok = b == "mtd0:00060000" && e == "mtd1:00010000" && k == "mtd2:001d0000" &&
				r == "mtd3:004e0000" && d == "mtd4:000e0000"
			exit !ok
		}' "$PROC_MTD"
}

updater_version() {
	sed -n 's/^scr_version=//p' "$REAL" 2>/dev/null | head -n 1
}

file_size() {
	echo $(($(wc -c <"$1")))
}

hex4() {
	od -An -tx1 -N4 "$1" 2>/dev/null | tr -d ' \n'
}

# kernel_problem HEADER SIZE: prints why a uImage (its first 64 bytes in HEADER, SIZE bytes long) cannot go in.
kernel_problem() {
	[ "$2" -le "$KERNEL_SLOT" ] ||
		{ echo "the kernel image is $2 bytes; the kernel slot holds $KERNEL_SLOT"; return; }
	[ "$(hex4 "$1")" = 27051956 ] || { echo "the kernel image is not a uImage"; return; }
	set -- "$1" "$2" $(od -An -tu1 -j12 -N4 "$1" 2>/dev/null)
	[ $# -eq 6 ] || { echo "the uImage header is short"; return; }
	n=$((($3 << 24) + ($4 << 16) + ($5 << 8) + $6 + 64))
	[ "$n" -le "$2" ] || { echo "the uImage is cut short ($2 of $n bytes)"; return; }
	name=$(dd if="$1" bs=32 skip=1 count=1 2>/dev/null | tr -d '\000')
	case $name in
	*"$SOC"*) ;;
	*) echo "the uImage is built for '$name', not $SOC" ;;
	esac
}

# rootfs_problem HEADER SIZE: the same for a squashfs (its first 96 bytes in HEADER).
rootfs_problem() {
	[ "$2" -le "$ROOTFS_SLOT" ] ||
		{ echo "the rootfs image is $2 bytes, $(($2 - ROOTFS_SLOT)) more than the rootfs slot holds ($ROOTFS_SLOT)"; return; }
	[ "$(hex4 "$1")" = 68737173 ] || { echo "the rootfs image is not a squashfs"; return; }
	set -- "$1" "$2" $(od -An -tu1 -j40 -N8 "$1" 2>/dev/null)
	[ $# -eq 10 ] || { echo "the squashfs superblock is short"; return; }
	[ "$7$8$9${10}" = 0000 ] || { echo "the squashfs claims more than 4 GiB"; return; }
	used=$(($3 + ($4 << 8) + ($5 << 16) + ($6 << 24)))
	[ "$used" -le "$2" ] || echo "the squashfs is cut short ($2 of $used bytes)"
}

check_file() { # check_file kernel|rootfs FILE
	[ -f "$2" ] || refuse "$2 not found"
	if [ "$1" = kernel ]; then
		p=$(kernel_problem "$2" "$(file_size "$2")")
	else
		p=$(rootfs_problem "$2" "$(file_size "$2")")
	fi
	[ -z "$p" ] || refuse "$2: $p"
}

# Lists the archive without unpacking it (/tmp is small) and reads only the image headers.
check_archive() {
	a=$1
	[ -f "$a" ] || refuse "$a not found"
	summary=$(gzip -dc "$a" 2>/dev/null | tar -tvf - 2>/dev/null | awk -v soc="$SOC" '
		NF < 6 { next }
		{ n = $NF; sub(/^\.\//, "", n) }
		n == "" || n == "." { next }
		n ~ /^firmware/ { sub(/\.md5sum$/, "", n); print "split " n; next }
		n == "uImage." soc { print "kernel " $3 " " $NF; next }
		n == "rootfs.squashfs." soc { print "rootfs " $3 " " $NF; next }
		n == "uImage." soc ".md5sum" || n == "rootfs.squashfs." soc ".md5sum" { next }
		{ print "other " n }')
	ksize= rsize= kname= rname=
	while read -r kind v1 v2; do
		case $kind in
		split) refuse "$a carries $v1: a split-layout image is written from 0x070000 in one piece and overruns rootfs_data here" ;;
		other) refuse "$a carries $v1, which is not part of a plain $SOC package" ;;
		kernel)
			[ -z "$ksize" ] || refuse "$a carries the kernel image twice"
			ksize=$v1 kname=$v2
			;;
		rootfs)
			[ -z "$rsize" ] || refuse "$a carries the rootfs image twice"
			rsize=$v1 rname=$v2
			;;
		esac
	done <<EOF
$summary
EOF
	[ -n "$ksize" ] && [ -n "$rsize" ] ||
		refuse "$a is not a plain $SOC package (uImage.$SOC and rootfs.squashfs.$SOC)"
	tmp=$(mktemp /tmp/sysupgrade-guard.XXXXXX) || refuse "cannot create a file in /tmp"
	gzip -dc "$a" 2>/dev/null | tar -xOf - "$kname" 2>/dev/null | head -c 64 >"$tmp"
	p=$(kernel_problem "$tmp" "$ksize")
	if [ -z "$p" ]; then
		gzip -dc "$a" 2>/dev/null | tar -xOf - "$rname" 2>/dev/null | head -c 96 >"$tmp"
		p=$(rootfs_problem "$tmp" "$rsize")
	fi
	rm -f "$tmp"
	[ -z "$p" ] || refuse "$a: $p"
}

guard_status() {
	v=$(updater_version)
	echo "sysupgrade-guard $GUARD_VERSION: active; the updater is $REAL (v${v:-unknown})"
	if layout_ok; then
		echo "flash layout: fixed slots (kernel 1856K, rootfs 4992K, rootfs_data 896K)"
	else
		echo "flash layout: NOT the expected fixed slots -- every write is refused"
	fi
	case " $KNOWN_UPDATERS " in
	*" $v "*) echo "updater version: known" ;;
	*) echo "updater version: not reviewed (known: $KNOWN_UPDATERS) -- every write is refused" ;;
	esac
	echo "refused: -k/-r without --archive, --url, --channel, --build, -u, -f/--force_*, --no_ramfs, split images"
	if command -v fw_printenv >/dev/null 2>&1; then
		echo "env upgrade: $(fw_printenv -n upgrade 2>/dev/null || echo '(unset)')"
	fi
}

writes= info= archive= kernel= rootfs= kr= status=
for arg in "$@"; do
	case $arg in
	-h | --help | --history | --list-builds | --list-builds=*) info=1 ;;
	--guard-status) status=1 ;;
	-k | -r) kr=1 ;;
	--kernel=*) kernel=${arg#*=} writes=1 ;;
	--rootfs=*) rootfs=${arg#*=} writes=1 ;;
	--archive=*) archive=${arg#*=} writes=1 ;;
	-n | --wipe_overlay) writes=1 ;;
	-x | --no_reboot | -z | --no_update | -s | --web | --insecure) ;;
	-u) refuse "-u would rewrite U-Boot; the vendor U-Boot must stay" ;;
	--url=* | --channel=* | --build=*)
		refuse "$arg downloads the split package, which overruns rootfs_data on this layout" ;;
	-f | --force_all | --force_md5 | --force_soc | --force_ver)
		refuse "$arg turns off the updater's checksum, SoC and version checks together" ;;
	--no_ramfs) refuse "--no_ramfs flashes the running rootfs without a copy in RAM" ;;
	*) refuse "unknown option '$arg'" ;;
	esac
done

if [ -n "$status" ]; then
	guard_status
	exit 0
fi
[ -x "$REAL" ] || refuse "the original updater $REAL is missing or not executable"
if [ -n "$kr" ] && [ -z "$archive" ]; then
	refuse "-k/-r without --archive is the online update (the split package from GitHub); use --kernel=FILE --rootfs=FILE or --archive=FILE"
fi
if [ -n "$archive" ] && [ -n "$kernel$rootfs" ]; then
	refuse "use either --archive or --kernel/--rootfs"
fi
if [ -n "$writes" ] && [ -z "$info" ]; then
	layout_ok || refuse "$PROC_MTD does not show the fixed slots this guard was written for"
	v=$(updater_version)
	case " $KNOWN_UPDATERS " in
	*" $v "*) ;;
	*) refuse "$REAL is v${v:-unknown}, not a version reviewed for this layout ($KNOWN_UPDATERS)" ;;
	esac
	[ -z "$kernel" ] || check_file kernel "$kernel"
	[ -z "$rootfs" ] || check_file rootfs "$rootfs"
	[ -z "$archive" ] || check_archive "$archive"
fi
if [ "$SYSUPGRADE_GUARD_DRYRUN" = 1 ]; then
	say "would run: $REAL -z $*"
	exit 0
fi
[ -z "$writes" ] || [ -n "$info" ] || logger -t sysupgrade-guard "passing on: $*" 2>/dev/null
exec "$REAL" -z "$@"
