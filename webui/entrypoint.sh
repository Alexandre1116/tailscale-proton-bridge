#!/bin/sh
set -eu

: "${WEBUI_PROTOCOL:=http}"
: "${WEBUI_INTERNAL_PORT:=8080}"
: "${WEBUI_LISTEN_HOST:=0.0.0.0}"
: "${WEBUI_CERT_FILE:=/certs/fullchain.pem}"
: "${WEBUI_KEY_FILE:=/certs/privkey.pem}"

if [ -n "${WEBUI_PASSWORD_FILE:-}" ]; then
    if [ ! -r "$WEBUI_PASSWORD_FILE" ]; then
        echo "ERROR: WEBUI_PASSWORD_FILE is missing or unreadable." >&2
        exit 1
    fi
    WEBUI_PASSWORD="$(tr -d '\r\n' < "$WEBUI_PASSWORD_FILE")"
    export WEBUI_PASSWORD
fi

if [ -z "${WEBUI_PASSWORD:-}" ]; then
    echo "ERROR: WEBUI_PASSWORD must be set. Refusing to start without authentication." >&2
    exit 1
fi

case "$(printf '%s' "$WEBUI_PROTOCOL" | tr '[:upper:]' '[:lower:]')" in
    http)
        exec waitress-serve \
            --listen="${WEBUI_LISTEN_HOST}:${WEBUI_INTERNAL_PORT}" \
            app:app
        ;;
    https)
        if [ ! -r "$WEBUI_CERT_FILE" ] || [ ! -r "$WEBUI_KEY_FILE" ]; then
            echo "ERROR: HTTPS requires readable certificate and key files." >&2
            echo "       WEBUI_CERT_FILE=$WEBUI_CERT_FILE" >&2
            echo "       WEBUI_KEY_FILE=$WEBUI_KEY_FILE" >&2
            exit 1
        fi
        exec gunicorn \
            --bind "${WEBUI_LISTEN_HOST}:${WEBUI_INTERNAL_PORT}" \
            --workers "${WEBUI_WORKERS:-2}" \
            --certfile "$WEBUI_CERT_FILE" \
            --keyfile "$WEBUI_KEY_FILE" \
            app:app
        ;;
    *)
        echo "ERROR: WEBUI_PROTOCOL must be http or https." >&2
        exit 1
        ;;
esac
