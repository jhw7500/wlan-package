#!/usr/bin/env python3
import time
import argparse
from evdev import InputDevice, ecodes, list_devices
import gpiod
import sys
import subprocess
import threading

def find_gpio_keys(dev_name='gpio-keys'):
    for p in list_devices():
        d = InputDevice(p)
        if d.name == dev_name:
            return d
    raise RuntimeError(f"input device '{dev_name}' not found")

def pulse_line(req, line, active_high=True, ms=500):
    val_on  = 1 if active_high else 0
    val_off = 0 if active_high else 1
    req.set_value(line, val_on)
    time.sleep(ms/1000.0)
    req.set_value(line, val_off)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--key-name', default='gpio-keys')
    ap.add_argument('--key-code', default='KEY_PROG1')     # gpio-keys DTS에서 지정한 code
    ap.add_argument('--chip', default='gpiochip0')         # 예: gpiochip3
    ap.add_argument('--line', type=int, required=True)     # 예: 29 (GPIO4_IO29의 해당 chip offset)
    ap.add_argument('--out-active-high', action='store_true', help='출력 High가 활성(기본은 High=1)')
    ap.add_argument('--short-max-sec', type=float, default=2.0)   # <2s: short
    ap.add_argument('--long-min-sec',  type=float, default=10.0)  # >=10s: long
    ap.add_argument('--pulse-ms', type=int, default=500)          # short 시 High 유지 시간
    ap.add_argument('--factory-cmd', default='', help='long-press 시 실행할 명령(비우면 실행 안함)')
    args = ap.parse_args()

    # 입력(키) 디바이스
    dev = find_gpio_keys(args.key-name if hasattr(args, 'key-name') else args.key_name)
    dev.grab()  # 다른 프로세스가 가로채지 않게(필요없으면 주석)
    key_code = getattr(ecodes, args.key_code, ecodes.KEY_PROG1)

    # 출력 GPIO 준비
    chip = gpiod.Chip(args.chip)
    line = args.line
    cfg = gpiod.LineSettings(direction=gpiod.LineDirection.OUTPUT, output_value=(0 if args.out_active_high else 1))
    req = chip.request_lines(config={line: cfg})

    press_t = None
    long_fired = False
    long_thread = None
    stop_flag = False

    def long_watch():
        nonlocal long_fired
        while not stop_flag:
            if press_t is not None:
                if (time.monotonic() - press_t) >= args.long_min_sec and not long_fired:
                    long_fired = True
                    if args.factory_cmd:
                        try:
                            subprocess.Popen(args.factory_cmd, shell=True)
                        except Exception as e:
                            print(f"[btn] factory_cmd failed: {e}", file=sys.stderr)
            time.sleep(0.05)

    long_thread = threading.Thread(target=long_watch, daemon=True)
    long_thread.start()

    print(f"[btn] listening on {dev.path} ({dev.name}) code={args.key_code}, driving {args.chip}:{line}")

    try:
        for e in dev.read_loop():
            if e.type != ecodes.EV_KEY or e.code != key_code:
                continue
            # 1=press, 0=release, 2=repeat
            if e.value == 1:
                press_t = time.monotonic()
                long_fired = False
            elif e.value == 0:
                if press_t is None:
                    continue
                dur = time.monotonic() - press_t
                # 짧은 눌림: long 동작이 아직 안 나갔고, dur < short_max
                if not long_fired and dur < args.short_max_sec:
                    try:
                        pulse_line(req, line, active_high=args.out_active_high, ms=args.pulse_ms)
                    except Exception as ex:
                        print(f"[btn] pulse error: {ex}", file=sys.stderr)
                press_t = None
            # value==2 (repeat)는 무시
    finally:
        stop_flag = True
        try:
            req.set_value(line, (0 if args.out_active_high else 1))
        except Exception:
            pass

if __name__ == '__main__':
    main()
