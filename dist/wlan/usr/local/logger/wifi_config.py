import sys
import logging
from sUTILS import Logger, _EXTRA_

IFACE = "mlan0"

def insert_mac_addr(conf_path, conf, val, target_block="PCIE9098_0"):
    try:
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
                if stripped.startswith(f"{conf}="):
                    updated_lines.append(f"{block_indent}    {conf}={val}\n")
                    inserted = True
                    continue

                # 블록 종료 시점에 값이 없으면 삽입
                if stripped == "}":
                    if not inserted:
                        updated_lines.append(f"{block_indent}    {conf}={val}\n")
                    inside_target = False

            updated_lines.append(line)

        with open(conf_path, "w") as f:
            f.writelines(updated_lines)

        print(f"[INFO] {conf}={val} successfully written into block {target_block}.")
        logger.message("info", f"[{IFACE}] {conf}={val} successfully written into block {target_block}", _EXTRA_())

    except Exception as e:
        print(f"[ERROR] {e}")

if __name__ == "__main__":
    logger = Logger(app_name="wifi_config", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    if len(sys.argv) < 4:
        logger.message("err", f"[{IFACE}] arg is wrong", _EXTRA_())
    else:
        IFACE = sys.argv[1]
        conf = sys.argv[2]
        val = sys.argv[3]

    conf_file = "/lib/firmware/nxp/wifi_mod_para.conf"

    if IFACE == "mlan0" or IFACE == "0" :
        block = "PCIE9098_0"
        insert_mac_addr(conf_file, conf, val, block)        
    elif IFACE == "mlan1" or IFACE == "1" :
        block = "PCIE9098_1"
        insert_mac_addr(conf_file, conf, val, block)
    elif IFACE == "2":
        block = "PCIE9098_0"
        insert_mac_addr(conf_file, conf, val, block)
        block = "PCIE9098_1"
        insert_mac_addr(conf_file, conf, val, block)
    else:
        logger.message("info", f"[{IFACE}] interface is wrong", _EXTRA_())

