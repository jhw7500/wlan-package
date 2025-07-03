import sys
import re
import logging
from sUTILS import Logger, _EXTRA_

IFACE = "mlan0"
BASE_MAC_PATH = "/var/log/cantops/base_mac"
CONF_FILE = "/lib/firmware/nxp/wifi_mod_para__.conf"
MAC_FILE = "/opt/wlan/mac/target0"

def is_valid_mac(mac: str) -> bool:
    return bool(re.fullmatch(r"([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})", mac))

def insert_mac_addr(conf_path, mac_path, target_block="PCIE9098_0"):
    try:
        with open(mac_path, "r") as f:
            mac = f.read().strip()

        if not mac or not is_valid_mac(mac):
            print(f"{mac} is invalid in {mac_path}")
            logger.message("err", f"[{IFACE}] {mac} is invalid in {mac_path}", _EXTRA_())
            try:
                with open(BASE_MAC_PATH, "r") as f:
                    mac = f.read().strip()

                if not mac or not is_valid_mac(mac):
                    print(f"{mac} is invalid in {BASE_MAC_PATH}")
                    logger.message("err", f"[{IFACE}] {mac} is invalid in {BASE_MAC_PATH}", _EXTRA_())
                    sys.exit(1)

            except Exception as e:
                print(f"[ERROR] {e}")
                logger.message("err", f"[{IFACE}] {e}", _EXTRA_())
                sys.exit(1)

        with open(conf_path, "r") as f:
            lines = f.readlines()

        updated_lines = []
        inside_target = False
        block_indent = ""
        inserted = False

        for idx, line in enumerate(lines):
            stripped = line.strip()

            # 타겟 블록 시작
            if stripped.startswith(f"{target_block} ="):
                inside_target = True
                block_indent = line[:line.index(stripped)]
                updated_lines.append(line)
                continue

            if inside_target:
                # mac_addr 항목 수정
                if stripped.startswith("mac_addr="):
                    updated_lines.append(f"{block_indent}    mac_addr={mac}\n")
                    inserted = True
                    continue

                # 블록 종료 시점에 값이 없으면 삽입
                if stripped == "}":
                    if not inserted:
                        updated_lines.append(f"{block_indent}    mac_addr={mac}\n")
                    inside_target = False

            updated_lines.append(line)

        with open(conf_path, "w") as f:
            f.writelines(updated_lines)

        print(f"[INFO] mac_addr={mac} successfully written into block {target_block}.")
        logger.message("info", f"[{IFACE}] mac_addr={mac} successfully written into block {target_block}", _EXTRA_())

    except Exception as e:
        print(f"[ERROR] {e}")
        logger.message("err", f"[{IFACE}] {e}", _EXTRA_())

if __name__ == "__main__":
    logger = Logger(app_name="config_mac", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    if len(sys.argv) < 2:
        print(f"[ERR] few arg 1")
    elif len(sys.argv) < 3:
        print(f"[ERR] few arg 2")
        IFACE = sys.argv[1]        
    else:
        IFACE = sys.argv[1]
        CONF_FILE = f"/lib/firmware/nxp/{sys.argv[2]}"

    if IFACE == "mlan0" :
        block = "PCIE9098_0"
        MAC_FILE = "/opt/wlan/mac/target0"
    elif IFACE == "mlan1" :
        block = "PCIE9098_1"
        MAC_FILE = "/opt/wlan/mac/target1"
    else:
        logger.message("info", f"[{IFACE}] interface is wrong", _EXTRA_())
        block = "PCIE9098_0"
        sys.exit(1)

    print(f"[INFO] iface={IFACE}, conf_file={CONF_FILE}, mac_file={MAC_FILE}, block={block}")
    insert_mac_addr(CONF_FILE, MAC_FILE, block)
