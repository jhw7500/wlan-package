import sys
import logging
from sUTILS import Logger, _EXTRA_

IFACE = "mlan0"

def insert_mac_addr(conf_path, mac_path, target_block="PCIE9098_0"):
    try:
        with open(mac_path, "r") as f:
            mac = f.read().strip()

        with open(conf_path, "r") as f:
            lines = f.readlines()

        updated_lines = []
        inside_target = False
        already_present = False
        block_indent = ""
        block_end_idx = None

        for idx, line in enumerate(lines):
            stripped = line.strip()

            # 타겟 블록 진입
            if stripped.startswith(f"{target_block} ="):
                inside_target = True
                block_indent = line[:line.index(stripped)]
                updated_lines.append(line)
                continue

            # 블록 내 처리
            if inside_target:
                if "mac_addr=" in stripped:
                    already_present = True
                if stripped == "}":
                    block_end_idx = idx
                    if not already_present:
                        updated_lines.append(f"{block_indent}    mac_addr={mac}\n")
                    inside_target = False

            updated_lines.append(line)

        with open(conf_path, "w") as f:
            f.writelines(updated_lines)

        print(f"[INFO] mac_addr={mac} successfully inserted into block {target_block}.")
        logger.message("info", f"[{IFACE}] mac_addr={mac} successfully inserted into block {target_block}", _EXTRA_())

    except Exception as e:
        print(f"[ERROR] {e}")

if __name__ == "__main__":
    logger = Logger(app_name="config_mac", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    if len(sys.argv) < 2:
        IFACE = "mlan0"
    else:
        IFACE = sys.argv[1]

    if IFACE == "mlan0" :
        block = "PCIE9098_0"
    elif IFACE == "mlan1" :
        block = "PCIE9098_1"
    else:
        logger.message("info", f"[{IFACE}] interface is wrong", _EXTRA_())
        block = "PCIE9098_0"

    conf_file = "/lib/firmware/nxp/wifi_mod_para.conf"
    mac_file = "/var/log/cantops/target_mac"
    insert_mac_addr(conf_file, mac_file, block)
