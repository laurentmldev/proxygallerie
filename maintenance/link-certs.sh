#!/bin/sh
# Keeps /etc/nginx/certs/maintenance/<host> -> ../<cert dir> for every
# host acme-companion has issued a certificate for, so that
# conf.d/maintenance-fallback.conf can still find a host's certificate
# once its container is stopped.
#
# Why this is needed: acme-companion stores a certificate in a folder
# named after its *first* host (LETSENCRYPT_HOST=a.fr,b.fr -> certs/a.fr/)
# and only links <host>.crt/.key for each host while the container is
# running. Those links are deleted when it stops, so for b.fr nothing
# on disk would name the folder any more. The links made here are never
# deleted (the certificates they point to aren't either).
set -u
CERTS=/etc/nginx/certs
OUT="$CERTS/maintenance"
NGINX_GID=101

while true; do
    mkdir -p "$OUT"
    # Live <host>.crt links, which name the folder for every host,
    # including the 2nd, 3rd... host of a multi-host certificate.
    for link in "$CERTS"/*.crt; do
        [ -L "$link" ] || continue
        host=$(basename "$link" .crt)
        [ "$host" = default ] && continue
        dir=$(basename "$(dirname "$(readlink "$link")")")
        [ -f "$CERTS/$dir/fullchain.pem" ] || continue
        [ "$(readlink "$OUT/$host" 2>/dev/null)" = "../$dir" ] || ln -sfn "../$dir" "$OUT/$host"
    done
    # Certificate folders themselves, for a host whose container was
    # already stopped before this ran (covers its first host). Also
    # gives their key the same group-readable permissions acme-companion
    # is set to use (FILES_GID / FILES_PERMS), which it only applies to
    # the certificates of running containers.
    for dir in "$CERTS"/*/; do
        dir=$(basename "$dir")
        [ -f "$CERTS/$dir/fullchain.pem" ] || continue
        [ -e "$OUT/$dir" ] || ln -sfn "../$dir" "$OUT/$dir"
        key="$CERTS/$dir/key.pem"
        if [ -f "$key" ] && [ "$(stat -c %g:%a "$key")" != "$NGINX_GID:640" ]; then
            chgrp "$NGINX_GID" "$key" && chmod 640 "$key"
        fi
    done
    sleep 60
done
