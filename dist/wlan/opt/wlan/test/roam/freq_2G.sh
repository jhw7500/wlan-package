#!/bin/sh
# toggle_scan_freq.sh <wpa_supplicant.conf>

set -u

if [ $# -ne 1 ]; then
  #echo "Usage: $0 /path/to/wpa_supplicant.conf" >&2
  #exit 1
  FILE="/etc/wpa_supplicant/wpa_supplicant-mlan0.conf"
else
  FILE="$1"
fi

[ -f "$FILE" ] || { echo "No such file: $FILE" >&2; exit 2; }

BACKUP="${FILE}.bak.$(date +%Y%m%d%H%M%S)"
cp -a "$FILE" "$BACKUP" || exit 3
echo "Backup: $BACKUP"

# 1) 5GHz 라인(예: 5180/5200/5220/5240 포함) 주석 해제
# 2) 2.4GHz 라인(예: 2412/2437/2462/2472 포함) 주석 처리
# 3) scan_freq와 sca_freq(오타) 모두 대응
#    -E: 확장 정규식. \b 경계와 [[:space:]] 사용.
sed -i -E \
  -e 's/^([[:space:]]*)#([[:space:]]*)((scan|sca)_freq=([^#]*\b24[0-9]{2}\b.*))$/\1\3/' \
  -e 's/^([[:space:]]*)((scan|sca)_freq=([^#]*\b5[0-9]{3}\b.*))$/#\1\2/' \
  "$FILE"

echo "Done. Preview of scan/sca lines:"
grep -nE '^[[:space:]]*#?[[:space:]]*(scan_freq|sca_freq)=' "$FILE" || true

# 참고: 적용 즉시 재연결하려면(서비스 재시작 대신 깔끔하게)
# wpa_cli -i mlan0 reconfigure
# wpa_cli -i mlan0 disconnect
# wpa_cli -i mlan0 reassociate
