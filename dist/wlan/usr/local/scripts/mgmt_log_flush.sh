#!/bin/bash
set -euo pipefail

PROC_BASE="/proc/mwlan"
LOG_BASE="/var/log/cantops/mgmt"

flush_adapter() {
    local adapter="$1" iface="$2"
    local proc_file="${PROC_BASE}/${adapter}/mgmt_log"
    local log_dir="${LOG_BASE}/${iface}"
    local log_file="${log_dir}/mgmt.log"

    [ -f "$proc_file" ] || return 0

    mkdir -p "$log_dir"
    # Convert UTC timestamps to local time
    # [2026-03-17 09:15:23.045] ... → [2026-03-17 18:15:23.045] ... (KST)
    while IFS= read -r line; do
        if [[ "$line" =~ ^\[([0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}:[0-9]{2})\.([0-9]+)\](.*) ]]; then
            local_ts=$(date -d "${BASH_REMATCH[1]} UTC" "+%Y-%m-%d %H:%M:%S" 2>/dev/null) || local_ts="${BASH_REMATCH[1]}"
            printf '[%s.%s]%s\n' "$local_ts" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}"
        else
            printf '%s\n' "$line"
        fi
    done < "$proc_file" >> "$log_file" && echo 1 > "$proc_file"
}

flush_adapter "adapter0" "mlan0"
flush_adapter "adapter1" "mlan1"
