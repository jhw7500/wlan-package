import subprocess
import datetime
import os
import json
import time
import logging
from sUTILS import Logger, _EXTRA_

VERSION="0.0"
LOG_DIR="/var/log/cantops/module/dmesg"
LOG_FILE = "/var/log/cantops/module/mlan_check.log"
COUNT_FILE = "/var/log/cantops/module/mlan_check_count.json"

def interface_exists(name="mlan0"):
    try:
        subprocess.run(["ifconfig", name, "up"])
        output = subprocess.check_output(["ifconfig", name], stderr=subprocess.DEVNULL, text=True)
        return bool(output.strip())  # 출력이 있으면 True, 없으면 False
    except subprocess.CalledProcessError:
        return False

def load_counts():
    if os.path.exists(COUNT_FILE):
        with open(COUNT_FILE, 'r') as f:
            return json.load(f)
    return {"total": 0, "success": 0, "fail": 0}

def save_counts(counts):
    with open(COUNT_FILE, 'w') as f:
        json.dump(counts, f)

def append_log(success):
    now = datetime.datetime.now().isoformat()
    with open(LOG_FILE, 'a') as f:
        f.write(f"{now} - mlan0 {'FOUND' if success else 'NOT FOUND'}\n")

def main():
    #subprocess.run(["/home/root/modules.sh"])
    logger = Logger(app_name="module_check", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)

    logger.message("notice", f"version : {VERSION}, LOG_DIR : {LOG_DIR}", _EXTRA_())

    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)

    counts = load_counts()
    counts["total"] += 1
    now = datetime.datetime.now()
    dmesg_logfile = f"{LOG_DIR}/{now.strftime('%Y%m%d_%H%M%S')}.log"

    retry_success = False

    for i in range(5):
        time.sleep(2)
        if interface_exists():
            retry_success = True
            break

    if retry_success:
        logger.message("notice", f"module init success", _EXTRA_())
        counts["success"] += 1
        append_log(True)
    else:
        logger.message("emerg", f"module init fail", _EXTRA_())
        counts["fail"] += 1
        append_log(False)

        with open(dmesg_logfile, 'w') as df:
            subprocess.run(["dmesg"], stdout=df)

    logger.message("notice", f"success : {counts['success']}, fail : {counts['fail']}, total : {counts['total']}", _EXTRA_())
    save_counts(counts)
    #subprocess.run(["cat", COUNT_FILE])
    with open("/dev/ttymxc1", "w") as tty:
        tty.write("===================================================================================================\n")
        subprocess.run(["cat", COUNT_FILE], stdout=tty)
        tty.write("\n==================================================================================================\n")
    #print("save", flush=True)
    time.sleep(10)
    #print("test", flush=True)
    if not retry_success:
        subprocess.run(["reboot"])


if __name__ == "__main__":
    main()
