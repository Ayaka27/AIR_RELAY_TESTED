#!/usr/bin/env bash
# Bring up the ground control link on the Raspberry Pi.
#
# Two supported modes:
#   client   - join an authorized existing wireless network (WPA2)
#   ap       - create a private access point the ground operator joins
#
# Usage:
#   sudo bash scripts/setup_network.sh client "SSID" "passphrase"
#   sudo bash scripts/setup_network.sh ap     "AirRelay-AP" "somepassphrase12"
#
# This is only the link bring-up; it does not alter relay or dashboard logic.
# The dashboard binds 0.0.0.0:8080, so no firewall rule changes are needed to
# reach it on the Pi's interface address.
set -euo pipefail

MODE="${1:-}"; SSID="${2:-AirRelay}"; PASS="${3:-}"

ensure_wifi_dev() {
  dev="$(iw dev | awk '/Interface/{print $2; exit}')"
  if [[ -z "$dev" ]]; then echo "No wireless interface found."; exit 1; fi
  echo "$dev"
}

case "$MODE" in
  client)
    dev="$(ensure_wifi_dev)"
    rfkill unblock wifi || true
    cat > /etc/wpa_supplicant/wpa_supplicant.conf <<EOF
network={
    ssid="$SSID"
    psk="$PASS"
    key_mgmt=WPA-PSK
}
EOF
    wpa_supplicant -B -i "$dev" -c /etc/wpa_supplicant/wpa_supplicant.conf
    dhclient -v "$dev" || dhcpcd -n "$dev" || true
    echo "Joined network '$SSID'. Dashboard at: http://$(hostname -I | awk '{print $1}'):8080"
    ;;
  ap)
    if [[ ${#PASS} -lt 8 ]]; then echo "AP passphrase must be >= 8 chars"; exit 1; fi
    dev="$(ensure_wifi_dev)"
    ip link set "$dev" up
    cat > /etc/hostapd/hostapd.conf <<EOF
interface=$dev
ssid=$SSID
wpa_passphrase=$PASS
driver=nl80211
hw_mode=g
channel=6
wmm_enabled=1
country_code=US
ieee80211n=1
wpa=2
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP CCMP
rsn_pairwise=CCMP
EOF
    sed -i "s/^#\?DAEMON_CONF=.*/DAEMON_CONF=\"\/etc\/hostapd\/hostapd.conf\"/" /etc/default/hostapd
    cat > /etc/dnsmasq.conf <<EOF
interface=$dev
dhcp-range=10.42.0.10,10.42.0.100,255.255.255.0,12h
address=/airrelay.local/10.42.0.1
EOF
    ip addr add 10.42.0.1/24 dev "$dev" 2>/dev/null || true
    systemctl restart dnsmasq hostapd
    echo "Access point '$SSID' ready. Connect a ground device and open:"
    echo "  http://airrelay.local:8080  or  http://10.42.0.1:8080"
    ;;
  *)
    echo "usage: $0 {client|ap} [ssid] [passphrase]"; exit 1 ;;
esac
