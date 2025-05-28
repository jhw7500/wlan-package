import subprocess
import datetime
import os
import sys
import json
import time
import logging
from sUTILS import Logger, _EXTRA_

IFACE="mlan0"
VERSION="0.0"
DLOG_DIR="/var/log/cantops/module/dmesg"
LOG_DIR="/var/log/cantops/module"
LOG_FILE = "module.log"
COUNT_FILE = "count.json"

def interface_exists(name="mlan0"):
    try:
        subprocess.run(["ifconfig", name, "up"])
        output = subprocess.check_output(["ifconfig", name], stderr=subprocess.DEVNULL, text=True)
        return bool(output.strip())  # 출력이 있으면 True, 없으면 False
    except subprocess.CalledProcessError:
        return False

def load_counts(log_dir=LOG_DIR):
    log_path = f"{log_dir}/{COUNT_FILE}"
    if os.path.exists(log_path):
        with open(log_path, 'r') as f:
            return json.load(f)
    return {"total": 0, "success": 0, "fail": 0}

def save_counts(counts, log_dir=LOG_DIR):
    log_path = f"{log_dir}/{COUNT_FILE}"
    with open(log_path, 'w') as f:
        json.dump(counts, f)

def append_log(success):
    now = datetime.datetime.now().isoformat()
    with open(LOG_FILE, 'a') as f:
        f.write(f"{now} - mlan0 {'FOUND' if success else 'NOT FOUND'}\n")

def main():
    #subprocess.run(["/home/root/modules.sh"])
    logger = Logger(app_name="module_check", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    if len(sys.argv) < 2:
        IFACE = "mlan0"
    else:
        IFACE = sys.argv[1]

    LOG_DIR = f"/var/log/cantops/module/{IFACE}"
    logger.message("notice", f"iface : {IFACE}, version : {VERSION}, LOG_DIR : {LOG_DIR}", _EXTRA_())

    if IFACE != "mlan0" and IFACE != "mlan1" :
        logger.message("err", f"{IFACE} is not vaild interface", _EXTRA_())

    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)

    if not os.path.exists(DLOG_DIR):
        os.makedirs(DLOG_DIR, exist_ok=True)

    counts = load_counts(LOG_DIR)
    counts["total"] += 1
    now = datetime.datetime.now()
    dmesg_logfile = f"{DLOG_DIR}/{now.strftime('%Y%m%d_%H%M%S')}.log"

    retry_success = False
    time.sleep(10)

    for i in range(3):
        if interface_exists():
            retry_success = True
            break
        time.sleep(3)

    if retry_success:
        logger.message("notice", f"module init success", _EXTRA_())
        counts["success"] += 1
        #append_log(True)
    else:
        logger.message("emerg", f"module init fail", _EXTRA_())
        counts["fail"] += 1
        #append_log(False)

        with open(dmesg_logfile, 'w') as df:
            subprocess.run(["dmesg"], stdout=df)

    logger.message("notice", f"{IFACE} init success : {counts['success']}, fail : {counts['fail']}, total : {counts['total']}", _EXTRA_())
    save_counts(counts, LOG_DIR)
    #subprocess.run(["cat", COUNT_FILE])
    with open("/dev/ttymxc1", "w") as tty:
        tty.write("===================================================================================================\n")
        subprocess.run(["cat", COUNT_FILE], stdout=tty)
        tty.write("\n==================================================================================================\n")
    #print("save", flush=True)
    #time.sleep(10)
    #print("test", flush=True)
    if not retry_success:
        time.sleep(5)
        subprocess.run(["reboot"])


if __name__ == "__main__":
    main()
