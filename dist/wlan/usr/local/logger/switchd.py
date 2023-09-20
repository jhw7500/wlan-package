#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import subprocess, time, signal, os, logging, logging.handlers, sys, threading
from sUTILS import Logger, _EXTRA_

# ---- 설정 ----
CHIP = "gpiochip1"
LINE = "9"                 # GPIO2_IO9
ACTIVE_LOW = True          # 눌리면 GND로 떨어지는 회로면 True
DEBOUNCE_SEC = 0.05

SHORT_MIN = 0.1            # 1~3초: LED OFF (릴리즈 시 판정)
SHORT_MAX = 3.0
LONG_MIN  = 10.0           # 10초 이상: 프레스 유지 중 즉시 long 실행 (릴리즈 불필요)

LED_BRIGHTNESS = "/sys/class/leds/5VREG_nEN/brightness"
LONG_SCRIPT = "/usr/local/scripts/longpress.sh"

LOG_FACILITY = "local0"
LOG_TAG = "switchd"

#logger = logging.getLogger(LOG_TAG)
#logger.setLevel(logging.INFO)
#try:
#    sh = logging.handlers.SysLogHandler(address="/dev/log", facility=LOG_FACILITY)
#    sh.setFormatter(logging.Formatter('%(name)s[%(process)d]: %(message)s'))
#    logger.addHandler(sh)
#except Exception:
#    pass

#def log(msg):
#    logger.info(msg)
#    print(msg, flush=True)


def kmsg(level: int, msg: str, tag="switchd"):
    """
    level: 0~7 (emerg=0, alert=1, crit=2, err=3, warn=4, notice=5, info=6, debug=7)
    콘솔에 보이려면 console_loglevel >= level 이어야 함.
    """
    line = f"<{level}>{tag}: {msg}\n"
    try:
        with open("/dev/kmsg", "w") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        # 커널 버퍼까지 내려간 뒤 장치 콘솔에도 흘러가게 약간의 유예
        time.sleep(0.01)
        return True
    except Exception as e:
        # kmsg 미지원/권한 문제시 False
        return False

def to_console(msg: str):
    try:
        with open("/dev/console", "w") as f:
            f.write(msg + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True
    except Exception:
        return False

def to_tty(path="/dev/ttyGS0", msg="hello"):
    try:
        with open(path, "w", buffering=1) as f:  # line-buffered
            f.write(msg + "\r\n")
            f.flush()
            os.fsync(f.fileno())
        return True
    except Exception:
        return False

def emit_everywhere(msg: str, level=6, tag="switchd"):
    # 1) 커널 메시지로 (printk 경로)
    if kmsg(level, msg, tag):
        return
    # 2) 시스템 콘솔
    if to_console(f"{tag}: {msg}"):
        return
    # 3) USB 가젯 콘솔(필요시 경로 변경)
    to_tty("/dev/ttyGS0", f"{tag}: {msg}")

_running = True
def _sigterm(_s, _f):
    global _running
    _running = False
signal.signal(signal.SIGINT, _sigterm)
signal.signal(signal.SIGTERM, _sigterm)

def set_led_off():
    try:
        logger.message("emerg", f"power off 5VREG(short press)", _EXTRA_())
        #emit_everywhere("power off 5VREG (short press)", level=6, tag="switchd")
        os.sync()
        time.sleep(0.2)
        if os.path.exists(LED_BRIGHTNESS):
            #with open(LED_BRIGHTNESS, "w") as f:
            #    f.write("0\n")
            return True
        else:
            #emit_everywhere("gpioset", level=6, tag="switchd")
            subprocess.run(["/usr/bin/gpioset","-c","3","29=0"], check=False)
            return False

    except Exception as e:
        logger.message("err", f"LED control failed: {e}", _EXTRA_())

def run_long():
    try:
        if os.path.exists(LONG_SCRIPT) and os.access(LONG_SCRIPT, os.X_OK):
            subprocess.Popen([LONG_SCRIPT], close_fds=True)
            logger.message("info", f"Long-press script executed: {LONG_SCRIPT}", _EXTRA_())
        else:
            logger.message("info", f"Long-press script missing or not executable: {LONG_SCRIPT}", _EXTRA_())
    except Exception as e:
        logger.message("err", f"Script exec failed: {e}", _EXTRA_())

class PressTicker:
    """프레스 중 1초마다 경과시간 로그 + 10초 도달 즉시 long 실행(1회)"""
    def __init__(self):
        self._thr = None
        self._stop = threading.Event()
        self._start_t = 0.0
        self._fired_long = False

    def start(self, start_t: float):
        self._start_t = start_t
        self._fired_long = False
        self._stop.clear()
        self._thr = threading.Thread(target=self._run, daemon=True)
        self._thr.start()

    def _run(self):
        next_tick = 1.0
        while not self._stop.wait(0.05):
            now = time.monotonic()
            dur = now - self._start_t

            # 10초 이상이 되었고 아직 실행 안 했으면 즉시 long 실행
            if (not self._fired_long) and dur >= LONG_MIN:
                run_long()
                self._fired_long = True
                # 계속 경과시간 로그는 유지 (원한다면 여기서 return 해도 됨)

            # 1초 단위로 경과시간 로그
            if dur >= next_tick:
                logger.message("info", f"press elapsed: {int(dur)}s", _EXTRA_())
                next_tick += 1.0

    def stop(self):
        self._stop.set()
        if self._thr:
            self._thr.join(timeout=0.5)

    @property
    def fired_long(self):
        return self._fired_long

def main():
    logger.message("info", f"daemon boot: chip={CHIP} line={LINE} active_low={ACTIVE_LOW}", _EXTRA_())
    #emit_everywhere("Possible reset!!", level=6, tag="switchd")
    #time.sleep(3)

    pressed = False
    t_press = 0.0
    last_ts = 0.0
    ticker = PressTicker()

    while _running:
        # gpiomon: -e both, 내부 풀업(+디바운스) 요청
        cmd = ["/usr/bin/gpiomon", "-c", CHIP, "-e", "both", "-b", "pull-up", "-p", "50ms", LINE]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, bufsize=1)
        except FileNotFoundError:
            logger.message("err", "gpiomon not found. Install gpiod-tools.", _EXTRA_())
            sys.exit(1)

        logger.message("info", f"started: {CHIP} offset {LINE} active_low={ACTIVE_LOW} (via gpiomon)", _EXTRA_())

        while _running:
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                err = (proc.stderr.read() or "").strip()
                logger.message("err", f"gpiomon exited (rc={proc.returncode}) stderr='{err}'", _EXTRA_())
                time.sleep(0.5)
                break

            if not line:
                time.sleep(0.05)
                continue

            now = time.monotonic()
            if last_ts and (now - last_ts) < DEBOUNCE_SEC:
                continue
            last_ts = now

            txt = line.strip().upper()
            if "RISING" in txt:
                is_rising = True
                is_falling = False
            elif "FALLING" in txt:
                is_rising = False
                is_falling = True
            else:
                continue

            # 눌림/떼기 판정
            is_press = is_falling if ACTIVE_LOW else is_rising

            if is_press:
                if not pressed:
                    pressed = True
                    t_press = now
                    logger.message("info", "press", _EXTRA_())
                    ticker.start(t_press)   # 프레스 시작 → 티커 시작 (1초마다 로그 + 10초 즉시 long)
            else:
                if pressed:
                    pressed = False
                    ticker.stop()           # 프레스 종료 → 티커 종료
                    dur = now - t_press
                    logger.message("info", f"release({dur:.2f}s)", _EXTRA_())

                    # long이 이미 발동했다면 릴리즈에서는 추가 동작 없음
                    if ticker.fired_long:
                        logger.message("info", "long already fired during press; release ignored", _EXTRA_())
                        continue

                    # 릴리즈 시 short 판정
                    if SHORT_MIN <= dur <= SHORT_MAX:
                        set_led_off()
                    elif dur < SHORT_MIN:
                        logger.message("info", "no-op (too short)", _EXTRA_())
                    else:
                        # 3~10초 구간 포함하여 릴리즈 시에는 아무 동작 없음 (로그만)
                        logger.message("info", "no-op zone", _EXTRA_())

        # 프로세스 종료 정리 후 재시작 루프 지속
        try:
            proc.terminate()
            proc.wait(timeout=0.5)
        except Exception:
            try: proc.kill()
            except Exception:
                pass

    logger.message("info", "stopped", _EXTRA_())

if __name__ == "__main__":
    logger = Logger(app_name="switchd", facility=logging.handlers.SysLogHandler.LOG_LOCAL0)
    logger.set_log_print("on")
    main()
