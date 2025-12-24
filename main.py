#!/usr/bin/env python3
import os
import csv
import time
import threading
from datetime import datetime
import sys
import subprocess
import keyboard
import RPi.GPIO as GPIO
import pymysql
import mysql.connector
import calendar

from config import (
    PRODUCTION_DB, STAFF_DB,
    DEVICE_LINE, DEVICE_ID,
    CSV_FOLDER, LOG_PATH,
    RESET_CODES, SCAN_INTERVAL,
    DB_CONNECT_TIMEOUT, DB_READ_TIMEOUT, DB_WRITE_TIMEOUT,
    UPLOAD_INTERVAL_SEC,
)

# ===================== SETTINGS =====================
DEBUG_MODE = True

# Error alert mode:
#   "blink" -> (same as your old perfect code) red blink + buzzer beep pattern
#   "solid" -> red ON continuously + buzzer ON continuously
ERROR_ALERT_MODE = "blink"   # <-- if you want continuous ON, change to "solid"

# Relay polarity (your old code assumes LOW=ON HIGH=OFF)
ACTIVE_LOW = False # HF6 using Relay so using "True", others line no using Relay so using "False"
CHANNEL_ACTIVE_LOW = {
    "RED": ACTIVE_LOW,
    "GREEN": ACTIVE_LOW,
    "YELLOW": ACTIVE_LOW,
    "BUZZER": ACTIVE_LOW,
}
# If any channel wiring differs, override:
# CHANNEL_ACTIVE_LOW["BUZZER"] = False
# CHANNEL_ACTIVE_LOW["RED"] = False

def debug(msg):
    if DEBUG_MODE:
        print(f"[DEBUG] {msg}")

# ===================== GPIO Setup =====================
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

RED_PIN = 5
GREEN_PIN = 6
YELLOW_PIN = 13
BUZZER_PIN = 19

GPIO.setup(RED_PIN, GPIO.OUT)
GPIO.setup(GREEN_PIN, GPIO.OUT)
GPIO.setup(YELLOW_PIN, GPIO.OUT)
GPIO.setup(BUZZER_PIN, GPIO.OUT)

def _pin_write(pin: int, on: bool, active_low: bool):
    if active_low:
        GPIO.output(pin, GPIO.LOW if on else GPIO.HIGH)
    else:
        GPIO.output(pin, GPIO.HIGH if on else GPIO.LOW)

def set_light(pin, state=True):
    # state=True means ON logically
    if pin == RED_PIN:
        _pin_write(pin, state, CHANNEL_ACTIVE_LOW["RED"])
    elif pin == GREEN_PIN:
        _pin_write(pin, state, CHANNEL_ACTIVE_LOW["GREEN"])
    elif pin == YELLOW_PIN:
        _pin_write(pin, state, CHANNEL_ACTIVE_LOW["YELLOW"])
    elif pin == BUZZER_PIN:
        _pin_write(pin, state, CHANNEL_ACTIVE_LOW["BUZZER"])
    else:
        GPIO.output(pin, GPIO.LOW if state else GPIO.HIGH)

# init OFF
set_light(RED_PIN, False)
set_light(GREEN_PIN, False)
set_light(YELLOW_PIN, False)
set_light(BUZZER_PIN, False)

# ===================== Log redirect =====================
try:
    sys.stdout = open(LOG_PATH, "a", buffering=1)
    sys.stderr = sys.stdout
    debug("🔁 Script started (log ready)")
except Exception as e:
    with open("/home/pi/gwim-scanner/gwim_fallback.txt", "a") as f:
        f.write(f"Logging failed: {e}\n")

# ===================== Logs writable check (for your old chown issue) =====================
os.makedirs(CSV_FOLDER, exist_ok=True)

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

# ===================== State Control (exactly like your old) =====================
green_blink_running = True
green_blink_thread = None

red_alert_active = False
red_alert_thread = None
buzzer_alert_active = False
buzzer_alert_thread = None

def blink_light(pin, duration=0.3, times=3):
    for _ in range(times):
        set_light(pin, True)
        time.sleep(duration)
        set_light(pin, False)
        time.sleep(duration)

def buzz(times=1, duration=0.15):
    for _ in range(times):
        set_light(BUZZER_PIN, True)
        time.sleep(duration)
        set_light(BUZZER_PIN, False)
        time.sleep(0.1)

def continuous_green_blink():
    global green_blink_running
    # Fast blink 5 times
    for _ in range(5):
        set_light(GREEN_PIN, True)
        time.sleep(0.2)
        set_light(GREEN_PIN, False)
        time.sleep(0.1)
    # Slow blink until stopped
    while green_blink_running:
        set_light(GREEN_PIN, True)
        time.sleep(0.5)
        set_light(GREEN_PIN, False)
        time.sleep(0.5)
    set_light(GREEN_PIN, False)

def continuous_red_alert():
    global red_alert_active
    while red_alert_active:
        if ERROR_ALERT_MODE == "solid":
            set_light(RED_PIN, True)
            time.sleep(0.1)
        else:
            set_light(RED_PIN, True); time.sleep(0.5)
            set_light(RED_PIN, False); time.sleep(0.5)
    set_light(RED_PIN, False)

def continuous_buzzer_alert():
    global buzzer_alert_active
    while buzzer_alert_active:
        if ERROR_ALERT_MODE == "solid":
            set_light(BUZZER_PIN, True)
            time.sleep(0.1)
        else:
            set_light(BUZZER_PIN, True); time.sleep(0.15)
            set_light(BUZZER_PIN, False); time.sleep(0.5)
    set_light(BUZZER_PIN, False)

def stop_all_alerts():
    global red_alert_active, buzzer_alert_active, red_alert_thread, buzzer_alert_thread
    debug("Stopping all active alerts...")
    red_alert_active = False
    buzzer_alert_active = False

    if red_alert_thread and red_alert_thread.is_alive():
        red_alert_thread.join(timeout=0.6)
    set_light(RED_PIN, False)

    if buzzer_alert_thread and buzzer_alert_thread.is_alive():
        buzzer_alert_thread.join(timeout=0.6)
    set_light(BUZZER_PIN, False)
    debug("All alerts stopped.")

def start_red_buzzer_alert():
    global red_alert_active, buzzer_alert_active, red_alert_thread, buzzer_alert_thread

    debug(f"🚨 START ALERT (mode={ERROR_ALERT_MODE})")
    red_alert_active = True
    buzzer_alert_active = True

    # IMPORTANT: always restart threads if they died
    if not (red_alert_thread and red_alert_thread.is_alive()):
        red_alert_thread = threading.Thread(target=continuous_red_alert, daemon=True)
        red_alert_thread.start()

    if not (buzzer_alert_thread and buzzer_alert_thread.is_alive()):
        buzzer_alert_thread = threading.Thread(target=continuous_buzzer_alert, daemon=True)
        buzzer_alert_thread.start()

# ===================== Internet Yellow (same as your old) =====================
yellow_checker_timer = None

def check_internet():
    try:
        return subprocess.call(
            ["ping", "-c", "1", "-W", "1", "8.8.8.8"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ) == 0
    except Exception:
        return False

def update_yellow_light():
    global yellow_checker_timer
    if yellow_checker_timer and yellow_checker_timer.is_alive():
        yellow_checker_timer.cancel()

    if check_internet():
        set_light(YELLOW_PIN, True)
    else:
        blink_light(YELLOW_PIN, duration=0.5, times=1)

    yellow_checker_timer = threading.Timer(10, update_yellow_light)
    yellow_checker_timer.daemon = True
    yellow_checker_timer.start()

update_yellow_light()

# ===================== Helpers =====================
def safe_int(value):
    try:
        return int(value)
    except Exception:
        return None

def normalize_barcode(code):
    return (
        code.strip()
            .replace("–", "-")
            .replace("−", "-")
            .replace("—", "-")
            .replace("_", "-")
            .upper()
    )

def is_reset_code(barcode):
    return normalize_barcode(barcode) in {normalize_barcode(r) for r in RESET_CODES}

def resolve_image_url(path):
    path = (path or "").strip().lstrip("../")
    return f"http://192.168.20.17/{path}"

# ===================== DB Connect helpers (NEW, minimal) =====================
def connect_production(dict_cursor=False):
    kwargs = dict(
        host=PRODUCTION_DB["host"],
        user=PRODUCTION_DB["user"],
        password=PRODUCTION_DB["password"],
        database=PRODUCTION_DB["database"],
        port=int(PRODUCTION_DB.get("port", 3306)),
        connect_timeout=int(DB_CONNECT_TIMEOUT),
        read_timeout=int(DB_READ_TIMEOUT),
        write_timeout=int(DB_WRITE_TIMEOUT),
        autocommit=True,
    )
    if dict_cursor:
        kwargs["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**kwargs)

# ===================== Global vars =====================
current_batch = None
current_muf = None
template_code = None
muf_info = None
last_scan_time = 0
last_barcode = None
barcode_buffer = ""
staff_id = None

csv_lock = threading.Lock()

# ===================== MUF query =====================
def fetch_muf_info(cursor, muf_code):
    debug(f"Querying table 'main' for muf_no = '{muf_code}'")
    cursor.execute("SELECT * FROM main WHERE muf_no = %s", (muf_code,))
    return cursor.fetchone()

# ===================== CSV write (keep, but add safety) =====================
CSV_HEADER = [
    "muf_no", "line", "fg_no", "pack_per_ctn", "pack_per_hr",
    "actual_pack", "ctn_count", "scanned_code", "scanned_count",
    "scanned_at", "scanned_by", "remarks", "is_uploaded"
]

def write_to_csv(data_11, muf_no, uploaded=0, remarks=""):
    if not LOGS_WRITABLE:
        debug("⚠️ logs not writable; CSV not saved.")
        return

    with csv_lock:
        filename = os.path.join(CSV_FOLDER, f"{muf_no}_{datetime.now().strftime('%Y%m%d')}.csv")
        is_new = not os.path.exists(filename)
        try:
            with open(filename, "a", newline="") as f:
                writer = csv.writer(f)
                if is_new:
                    writer.writerow(CSV_HEADER)
                writer.writerow(list(data_11) + [remarks, int(uploaded)])
            debug(f"📂 Written to CSV: {filename} (uploaded={uploaded}, remarks={remarks})")
        except Exception as e:
            debug(f"⚠️ CSV write failed: {e}")

# ===================== Insert output_log (minimal change) =====================
def process_and_store(barcode, muf_info_dict, remarks=""):
    pack_per_ctn = safe_int(muf_info_dict.get("pack_per_ctn"))
    ctn_count = 1
    actual_pack = pack_per_ctn * ctn_count if pack_per_ctn is not None else None

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    data_11 = (
        current_muf,
        DEVICE_LINE,
        muf_info_dict.get("fg_no"),
        pack_per_ctn,
        safe_int(muf_info_dict.get("pack_per_hr")),
        actual_pack,
        ctn_count,
        normalize_barcode(barcode),
        1,
        timestamp,
        staff_id if staff_id else DEVICE_ID,
    )

    try:
        conn = connect_production(dict_cursor=False)
        cursor = conn.cursor()
        sql = (
            "INSERT INTO output_log ("
            "muf_no, line, fg_no, pack_per_ctn, pack_per_hr, actual_pack, "
            "ctn_count, scanned_code, scanned_count, scanned_at, scanned_by, remarks"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        cursor.execute(sql, data_11 + (remarks,))
        conn.commit()
        conn.close()

        debug("✅ DB insert successful")
        write_to_csv(data_11, current_muf, uploaded=1, remarks=remarks)

    except Exception as e:
        debug(f"⚠️ DB insert failed. Cached locally: {e}")
        write_to_csv(data_11, current_muf, uploaded=0, remarks=remarks)

# ===================== Upload pending CSV (fix 0-byte/empty) =====================
def upload_from_csv():
    debug("⏫ Attempting to upload cached CSV data...")

    if not os.path.isdir(CSV_FOLDER):
        return

    for file in os.listdir(CSV_FOLDER):
        if not file.endswith(".csv"):
            continue

        path = os.path.join(CSV_FOLDER, file)

        # Fix: remove 0-byte CSV
        try:
            if os.path.getsize(path) == 0:
                debug(f"🧹 Removing 0-byte CSV: {path}")
                try:
                    os.remove(path)
                except Exception:
                    pass
                continue
        except Exception:
            continue

        with csv_lock:
            try:
                with open(path, "r", newline="") as f:
                    reader = list(csv.reader(f))
            except Exception:
                continue

        if len(reader) <= 1:
            debug(f"ℹ️ Skip CSV (empty/header-only): {path}")
            continue

        headers = reader[0]
        data_rows = reader[1:]

        # ensure header has our required fields
        if "is_uploaded" not in headers:
            debug(f"⚠️ CSV header unexpected, skip: {path}")
            continue

        idx_uploaded = headers.index("is_uploaded")
        idx_remarks = headers.index("remarks") if "remarks" in headers else None

        pending = []
        pending_row_index = []

        for i, row in enumerate(data_rows, start=1):
            if len(row) <= idx_uploaded:
                continue
            if row[idx_uploaded] == "0":
                pending.append(row)
                pending_row_index.append(i)

        if not pending:
            continue

        try:
            conn = connect_production(dict_cursor=False)
            cursor = conn.cursor()

            sql = (
                "INSERT INTO output_log ("
                "muf_no, line, fg_no, pack_per_ctn, pack_per_hr, actual_pack, "
                "ctn_count, scanned_code, scanned_count, scanned_at, scanned_by, remarks"
                ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            )

            for row in pending:
                # Map using our known header order CSV_HEADER
                # If old files exist, still handle by header index
                def get(col):
                    return row[headers.index(col)] if col in headers and headers.index(col) < len(row) else None

                data_to_insert = (
                    get("muf_no"),
                    get("line"),
                    get("fg_no"),
                    get("pack_per_ctn"),
                    get("pack_per_hr"),
                    get("actual_pack"),
                    get("ctn_count"),
                    get("scanned_code"),
                    get("scanned_count"),
                    get("scanned_at"),
                    get("scanned_by"),
                    get("remarks") if idx_remarks is not None else "",
                )
                cursor.execute(sql, data_to_insert)

            conn.commit()
            conn.close()

            # mark uploaded=1
            with csv_lock:
                for i in pending_row_index:
                    if len(reader[i]) > idx_uploaded:
                        reader[i][idx_uploaded] = "1"
                with open(path, "w", newline="") as f:
                    w = csv.writer(f)
                    w.writerows(reader)

            debug(f"✅ Upload complete and marked: {path}")

        except Exception as e:
            debug(f"⚠️ Upload failed: {e}")

    threading.Timer(UPLOAD_INTERVAL_SEC, upload_from_csv).start()

# ===================== Staff verification (keep old behavior, use STAFF_DB) =====================
def is_valid_staff_id(staff_id_in):
    try:
        debug("Connecting to allocation_m3 for staff verification...")
        connection = mysql.connector.connect(
            host=STAFF_DB["host"],
            port=int(STAFF_DB.get("port", 3306)),
            user=STAFF_DB["user"],
            password=STAFF_DB["password"],
            database=STAFF_DB["database"]
        )
        cursor = connection.cursor()
        cursor.execute("SELECT staffid FROM staff_list WHERE staffpos = 'OPERATOR'")
        valid_ids = {row[0].strip().upper() for row in cursor.fetchall()}
        connection.close()
        debug(f"Retrieved {len(valid_ids)} staff IDs")
        return staff_id_in.upper() in valid_ids
    except Exception as e:
        debug(f"Staff DB connection error: {e}")
        return False

# ===================== Barcode listener (KEEP YOUR PERFECT FLOW ORDER) =====================
def on_key(event):
    global barcode_buffer, last_barcode, last_scan_time
    global current_batch, current_muf, template_code, muf_info, staff_id
    global green_blink_running, green_blink_thread

    if event.name == "enter":
        barcode = barcode_buffer.strip()
        normalized_barcode = normalize_barcode(barcode)
        barcode_buffer = ""

        now = datetime.now()
        now_ts = time.time()

        if barcode == last_barcode and now_ts - last_scan_time < SCAN_INTERVAL:
            debug(f"⏱️ Duplicate scan ignored: {barcode}")
            return

        last_barcode = barcode
        last_scan_time = now_ts

        debug(f"📥 Scanned barcode: '{barcode}' → normalized: '{normalized_barcode}'")
        debug(f"STATE before: batch={current_batch}, muf={current_muf}, template={template_code}, staff={staff_id}")

        # stop alerts first (exact old behavior)
        stop_all_alerts()

        # RESET
        if is_reset_code(barcode):
            debug("🔄 RESET scanned. Starting new batch")
            current_batch = f"batch_{now.strftime('%Y%m%d_%H%M%S')}"
            current_muf = None
            template_code = None
            muf_info = None

            green_blink_running = False
            if green_blink_thread and green_blink_thread.is_alive():
                green_blink_thread.join(timeout=1)
            set_light(GREEN_PIN, False)

            green_blink_running = True
            green_blink_thread = threading.Thread(target=continuous_green_blink, daemon=True)
            green_blink_thread.start()
            debug("✅ Green light blinking restarted (RESET)")
            return

        # Staff
        if any(c.isalpha() for c in normalized_barcode):
            debug("Detected alpha -> treat as staff barcode")
            # (keep your old staff IN/OUT block if you want full attendance logic;
            #  you can paste it here unchanged. For now, we just validate and store session.)
            if staff_id is None:
                if not is_valid_staff_id(normalized_barcode):
                    debug(f"Invalid staff ID: {normalized_barcode}")
                    start_red_buzzer_alert()
                    return
                staff_id = normalized_barcode
                debug(f"✅ Staff IN: {staff_id}")
                blink_light(GREEN_PIN, times=1)
                buzz(times=1)
                return
            elif normalized_barcode == staff_id:
                debug(f"🔁 Staff OUT: {staff_id}")
                staff_id = None
                blink_light(GREEN_PIN, times=1)
                buzz(times=1)
                return
            else:
                debug(f"⚠️ Different staff scanned while staff session active. current={staff_id}, scanned={normalized_barcode}")
                start_red_buzzer_alert()
                return

        # Must RESET first
        if not current_batch:
            debug("⚠️ Please scan RESET first.")
            start_red_buzzer_alert()
            return

        # MUF stage
        if current_muf is None:
            try:
                clean = normalize_barcode(barcode)
                conn = connect_production(dict_cursor=True)
                cursor = conn.cursor()
                muf_info = fetch_muf_info(cursor, clean)
                conn.close()

                if muf_info:
                    current_muf = clean
                    debug(f"✅ MUF found: {current_muf}")
                    debug("Green continues blinking until template set.")
                else:
                    debug(f"❌ MUF not found: {clean}")
                    start_red_buzzer_alert()
                return

            except Exception as e:
                debug(f"⚠️ DB connection error: {e}")
                start_red_buzzer_alert()
                return

        # Template stage
        if template_code is None:
            normalized = normalize_barcode(barcode)
            if normalized == current_muf:
                debug(f"⚠️ Duplicate MUF barcode scanned as template: {normalized}")
                start_red_buzzer_alert()
                return

            template_code = normalized
            debug(f"🧾 Template barcode set: {template_code}")

            green_blink_running = False
            if green_blink_thread and green_blink_thread.is_alive():
                green_blink_thread.join(timeout=1)

            set_light(GREEN_PIN, True)  # solid ON
            debug("✅ Green light solid ON (Template Set)")

            process_and_store(barcode, muf_info, remarks="TEMPLATE")
            return

        # MISMATCH stage (THIS MUST ALERT)
        if normalize_barcode(barcode) != template_code:
            debug(f"❌ Carton mismatch! scanned={normalize_barcode(barcode)} != template={template_code}")
            start_red_buzzer_alert()
            return

        # MATCH stage
        debug(f"✅ Carton matches template: {template_code}")
        process_and_store(template_code, muf_info, remarks="SCAN")
        return

    elif len(event.name) == 1:
        barcode_buffer += event.name
    elif event.name == "minus":
        barcode_buffer += "-"

# ===================== Main =====================
if __name__ == "__main__":
    debug("🔌 GPIO initialized")
    debug(f"ERROR_ALERT_MODE={ERROR_ALERT_MODE}")
    debug(f"CHANNEL_ACTIVE_LOW={CHANNEL_ACTIVE_LOW}")

    upload_from_csv()

    green_blink_thread = threading.Thread(target=continuous_green_blink, daemon=True)
    green_blink_thread.start()
    debug("Initial green light blinking started.")

    debug("🧭 Listening for barcode scan via keyboard...")
    keyboard.on_press(on_key)
    keyboard.wait()


