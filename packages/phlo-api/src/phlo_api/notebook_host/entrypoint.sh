#!/bin/sh
# Network rules for the notebook kernel host, then the host itself.
#
# Kernel users (20001-20064) may not open any connection: only replies to
# connections the host opened to them pass. The installer user (19999) may
# resolve names and reach public HTTP(S) package indexes, plus the configured
# package index and proxy, but no other private address. Any rule that cannot
# be installed stops the container: the host never runs kernels without them.
set -eu

KERNELS=20001-20064
INSTALLER=19999

kernel_rules() {
    "$1" -w -A OUTPUT -m owner --uid-owner "$KERNELS" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    "$1" -w -A OUTPUT -m owner --uid-owner "$KERNELS" -j REJECT
}

allow_endpoint() {
    # allow_endpoint URL: let the installer reach this URL's host and port.
    url="$1"
    [ -n "$url" ] || return 0
    hostport=$(printf '%s' "$url" | sed -E 's#^[a-zA-Z]+://([^/@]*@)?([^/]+).*#\2#')
    host=${hostport%:*}
    port=${hostport##*:}
    if [ "$host" = "$hostport" ]; then
        case "$url" in https://*) port=443 ;; *) port=80 ;; esac
    fi
    for address in $(getent ahostsv4 "$host" | awk '{print $1}' | sort -u); do
        iptables -w -A OUTPUT -m owner --uid-owner "$INSTALLER" -p tcp -d "$address" --dport "$port" -j ACCEPT
    done
}

kernel_rules iptables

iptables -w -A OUTPUT -m owner --uid-owner "$INSTALLER" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -w -A OUTPUT -m owner --uid-owner "$INSTALLER" -p udp --dport 53 -j ACCEPT
iptables -w -A OUTPUT -m owner --uid-owner "$INSTALLER" -p tcp --dport 53 -j ACCEPT
allow_endpoint "${PHLO_NOTEBOOK_PACKAGE_INDEX:-}"
allow_endpoint "${HTTPS_PROXY:-}"
allow_endpoint "${HTTP_PROXY:-}"
for network in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10 127.0.0.0/8 0.0.0.0/8; do
    iptables -w -A OUTPUT -m owner --uid-owner "$INSTALLER" -d "$network" -j REJECT
done
iptables -w -A OUTPUT -m owner --uid-owner "$INSTALLER" -p tcp -m multiport --dports 80,443 -j ACCEPT
iptables -w -A OUTPUT -m owner --uid-owner "$INSTALLER" -j REJECT

if ip6tables -w -L OUTPUT >/dev/null 2>&1; then
    kernel_rules ip6tables
    ip6tables -w -A OUTPUT -m owner --uid-owner "$INSTALLER" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    for network in ::1/128 fc00::/7 fe80::/10; do
        ip6tables -w -A OUTPUT -m owner --uid-owner "$INSTALLER" -d "$network" -j REJECT
    done
    ip6tables -w -A OUTPUT -m owner --uid-owner "$INSTALLER" -p tcp -m multiport --dports 80,443 -j ACCEPT
    ip6tables -w -A OUTPUT -m owner --uid-owner "$INSTALLER" -j REJECT
elif grep -qv ' lo$' /proc/net/if_inet6 2>/dev/null; then
    echo "IPv6 is configured but ip6tables is unavailable; refusing to start." >&2
    exit 1
fi

exec python -I /opt/phlo-kernels/notebook_gateway.py
