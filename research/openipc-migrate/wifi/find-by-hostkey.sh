#!/bin/bash
# Find the camera on a LAN by its SSH host key, so that the root password is never sent to an
# unknown host. Runs on the Linux host, not on the camera.
# usage: find-by-hostkey.sh IFACE KEYFILE OUTFILE
#   IFACE    host interface on the camera's /24 (here: the host Wi-Fi joined to the home router)
#   KEYFILE  one line "ssh-ed25519 AAAA...", taken on the camera beforehand with
#            dropbearkey -y -f /etc/dropbear/dropbear_ed25519_host_key
#   OUTFILE  receives the camera's address (mode 0600); stdout shows only a status line
# dropbear on the camera needs about 5 s for the key exchange, hence ssh-keyscan -T 15.
set -u
IFACE=$1 KEY=$2 OUT=$3
umask 077
CIDR=$(ip -4 -o addr show "$IFACE" | awk '{print $4}' | head -1)
[ -n "$CIDR" ] || { echo "no IPv4 address on $IFACE"; exit 2; }
NET=${CIDR%.*} SELF=${CIDR%/*}
rm -f "$OUT"
for i in $(seq 1 254); do ping -c2 -i0.5 -W2 -n "$NET.$i" >/dev/null 2>&1 & done   # fill the neighbour table
wait
n=0 ssh=0
for ip in $(ip -4 neigh show dev "$IFACE" | awk '$2 == "lladdr" {print $1}'); do
	[ "$ip" = "$SELF" ] && continue
	n=$((n + 1))
	timeout 2 bash -c "</dev/tcp/$ip/22" 2>/dev/null || continue
	ssh=$((ssh + 1))
	k=$(ssh-keyscan -t ed25519 -T 15 "$ip" 2>/dev/null | awk '{print $2, $3}')
	if [ -n "$k" ] && [ "$k" = "$(cat "$KEY")" ]; then
		echo "$ip" > "$OUT"
		echo "found (neighbours=$n, ssh=$ssh)"
		exit 0
	fi
done
echo "not found (neighbours=$n, ssh=$ssh)"
exit 1
