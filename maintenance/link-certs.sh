#!/bin/sh
# Keeps /etc/nginx/certs/maintenance/<host> -> ../<cert dir> for every
# host that has a certificate, so that conf.d/maintenance-fallback.conf
# can still find a host's certificate once its container is stopped.
#
# Why this is needed: acme-companion stores a certificate in a folder
# named after its *first* host (LETSENCRYPT_HOST=a.fr,b.fr -> certs/a.fr/)
# and only links <host>.crt/.key for each host while the container is
# running; those links are deleted when it stops. So the host names are
# read from the certificates themselves (their subjectAltName list),
# which works whether or not the site is running right now.
#
# Runs in the acme-companion image, which has the openssl CLI.
set -u
CERTS=/etc/nginx/certs
OUT="$CERTS/maintenance"
NGINX_GID=101

link() { # link <host> <cert dir>
    [ "$(readlink "$OUT/$1" 2>/dev/null)" = "../$2" ] || ln -sfn "../$2" "$OUT/$1"
}

while true; do
    mkdir -p "$OUT"
    # Oldest certificate first, so that if a host appears in several
    # (an old folder left over after LETSENCRYPT_HOST was reordered),
    # the most recently issued one wins.
    for dir in $(cd "$CERTS" && ls -1tr -d -- */ 2>/dev/null); do
        dir=${dir%/}
        case "$dir" in maintenance|_test_*|wildcard_*) continue ;; esac
        cert="$CERTS/$dir/fullchain.pem"
        [ -f "$cert" ] || continue
        for host in $(openssl x509 -in "$cert" -noout -ext subjectAltName 2>/dev/null \
                        | tr ',' '\n' | sed -n 's/^ *DNS://p'); do
            case "$host" in *[!a-z0-9.-]*|"") continue ;; esac
            link "$host" "$dir"
        done
        # nginx's unprivileged worker loads the key at request time: same
        # group-readable permissions acme-companion is set to use
        # (FILES_GID / FILES_PERMS), which it only applies to running sites.
        key="$CERTS/$dir/key.pem"
        if [ -f "$key" ] && [ "$(stat -c %g:%a "$key")" != "$NGINX_GID:640" ]; then
            chgrp "$NGINX_GID" "$key" && chmod 640 "$key"
        fi
    done
    # A running site's own links are authoritative.
    for crt in "$CERTS"/*.crt; do
        [ -L "$crt" ] || continue
        host=$(basename "$crt" .crt)
        [ "$host" = default ] && continue
        dir=$(basename "$(dirname "$(readlink "$crt")")")
        [ -f "$CERTS/$dir/fullchain.pem" ] && link "$host" "$dir"
    done
    sleep 60
done
