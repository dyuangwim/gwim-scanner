#!/usr/bin/env python3
# main.py (Relay version - safe polarity handling)
# ==========================================================
# This version fixes:
# - buzzer stuck ON at boot due to wrong active level
# - tower lights not responding due to inversion mismatch
# - CSV empty file IndexError (skip + auto remove 0-byte)
# - logs folder writable check & clear guidance
#
# IMPORTANT:
# - For RELAY modules (common): ACTIVE_LOW = True
# - For DIRECT GPIO LED wiring: ACTIVE_LOW = False
# ==========================================================

import os
import csv
import time
import threading
import sys
import subprocess
from datetime import datetime

import pymysql
import keyboard
import RPi.GPIO as GPIO

from config import (
    PRODUCTION_DB, STAFF_DB,
    DEVICE_LINE, DEVICE_ID,
    CSV_FOLDER, LOG_PATH,
    RESET_CODES, SCAN_INTERVAL,
    DB_CONNECT_TIMEOUT, DB_READ_TIMEOUT, DB_WRITE_TIMEOUT,
    UPLOAD_INTERVAL_SEC,
)

# -------------------- OUTPUT POLARITY (MOST IMPORTANT) --------------------
# Relay modules are usually Active-Low (LOW = ON).
# Direct LED wiring is usually Active-High (HIGH = ON).
ACTIVE_LOW = True  # <-- YOU SAID THIS FILE IS FOR RELAY DEVICES => True

# If your buzzer is wired differently from lamps, you can override per-channel:
# True = Active-Low (LOW=ON), False = Active-High (HIGH=ON)
CHANNEL_ACTIVE_LOW = {
    "GREEN":  ACTIVE_LOW,
    "RED":    ACTIVE_LOW,
    "YELLOW": ACTIVE_LOW,
    "BUZZER": ACTIVE_LOW,
}

# -------------------- GPIO PINS (BCM) --------------------
GPIO_GREEN  = 6
GPIO_RED    = 13
GPIO_YELLOW = 19
GPIO_BUZZER = 26

# -------------------- Debug --------------------
DEBUG_MODE = True
def debug(msg: str):
    if DEBUG_MODE:
        print(f"[DEBUG] {msg}")

# -------------------- GPIO init --------------------
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

for pin in (GPIO_GREEN, GPIO_RED, GPIO_YELLOW, GPIO_BUZZER):
    GPIO.setup(pin, GPIO.OUT)

def _pin_write(pin: int, on: bool, active_low: bool):
    """
    Write physical level to pin given desired logical state on/off.
    active_low=True => ON=LOW, OFF=HIGH
    active_low=False => ON=HIGH, OFF=LOW
    """
    if active_low:
        GPIO.output(pin, GPIO.LOW if on else GPIO.HIGH)
    else:
        GPIO.output(pin, GPIO.HIGH if on else GPIO.LOW)

def green(on: bool):  _pin_write(GPIO_GREEN,  on, CHANNEL_ACTIVE_LOW["GREEN"])
def red(on: bool):    _pin_write(GPIO_RED,    on, CHANNEL_ACTIVE_LOW["RED"])
def yellow(on: bool): _pin_write(GPIO_YELLOW, on, CHANNEL_ACTIVE_LOW["YELLOW"])
def buzzer(on: bool): _pin_write(GPIO_BUZZER, on, CHANNEL_ACTIVE_LOW["BUZZER"])

def tower_all_off():
    # logical OFF for all channels
    green(False)
    red(False)
    yellow(False)
    buzzer(False)

# Critical: turn everything off immediately at boot
tower_all_off()

def buzzer_beep(seconds=0.20):
    buzzer(True)
    time.sleep(seconds)
    buzzer(False)

# -------------------- File/Log setup --------------------
os.makedirs(CSV_FOLDER, exist_ok=True)

try:
    sys.stdout = open(LOG_PATH, "a", buffering=1)
    sys.stderr = sys.stdout
except Exception:
    pass

debug("🔁 Script started (log ready)")
debug(f"🟩 GPIO initialized. ACTIVE_LOW={ACTIVE_LOW}, per-channel={CHANNEL_ACTIVE_LOW}")
debug("🧯 Safety: tower_all_off() executed at boot")

def ensure_logs_writable() -> bool:
    test_path = os.path.join(CSV_FOLDER, ".write_test")
    try:
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        debug(f"✅ Logs folder writable: {CSV_FOLDER}")
        return True
    except Exception as e:
        debug(f"❌ Logs folder NOT writable: {CSV_FOLDER} ({e})")
        debug("Fix on Raspberry Pi terminal:")
        debug("  sudo chown -R pi:pi /home/pi/gwim-scanner/logs")
        debug("  sudo chmod -R 775 /home/pi/gwim-scanner/logs")
        return False

LOGS_WRITABLE = ensure_logs_writable()

# -------------------- Helpers --------------------
def safe_int(v):
    try:
        return int(v)
    except Exception:
        return None

def normalize_barcode(code: str) -> str:
    return (
        code.strip()
            .replace("–", "-").replace("−", "-").replace("—", "-")
            .replace("_", "-")
            .upper()
    )

RESET_SET = {normalize_barcode(x) for x in RESET_CODES}

def is_reset_code(barcode: str) -> bool:
    return normalize_barcode(barcode) in RESET_SET

def looks_like_staff_id(barcode: str) -> bool:
    b = normalize_barcode(barcode)
    return any(c.isalpha() for c in b)

def ping_ok(ip="8.8.8.8") -> bool:
    try:
        subprocess.check_output(["ping", "-c", "1", "-W", "1", ip])
        return True
    except Exception:
        return False

def connect_pymysql(db_cfg: dict, dict_cursor=False):
    kwargs = dict(
        host=db_cfg["host"],
        user=db_cfg["user"],
        password=db_cfg["password"],
        database=db_cfg["database"],
        port=int(db_cfg.get("port", 3306)),
        connect_timeout=int(DB_CONNECT_TIMEOUT),
        read_timeout=int(DB_READ_TIMEOUT),
        write_timeout=int(DB_WRITE_TIMEOUT),
        autocommit=True,
    )
    if dict_cursor:
        kwargs["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**kwargs)

# -------------------- CSV storage + upload --------------------
csv_lock = threading.Lock()

def _csv_path_for_muf(muf_no: str) -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    return os.path.join(CSV_FOLDER, f"{muf_no}_{date_str}.csv")

CSV_HEADER = [
    "muf_no", "line", "fg_no", "pack_per_ctn", "pack_per_hr",
    "actual_pack", "ctn_count", "scanned_code", "scanned_count",
    "scanned_at", "scanned_by", "remarks", "is_uploaded"
]

def write_to_csv(row_tuple, muf_no: str, uploaded: int, remarks: str):
    if not LOGS_WRITABLE:
        debug("⚠️ CSV not written (logs folder not writable).")
        return
    path = _csv_path_for_muf(muf_no)
    with csv_lock:
        is_new = not os.path.exists(path)
        try:
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if is_new:
                    w.writerow(CSV_HEADER)
                w.writerow(list(row_tuple) + [remarks, int(uploaded)])
            debug(f"📂 CSV saved: {path} (uploaded={uploaded}, remarks={remarks})")
        except Exception as e:
            debug(f"⚠️ CSV write failed: {path} ({e})")

def upload_from_csv():
    try:
        debug("⏫ Attempting to upload cached CSV data...")

        if not os.path.isdir(CSV_FOLDER):
            debug(f"⚠️ CSV folder not found: {CSV_FOLDER}")
            return

        for file in os.listdir(CSV_FOLDER):
            if not file.endswith(".csv"):
                continue

            path = os.path.join(CSV_FOLDER, file)

            # auto remove empty file (0 bytes) to avoid old IndexError loop
            try:
                if os.path.getsize(path) == 0:
                    debug(f"🧹 Empty (0-byte) CSV found, removing: {path}")
                    try:
                        os.remove(path)
                    except Exception as e:
                        debug(f"⚠️ Cannot remove empty CSV: {path} ({e})")
                    continue
            except Exception as e:
                debug(f"⚠️ Cannot stat CSV: {path} ({e})")
                continue

            with csv_lock:
                try:
                    with open(path, "r", newline="") as f:
                        rows = list(csv.reader(f))
                except Exception as e:
                    debug(f"⚠️ Cannot read CSV: {path} ({e})")
                    continue

            if len(rows) <= 1:
                debug(f"ℹ️ Skip CSV (empty/header-only): {path}")
                continue

            data_rows = rows[1:]

            pending_idx = []
            pending_vals = []
            for idx, r in enumerate(data_rows, start=1):
                if len(r) < 13:
                    continue
                if r[-1] == "0":
                    pending_idx.append(idx)
                    pending_vals.append(r[:12])  # includes remarks at position 11

            if not pending_vals:
                continue

            debug(f"⏫ Pending rows: {len(pending_vals)} in {path}")

            try:
                conn = connect_pymysql(PRODUCTION_DB, dict_cursor=False)
                cur = conn.cursor()
                sql = (
                    "INSERT INTO output_log ("
                    "muf_no, line, fg_no, pack_per_ctn, pack_per_hr, actual_pack, "
                    "ctn_count, scanned_code, scanned_count, scanned_at, scanned_by, remarks"
                    ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                )
                for v in pending_vals:
                    cur.execute(sql, v)
                conn.commit()
                cur.close()
                conn.close()

                with csv_lock:
                    for i in pending_idx:
                        if len(rows[i]) >= 13:
                            rows[i][-1] = "1"
                    try:
                        with open(path, "w", newline="") as f:
                            w = csv.writer(f)
                            w.writerows(rows)
                    except Exception as e:
                        debug(f"⚠️ Failed to mark uploaded in CSV: {path} ({e})")

                debug(f"✅ Uploaded & marked: {path}")

            except Exception as e:
                debug(f"⚠️ Upload failed: {path} ({e})")

    except Exception as e:
        debug(f"⚠️ upload_from_csv unexpected error: {e}")

    threading.Timer(UPLOAD_INTERVAL_SEC, upload_from_csv).start()

# -------------------- Staff IN/OUT --------------------
def is_valid_staff_id(staff_id: str) -> bool:
    staff_id = normalize_barcode(staff_id)
    try:
        conn = connect_pymysql(STAFF_DB, dict_cursor=False)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM staff WHERE staff_id=%s LIMIT 1", (staff_id,))
        ok = cur.fetchone() is not None
        cur.close(); conn.close()
        return ok
    except Exception as e:
        debug(f"Staff DB connection error: {e}")
        return False

def toggle_staff_status(staff_id: str) -> str:
    staff_id = normalize_barcode(staff_id)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = connect_pymysql(STAFF_DB, dict_cursor=False)
        cur = conn.cursor()

        cur.execute("SELECT status FROM staff_status WHERE staff_id=%s LIMIT 1", (staff_id,))
        row = cur.fetchone()

        if row is None:
            cur.execute(
                "INSERT INTO staff_status (staff_id, status, updated_at) VALUES (%s,'IN',%s)",
                (staff_id, now_str)
            )
            conn.commit()
            cur.close(); conn.close()
            return "IN"

        status = row[0]
        if status == "IN":
            cur.execute("UPDATE staff_status SET status='OUT', updated_at=%s WHERE staff_id=%s", (now_str, staff_id))
            conn.commit()
            cur.close(); conn.close()
            return "OUT"
        else:
            cur.execute("UPDATE staff_status SET status='IN', updated_at=%s WHERE staff_id=%s", (now_str, staff_id))
            conn.commit()
            cur.close(); conn.close()
            return "IN"

    except Exception as e:
        debug(f"toggle_staff_status error: {e}")
        return "ERROR"

# -------------------- Production: MUF + output_log insert --------------------
def fetch_muf_info(muf_code: str):
    muf_code = normalize_barcode(muf_code)
    try:
        conn = connect_pymysql(PRODUCTION_DB, dict_cursor=True)
        cur = conn.cursor()
        cur.execute("SELECT * FROM main WHERE muf_no=%s LIMIT 1", (muf_code,))
        row = cur.fetchone()
        cur.close(); conn.close()
        return row
    except Exception as e:
        debug(f"fetch_muf_info DB error: {e}")
        return None

def insert_output_log(data_tuple_11, remarks: str) -> bool:
    try:
        conn = connect_pymysql(PRODUCTION_DB, dict_cursor=False)
        cur = conn.cursor()
        sql = (
            "INSERT INTO output_log ("
            "muf_no, line, fg_no, pack_per_ctn, pack_per_hr, actual_pack, "
            "ctn_count, scanned_code, scanned_count, scanned_at, scanned_by, remarks"
            ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        )
        cur.execute(sql, data_tuple_11 + (remarks,))
        conn.commit()
        cur.close(); conn.close()
        return True
    except Exception as e:
        debug(f"⚠️ DB insert failed: {e}")
        return False

# -------------------- Network indicator (yellow) --------------------
def network_indicator_loop():
    while True:
        try:
            if ping_ok("8.8.8.8"):
                yellow(True)
            else:
                # blink quickly when no internet
                yellow(False); time.sleep(0.2)
                yellow(True);  time.sleep(0.2)
                yellow(False)
        except Exception:
            pass
        time.sleep(10)

# -------------------- Scanning state --------------------
current_batch = None
current_muf = None
muf_info = None
template_code = None
barcode_buffer = ""

last_barcode = None
last_scan_time = 0.0

staff_id = None  # current staff IN

def set_state_reset():
    global current_batch, current_muf, muf_info, template_code
    now = datetime.now()
    current_batch = f"batch_{now.strftime('%Y%m%d_%H%M%S')}"
    current_muf = None
    muf_info = None
    template_code = None

    debug(f"🔄 RESET scanned. New batch: {current_batch}")
    red(False)
    green(False)

def process_and_store(scanned_barcode: str, remarks: str):
    global current_muf, muf_info, staff_id

    pack_per_ctn = safe_int(muf_info.get("pack_per_ctn"))
    pack_per_hr  = safe_int(muf_info.get("pack_per_hr"))

    ctn_count = 1
    actual_pack = (pack_per_ctn * ctn_count) if pack_per_ctn is not None else None

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scanned_by = staff_id if staff_id else DEVICE_ID

    data_11 = (
        current_muf,
        DEVICE_LINE,
        muf_info.get("fg_no"),
        pack_per_ctn,
        pack_per_hr,
        actual_pack,
        ctn_count,
        scanned_barcode,
        1,
        ts,
        scanned_by,
    )

    ok = insert_output_log(data_11, remarks=remarks)
    if ok:
        debug("✅ DB insert successful")
        write_to_csv(data_11, current_muf, uploaded=1, remarks=remarks)
        green(True); red(False)
        buzzer_beep(0.08)
    else:
        debug("⚠️ DB insert failed. Cached locally.")
        write_to_csv(data_11, current_muf, uploaded=0, remarks=remarks)
        buzzer_beep(0.08)

# -------------------- Keyboard handler --------------------
def on_key(event):
    global barcode_buffer, last_barcode, last_scan_time
    global current_batch, current_muf, muf_info, template_code
    global staff_id

    if event.name == "enter":
        barcode = barcode_buffer.strip()
        barcode_buffer = ""
        if not barcode:
            return

        normalized = normalize_barcode(barcode)
        now_ts = time.time()

        if barcode == last_barcode and (now_ts - last_scan_time) < float(SCAN_INTERVAL):
            debug(f"⏱️ Duplicate scan ignored: {barcode}")
            return

        last_barcode = barcode
        last_scan_time = now_ts

        debug(f"📥 Scanned: '{barcode}' -> '{normalized}'")

        # 1) RESET
        if is_reset_code(barcode):
            set_state_reset()
            # blink green to show ready
            green(True); time.sleep(0.2)
            green(False); time.sleep(0.2)
            green(True)
            return

        if not current_batch:
            debug("⚠️ Please scan RESET first.")
            red(True)
            buzzer_beep(0.30)
            return

        # 2) staff
        if looks_like_staff_id(barcode):
            candidate = normalized
            if not is_valid_staff_id(candidate):
                debug(f"❌ Invalid staff ID: {candidate}")
                red(True)
                buzzer_beep(0.30)
                return

            status = toggle_staff_status(candidate)
            if status == "IN":
                staff_id = candidate
                debug(f"👤 Staff IN: {staff_id}")
                green(True); red(False)
                buzzer_beep(0.08)
            elif status == "OUT":
                debug(f"👤 Staff OUT: {candidate}")
                if staff_id == candidate:
                    staff_id = None
                green(False); red(False)
                buzzer_beep(0.08)
            else:
                debug("⚠️ Staff status update ERROR")
                red(True)
                buzzer_beep(0.30)
            return

        # 3) MUF
        if current_muf is None:
            info = fetch_muf_info(normalized)
            if not info:
                debug(f"❌ MUF not found: {normalized}")
                red(True)
                buzzer_beep(0.30)
                return

            current_muf = normalized
            muf_info = info
            template_code = None
            debug(f"✅ MUF set: {current_muf} (fg_no={muf_info.get('fg_no')})")

            # blink green to prompt template
            green(True); time.sleep(0.2)
            green(False); time.sleep(0.2)
            green(True)
            red(False)
            return

        # 4) TEMPLATE
        if template_code is None:
            if normalized == current_muf:
                debug("⚠️ MUF scanned again; ignoring as template.")
                return
            template_code = normalized
            debug(f"🧾 Template set: {template_code}")
            process_and_store(template_code, remarks="TEMPLATE")
            return

        # 5) SCAN must match template
        if normalized != template_code:
            debug(f"❌ Barcode mismatch: {normalized} != {template_code}")
            red(True)
            green(False)
            buzzer_beep(0.35)
            return

        process_and_store(normalized, remarks="SCAN")

    elif len(event.name) == 1:
        barcode_buffer += event.name
    elif event.name == "minus":
        barcode_buffer += "-"

# -------------------- Entry point --------------------
if __name__ == "__main__":
    debug(f"🚀 Starting scanner: line={DEVICE_LINE}, device={DEVICE_ID}")

    # start periodic upload
    upload_from_csv()

    # start yellow lamp network indicator
    threading.Thread(target=network_indicator_loop, daemon=True).start()

    # start listening for barcode
    debug("🧭 Listening for barcode scan via keyboard...")
    keyboard.on_press(on_key)
    keyboard.wait()
