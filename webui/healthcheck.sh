#!/bin/sh
set -eu

case "${WEBUI_PROTOCOL:-http}" in
    https) scheme="https";;
    http) scheme="http";;
    *) exit 1;;
esac

SCHEME="$scheme" python -c 'import os, ssl, urllib.request; url = os.environ["SCHEME"] + "://127.0.0.1:8080/healthz"; ctx = ssl._create_unverified_context() if os.environ["SCHEME"] == "https" else None; urllib.request.urlopen(url, context=ctx, timeout=4)'
