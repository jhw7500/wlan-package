#!/bin/bash

WIFI_SH="${WIFI_SH:-/usr/local/scripts/wifi.sh}"

case "${1:-}" in
    start|stop|restart|status|enable|disable)
        exec "$WIFI_SH" log system "$1"
        ;;
    clean)
        exec "$WIFI_SH" log reset
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|enable|disable|clean}" >&2
        echo "For interface logging use: wifi <mlan0|mlan1|eth0> log <action>" >&2
        exit 2
        ;;
esac
