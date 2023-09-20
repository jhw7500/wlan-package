#!/usr/bin/env bash
# eMMC wear tester (full)
# - LIFE_A/B & Pre-EOL logging
# - Resume from previous run: STATE -> status_kv in LOG -> DIR/rotate scan
# - Effective bytes = (writes + deletes) * WAF
# - P/E cycles used = effective_bytes / (CAPACITY_GIB * 1GiB)
# - Virtual usage: SIM_DAYS_PER_GIB days per 1 GiB effective write
# - Rotation: delete oldest data file on low free space, count deleted bytes
# - Milestone logs on each 0.1 cycles & integer cycles

set -u  # resilience: avoid -e to keep long-running loop

# ===== User parameters (override via env) =====
EMMC_DEV="${EMMC_DEV:-/dev/mmcblk2}"
DATA_DIR="${DATA_DIR:-/var/log/cantops/data}"      # data files directory
LOG_FILE="${LOG_FILE:-/var/log/cantops/emmc_wear.log}"
STATE_FILE="${STATE_FILE:-/var/lib/cantops/emmc_wear.state}"
FILE_PREFIX="${FILE_PREFIX:-fill}"

RATED_PE_CYCLES="${RATED_PE_CYCLES:-3000}"        # vendor spec if known
CAPACITY_GIB="${CAPACITY_GIB:-16}"                # host-definition cycle basis
WRITE_SIZE_MIB="${WRITE_SIZE_MIB:-1024}"          # 1 GiB per loop
SLEEP_SEC="${SLEEP_SEC:-3}"                      # loop sleep
KEEP_FREE_GIB="${KEEP_FREE_GIB:-2}"               # guard free space
SIM_DAYS_PER_GIB="${SIM_DAYS_PER_GIB:-3}"         # virtual usage scale
WAF="${WAF:-1.0}"                                  # write amplification factor

mkdir -p "$DATA_DIR" "$(dirname "$LOG_FILE")" "$(dirname "$STATE_FILE")"

GiB_BYTES=$((1024*1024*1024))
now() { date '+%Y-%m-%d %H:%M:%S%z'; }

# ---------- Health (EXT_CSD) ----------
read_health() {
  local A B E ext
  ext=$(mmc extcsd read "$EMMC_DEV" 2>/dev/null || true)
  A=$(grep -i 'DEVICE_LIFE_TIME_EST_TYP_A' <<<"$ext" 2>/dev/null | awk '{print $NF}')
  B=$(grep -i 'DEVICE_LIFE_TIME_EST_TYP_B' <<<"$ext" 2>/dev/null | awk '{print $NF}')
  E=$(grep -i 'PRE_EOL_INFO'             <<<"$ext" 2>/dev/null | awk '{print $NF}')
  [[ -z "$A" ]] && A="0x00"
  [[ -z "$B" ]] && B="0x00"
  [[ -z "$E" ]] && E="0x01"
  echo "$A $B $E"
}
hex2dec() { printf "%d" "$1"; }
percent_range() { local v; v=$(hex2dec "$1"); [[ $v -eq 0 ]] && { echo "N/A"; return; }; echo "$(( (v-1)*10 ))-$(( v*10 ))"; }
pe_range() { local v rated; v=$(hex2dec "$1"); rated="$2"; [[ $v -eq 0 ]] && { echo "~N/A"; return; }; echo "~$(( rated*((v-1)*10)/100 ))-$(( rated*(v*10)/100 )) P/E"; }
reached_90p() { local a b; a=$(hex2dec "$1"); b=$(hex2dec "$2"); (( a>=10 || b>=10 )); }

fmt_gib() { awk -v b="$1" 'BEGIN{printf "%.2f", b/1024/1024/1024}'; }
human_elapsed() { local s="$1" d h m; d=$(( s/86400 )); s=$(( s%86400 )); h=$(( s/3600 )); s=$(( s%3600 )); m=$(( s/60 )); echo "${d}d ${h}h ${m}m"; }

log_life_line() {
  local A="$1" B="$2" E="$3" Ar Br Apr Bpr E_txt
  Ar=$(percent_range "$A"); Br=$(percent_range "$B")
  Apr=$(pe_range "$A" "$RATED_PE_CYCLES"); Bpr=$(pe_range "$B" "$RATED_PE_CYCLES")
  case "$(hex2dec "$E")" in
    1) E_txt="Pre-EOL: 0x01 (Normal <80% reserved)";;
    2) E_txt="Pre-EOL: 0x02 (Warning ≥80%)";;
    3) E_txt="Pre-EOL: 0x03 (Urgent ≥90%)";;
    *) E_txt="Pre-EOL: $E";;
  esac
  echo "$(now) LIFE_A=$A [$Ar% $Apr], LIFE_B=$B [$Br% $Bpr], $E_txt" | tee -a "$LOG_FILE"
}

# ---------- Persistent state ----------
START_EPOCH=""
TOTAL_WRITTEN_BYTES=0
TOTAL_DELETED_BYTES=0
TOTAL_EFFECTIVE_BYTES=0
PREV_A=""; PREV_B=""; PREV_E=""
PREV_CYCLES_USED="0.0"
CYCLES_PER_GIB=""

save_state() {
  cat >"$STATE_FILE" <<EOF
START_EPOCH=$START_EPOCH
TOTAL_WRITTEN_BYTES=$TOTAL_WRITTEN_BYTES
TOTAL_DELETED_BYTES=$TOTAL_DELETED_BYTES
TOTAL_EFFECTIVE_BYTES=$TOTAL_EFFECTIVE_BYTES
PREV_A=$PREV_A
PREV_B=$PREV_B
PREV_E=$PREV_E
PREV_CYCLES_USED=$PREV_CYCLES_USED
CYCLES_PER_GIB=$CYCLES_PER_GIB
EOF
}

# Parse helper for last status_kv line
_parse_last_status_kv() {
  local last_kv="$1"
  if [[ -n "$last_kv" ]]; then
    local v
    v=$(awk '{for(i=1;i<=NF;i++) if($i ~ /written_bytes=/){split($i,a,"="); print a[2]}}' <<<"$last_kv"); [[ -n "$v" ]] && TOTAL_WRITTEN_BYTES="$v"
    v=$(awk '{for(i=1;i<=NF;i++) if($i ~ /deleted_bytes=/){split($i,a,"="); print a[2]}}' <<<"$last_kv"); [[ -n "$v" ]] && TOTAL_DELETED_BYTES="$v"
    v=$(awk '{for(i=1;i<=NF;i++) if($i ~ /effective_bytes=/){split($i,a,"="); print a[2]}}' <<<"$last_kv"); [[ -n "$v" ]] && TOTAL_EFFECTIVE_BYTES="$v"
    v=$(awk '{for(i=1;i<=NF;i++) if($i ~ /cycles_used=/){split($i,a,"="); print a[2]}}' <<<"$last_kv"); [[ -n "$v" ]] && PREV_CYCLES_USED="$v"
  fi
}

load_state() {
  # 1) STATE_FILE
  if [[ -r "$STATE_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE" || true
  fi
  [[ -z "${START_EPOCH:-}" ]] && START_EPOCH="$(date +%s)"
  [[ -z "${PREV_A:-}" || -z "${PREV_B:-}" || -z "${PREV_E:-}" ]] && read -r PREV_A PREV_B PREV_E < <(read_health)
  [[ -z "${PREV_CYCLES_USED:-}" ]] && PREV_CYCLES_USED="0.0"
  [[ -z "${TOTAL_WRITTEN_BYTES:-}" ]] && TOTAL_WRITTEN_BYTES=0
  [[ -z "${TOTAL_DELETED_BYTES:-}" ]] && TOTAL_DELETED_BYTES=0
  [[ -z "${TOTAL_EFFECTIVE_BYTES:-}" ]] && TOTAL_EFFECTIVE_BYTES=0
  CYCLES_PER_GIB="$CAPACITY_GIB"

  # 2) status_kv 스냅샷 복구 (STATE가 없거나 0이면)
  if [[ ! -s "$STATE_FILE" || ( "$TOTAL_WRITTEN_BYTES" -eq 0 && "$TOTAL_DELETED_BYTES" -eq 0 && "$TOTAL_EFFECTIVE_BYTES" -eq 0 ) ]]; then
    if [[ -r "$LOG_FILE" ]]; then
      local last_kv
      last_kv=$(grep -E 'status_kv:' "$LOG_FILE" | tail -n 1 || true)
      _parse_last_status_kv "$last_kv"
    fi
  fi

  # 3) 디렉터리/rotate 로그 기반 보강
  if [[ "$TOTAL_WRITTEN_BYTES" -eq 0 && -d "$DATA_DIR" ]]; then
    local sum_present
    sum_present=$(find "$DATA_DIR" -maxdepth 1 -type f -name "${FILE_PREFIX}-*.dat" -printf '%s\n' 2>/dev/null | awk '{s+=$1} END{printf "%.0f", s+0}')
    [[ -n "$sum_present" ]] && TOTAL_WRITTEN_BYTES="$sum_present"
  fi
  if [[ "$TOTAL_DELETED_BYTES" -eq 0 && -r "$LOG_FILE" ]]; then
    local sum_deleted
    sum_deleted=$(grep -E '\[rotate\].*bytes=' "$LOG_FILE" | awk -F'bytes=' '{print $2}' | awk '{g+=$1} END{printf "%.0f", g+0}')
    [[ -n "$sum_deleted" ]] && TOTAL_DELETED_BYTES="$sum_deleted"
  fi

  # EFFECTIVE 재계산
  TOTAL_EFFECTIVE_BYTES=$(awk -v w="$TOTAL_WRITTEN_BYTES" -v d="$TOTAL_DELETED_BYTES" -v waf="$WAF" \
    'BEGIN{printf "%.0f", (w + d) * waf }')
}

# ---------- Rotation (returns deleted bytes) ----------
rotate_if_low_free() {
  local free_kb need_kb old del_bytes
  free_kb=$(df -Pk "$DATA_DIR" | awk 'NR==2{print $4}')
  need_kb=$(( KEEP_FREE_GIB * 1024 * 1024 ))
  if (( free_kb < need_kb )); then
    old=$(ls -1t "$DATA_DIR"/${FILE_PREFIX}-*.dat 2>/dev/null | tail -n 1 || true)
    if [[ -n "$old" ]]; then
      del_bytes=$(stat -c %s "$old" 2>/dev/null || echo 0)
      # 로그는 파일에만 기록 (stdout으로 내보내지 않음!)
      printf "%s [rotate] file=%s bytes=%s GiB=%s\n" \
        "$(now)" "$old" "$del_bytes" "$(fmt_gib "$del_bytes")" >> "$LOG_FILE"
      rm -f -- "$old"
      # stdout에는 오직 숫자만
      echo "$del_bytes"
      return 0
    fi
  fi
  echo "0"
}

# ---------- Status & milestones ----------
calc_and_log_status() {
  TOTAL_EFFECTIVE_BYTES=$(awk -v w="$TOTAL_WRITTEN_BYTES" -v d="$TOTAL_DELETED_BYTES" -v waf="$WAF" \
    'BEGIN{printf "%.0f", (w + d) * waf }')

  local total_written_gib total_deleted_gib total_effective_gib
  total_written_gib=$(fmt_gib "$TOTAL_WRITTEN_BYTES")
  total_deleted_gib=$(fmt_gib "$TOTAL_DELETED_BYTES")
  total_effective_gib=$(fmt_gib "$TOTAL_EFFECTIVE_BYTES")

  local cycles_used cycles_pct
  cycles_used=$(awk -v eff="$TOTAL_EFFECTIVE_BYTES" -v cap="$CAPACITY_GIB" -v G="$GiB_BYTES" \
    'BEGIN{printf "%.3f", eff/(cap*G)}')
  cycles_pct=$(awk -v cu="$cycles_used" -v rated="$RATED_PE_CYCLES" 'BEGIN{printf "%.4f", 100.0*cu/rated}')

  local now_s elapsed_s elapsed_human sim_days sim_days_int sim_hours
  now_s=$(date +%s); elapsed_s=$(( now_s - START_EPOCH )); elapsed_human=$(human_elapsed "$elapsed_s")
  sim_days=$(awk -v g="$total_effective_gib" -v k="$SIM_DAYS_PER_GIB" 'BEGIN{printf "%.1f", k*g}')
  sim_days_int=$(awk -v d="$sim_days" 'BEGIN{printf "%d", d}')
  sim_hours=$(awk -v d="$sim_days" 'BEGIN{printf "%d", (d-int(d))*24 }')

  # human readable
  echo "$(now) status: written=${total_written_gib}GiB, deleted=${total_deleted_gib}GiB, effective=${total_effective_gib}GiB (WAF=${WAF}), cycles_used=${cycles_used} (~${cycles_pct}% of ${RATED_PE_CYCLES}), real_elapsed=${elapsed_human}, sim_usage?${sim_days_int}d ${sim_hours}h (${SIM_DAYS_PER_GIB}d/GiB)" \
    | tee -a "$LOG_FILE"
  # machine-parsable snapshot
  echo "$(now) status_kv: written_bytes=${TOTAL_WRITTEN_BYTES} deleted_bytes=${TOTAL_DELETED_BYTES} effective_bytes=${TOTAL_EFFECTIVE_BYTES} cycles_used=${cycles_used}" \
    | tee -a "$LOG_FILE"

  # milestones: integer and 0.1 steps
  local prev="$PREV_CYCLES_USED"
  local int_prev int_curr step_prev step_curr
  int_prev=$(awk -v x="$prev" 'BEGIN{printf "%d", x}')
  int_curr=$(awk -v x="$cycles_used" 'BEGIN{printf "%d", x}')
  step_prev=$(awk -v x="$prev" 'BEGIN{printf "%.1f", (int(x*10))/10.0 }')
  step_curr=$(awk -v x="$cycles_used" 'BEGIN{printf "%.1f", (int(x*10))/10.0 }')

  if [[ "$int_curr" -gt "$int_prev" ]]; then
    echo "$(now) MARK: completed ${int_curr} P/E cycles (host-definition)" | tee -a "$LOG_FILE"
  elif [[ "$step_curr" != "$step_prev" ]]; then
    echo "$(now) MARK: crossed ${step_curr} P/E cycles" | tee -a "$LOG_FILE"
  fi

  PREV_CYCLES_USED="$cycles_used"
  save_state
}

# ---------- Main ----------
load_state
echo "=== eMMC wear tester start: DEV=$EMMC_DEV, OUT=$DATA_DIR, rated_PE=$RATED_PE_CYCLES ===" | tee -a "$LOG_FILE"
log_life_line "$PREV_A" "$PREV_B" "$PREV_E"
save_state

i=0
while true; do
  # Poll health & log on change
  read -r A B E < <(read_health)
  if [[ "$A$B$E" != "$PREV_A$PREV_B$PREV_E" ]]; then
    log_life_line "$A" "$B" "$E"
    PREV_A="$A"; PREV_B="$B"; PREV_E="$E"
    save_state
  fi
  #if reached_90p "$A" "$B"; then
  #  echo "$(now) threshold reached (≥90%). exiting." | tee -a "$LOG_FILE"
  #  break
  #fi

  # Guard free space -> deletion counted
  # 교체 (기존 코드 대체)
  del_bytes=$(rotate_if_low_free)
  # 공백이나 비숫자 대비 간단 방어
  if [[ "$del_bytes" =~ ^[0-9]+$ && "$del_bytes" != "0" ]]; then
    TOTAL_DELETED_BYTES=$(( TOTAL_DELETED_BYTES + del_bytes ))
    calc_and_log_status
  fi

  # Write 1 GiB
  ts=$(date +%Y%m%d-%H%M%S)
  f="$DATA_DIR/${FILE_PREFIX}-${ts}-$i.dat"
  echo "$(now) writing ${WRITE_SIZE_MIB}MiB -> $f" | tee -a "$LOG_FILE"
  dd if=/dev/zero of="$f" bs=1M count="$WRITE_SIZE_MIB" conv=fsync status=progress 2>>"$LOG_FILE" || \
    echo "$(now) write failed for $f" | tee -a "$LOG_FILE"
  sync

  # Counters & status
  TOTAL_WRITTEN_BYTES=$(( TOTAL_WRITTEN_BYTES + WRITE_SIZE_MIB*1024*1024 ))
  calc_and_log_status

  ((i++))
  sleep "$SLEEP_SEC"
done
