#!/bin/sh
# trial_dhcp.sh -- udhcpc script of the one-shot trial (stock BusyBox 1.18.4): address + default
# route only; no resolv.conf (the LAN host is addressed by IP), nothing written to flash.
case "$1" in
  deconfig) ifconfig "$interface" 0.0.0.0;;
  bound|renew)
    if [ -n "$subnet" ]; then ifconfig "$interface" "$ip" netmask "$subnet"; else ifconfig "$interface" "$ip"; fi
    if [ -n "$router" ]; then
      for i in 1 2 3; do route del default dev "$interface" 2>/dev/null || break; done
      set -- $router; route add default gw "$1" dev "$interface"
    fi;;
esac
exit 0
