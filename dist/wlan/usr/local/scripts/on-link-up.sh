#!/bin/sh
tag=$(basename "$0")
key=LOG

logger -p local0.info "[$tag:$LINENO] eth0 link up"
touch /tmp/link
