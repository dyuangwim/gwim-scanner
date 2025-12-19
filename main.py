#!/usr/bin/env python3
# main.py (Option 1 - Intranet DB + Perfect tower-light behavior + Relay safe polarity)
# =================================================================================
# PERFECT FLOW (matches your old “perfect”):
# GREEN:
#   - boot: fast blink 5 times (0.2 ON / 0.1 OFF)
#   - waiting RESET: slow blink (0.5 ON / 0.5 OFF)
#   - RESET scanned: restart fast->slow blinking
#   - MUF valid: keep blinking
#   - Template set: green solid ON
#
# YELLOW:
#   - internet ok: solid ON
#   - internet down: blink once (0.5s ON) every 10 sec
#
# ERROR (IMPORTANT FIX):
#   - error: red blinks continuously AND buzzer stays ON continuously
#   - stop alerts on ANY next scan
#   - uses persistent threads + Event (no race condition, never “red only no buzzer”)
#
# DB/CSV:
#   - inserts into intranet DB (PRODUCTION_DB)
#   - always writes CSV; marks uploaded=1 when DB insert ok
#   - upload_from_csv: removes 0-byte files; skips empty/header-only; uploads pending rows
# =================================================================================

import os
import csv
import time
import threading
import sys
import subprocess
from datetime import datetime
import calendar

import pymysql
import keyboard
import RPi.GPIO as GPIO
import mysql.connector

from config import (
    PRODUCTION_DB, STAFF_DB,
    DEVICE_LINE, DEVICE_ID,
    CSV_FOLDER, LOG_PATH,
    RESET_CODES, SCAN_INTERVAL,
    DB_CONNECT_TIMEOUT, DB_READ_TIMEOUT, DB_WRITE_TIMEOUT,
    UPLOAD_INTERVAL_SEC,
)

# -------------------- Debug --------------------
DEBUG_MODE = True
def debug(msg: str):
    if DEBUG_MODE:
        print(f"[DEBUG] {msg}")

# -------------------- OUTPUT POLARITY (Relay vs Direct LED) --------------------
ACTIVE_LOW = True  # relay device typical
CHANNEL_ACTIVE_LOW = {
    "RED":    ACTIVE_LOW,
    "GREEN":  ACTIVE_LOW,
    "YELLOW": ACTIVE_LOW,
    "BUZZER": ACTIVE_LOW,
}
# If buzzer wiring differs, override:
# CHANNEL_ACTIVE_LOW["BUZZER"] = False

# -------------------- GPIO PINS (BCM) --------------------
RED_PIN    = 5
GREEN_PIN  = 6
YELLOW_PIN = 13
BUZZER_PIN = 19

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
for p in (RED_PIN, GREEN_PIN, YELLOW_PIN, BUZZER_PIN):
    GPIO.setup(p, GPIO.OUT)

def _pin_write(pin: int, on: bool, active_low: bool):
    if active_low:
        GPIO.output(pin, GPIO.LOW if on else GPIO.HIGH)
    else:
        GPIO.output(pin, GPIO.HIGH if on else GPIO.LOW)

def red(on: bool):    _pin_write(RED_PIN,    on, CHANNEL_ACTIVE_LOW["RED"])
def green(on: bool):  _pin_write(GREEN_PIN,  on, CHANNEL_ACTIVE_LOW["GREEN"])
def yellow(on: bool): _pin_write(YELLOW_PIN, on, CHANNEL_ACTIVE_LOW["YELLOW"])
def buzzer(on: bool): _pin_write(BUZZER_PIN, on, CHANNEL_ACTIVE_LOW["BUZZER"])

def tower_all_off():
    red(False); green(False); yellow(False); buzzer(False)

tower_all_off()

# -------------------- File/Log setup --------------------
os.makedirs(CSV_FOLDER, exist_ok=True)

try:
    sys.stdout = open(LOG_PATH, "a", buffering=1)
    sys.stderr = sys.stdout
    debug("🔁 Script started (log ready)")
except Exception as e:
    with open("/home/pi/gwim-scanner/gwim_fallback.txt", "a") as f:
        f.write(f"Logging failed: {e}\n")

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

def check_internet() -> bool:
    try:
        return subprocess.call(
            ["ping", "-c", "1", "-W", "1", "8.8.8.8"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        ) == 0
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

# -------------------- Yellow internet behavior --------------------
yellow_checker_timer = None

def update_yellow_light():
    global yellow_checker_timer
    try:
        if yellow_checker_timer and yellow_checker_timer.is_alive():
            yellow_checker_timer.cancel()
    except Exception:
        pass

    if check_internet():
        yellow(True)
    else:
        yellow(True); time.sleep(0.5); yellow(False)

    yellow_checker_timer = threading.Timer(10, update_yellow_light)
    yellow_checker_timer.daemon = True
    yellow_checker_timer.start()

# -------------------- Green blinking state machine --------------------
green_blink_running = True
green_blink_thread = None
green_mode = "BLINK"  # BLINK or SOLID

def continuous_green_blink():
    global green_blink_running, green_mode
    # fast blink 5 times
    for _ in range(5):
        if not green_blink_running or green_mode != "BLINK":
            break
        green(True); time.sleep(0.2)
        green(False); time.sleep(0.1)

    # slow blink
    while green_blink_running and green_mode == "BLINK":
        green(True); time.sleep(0.5)
        green(False); time.sleep(0.5)

    if green_mode == "BLINK":
        green(False)

def restart_green_blink():
    global green_blink_running, green_blink_thread, green_mode
    green_blink_running = False
    if green_blink_thread and green_blink_thread.is_alive():
        green_blink_thread.join(timeout=1.0)

    green(False)
    green_mode = "BLINK"
    green_blink_running = True
    green_blink_thread = threading.Thread(target=continuous_green_blink, daemon=True)
    green_blink_thread.start()
    debug("✅ Green blinking restarted")

def set_green_solid_on():
    global green_blink_running, green_mode, green_blink_thread
    green_blink_running = False
    if green_blink_thread and green_blink_thread.is_alive():
        green_blink_thread.join(timeout=1.0)
    green_mode = "SOLID"
    green(True)
    debug("✅ Green solid ON")

# -------------------- ERROR ALERT (FIXED) --------------------
# Persistent threads + Event => no race condition
error_event = threading.Event()

def red_alert_worker():
    # runs forever
    while True:
        if error_event.is_set():
            red(True); time.sleep(0.5)
            red(False); time.sleep(0.5)
        else:
            red(False)
            time.sleep(0.1)

def buzzer_alert_worker():
    # runs forever
    while True:
        if error_event.is_set():
            # USER REQUIREMENT: buzzer continuous ON during error
            buzzer(True)
            time.sleep(0.1)
        else:
            buzzer(False)
            time.sleep(0.1)

# start persistent workers once
threading.Thread(target=red_alert_worker, daemon=True).start()
threading.Thread(target=buzzer_alert_worker, daemon=True).start()

def start_error_alert():
    error_event.set()

def stop_error_alert():
    error_event.clear()
    red(False)
    buzzer(False)

# -------------------- Staff functions (perfect flow) --------------------
def resolve_image_url(path):
    path = (path or "").strip().lstrip("../")
    return f"http://192.168.20.17/{path}"

def is_valid_staff_id(staff_id: str) -> bool:
    try:
        conn = mysql.connector.connect(
            host=STAFF_DB["host"],
            user=STAFF_DB["user"],
            password=STAFF_DB["password"],
            database=STAFF_DB["database"],
        )
        cur = conn.cursor()
        cur.execute("SELECT staffid FROM staff_list WHERE staffpos='OPERATOR'")
        valid = {row[0].strip().upper() for row in cur.fetchall()}
        cur.close(); conn.close()
        return staff_id.strip().upper() in valid
    except Exception as e:
        debug(f"Staff DB connection error: {e}")
        return False

staff_id = None

def staff_in_out_flow(staff_code: str):
    global staff_id
    staff_code = normalize_barcode(staff_code)

    try:
        conn = mysql.connector.connect(
            host=STAFF_DB["host"],
            user=STAFF_DB["user"],
            password=STAFF_DB["password"],
            database=STAFF_DB["database"],
        )
        cur = conn.cursor(dictionary=True)
        today_str = datetime.now().strftime("%Y-%m-%d")
        now_dt = datetime.now()

        if staff_id is None:
            if not is_valid_staff_id(staff_code):
                debug(f"Invalid staff ID: {staff_code}")
                start_error_alert()
                return

            staff_id = staff_code
            cur.execute("SELECT * FROM staff_list WHERE staffid=%s", (staff_id,))
            staff_row = cur.fetchone()
            if not staff_row:
                debug("❌ Staff not found after validation")
                staff_id = None
                start_error_alert()
                return

            shift = (staff_row.get("shift") or "").upper()
            shift_value = "DAY" if "DAY" in shift else "NIGHT" if "NIGHT" in shift else ""

            cur.execute("DELETE FROM allocation_temp WHERE staffid=%s", (staff_id,))
            cur.execute("""
                INSERT INTO allocation_temp
                  (staffid, line, staffname, staffpos, staffdept, status, remark, created_date, pic, flg)
                VALUES
                  (%s, %s, %s, %s, %s, 'IN', '', %s, %s, NULL)
            """, (
                staff_id, DEVICE_LINE,
                staff_row.get("staffname"), staff_row.get("staffpos"), staff_row.get("staffdept"),
                now_dt.date(), resolve_image_url(staff_row.get("pic"))
            ))

            cur.execute("SELECT id FROM allcation_log WHERE employee_id=%s AND date_run=%s", (staff_id, today_str))
            log_row = cur.fetchone()
            if log_row:
                cur.execute("UPDATE allcation_log SET out_datetime=%s, status='OUT' WHERE id=%s",
                            (now_dt, log_row["id"]))
            else:
                cur.execute("""
                    INSERT INTO allcation_log
                      (line, employee_id, name, job_title, department, datetime_log, status, remark,
                       file_path, date_run, in_datetime, out_datetime, time_taken, shift)
                    VALUES
                      (%s, %s, %s, %s, %s, %s, 'IN', '', %s, %s, %s, NULL, 0.00, %s)
                """, (
                    DEVICE_LINE, staff_id,
                    staff_row.get("staffname"), staff_row.get("staffpos"), staff_row.get("staffdept"),
                    now_dt, resolve_image_url(staff_row.get("pic")),
                    today_str, now_dt, shift_value
                ))

            cur.execute("SELECT id FROM prod_attendance WHERE staffid=%s AND date=%s", (staff_id, today_str))
            att_row = cur.fetchone()
            if att_row:
                cur.execute("UPDATE prod_attendance SET timeout=%s WHERE id=%s", (now_dt, att_row["id"]))
            else:
                cur.execute("""
                    INSERT INTO prod_attendance
                      (staffid, name, staffpos, staffdept, timein, timeout, work_hr, pic, staffic,
                       date, shift, flg, staffagency, day)
                    VALUES
                      (%s, %s, %s, %s, %s, NULL, 0.00, %s, NULL, %s, %s, NULL, %s, %s)
                """, (
                    staff_id,
                    staff_row.get("staffname"), staff_row.get("staffpos"), staff_row.get("staffdept"),
                    now_dt, resolve_image_url(staff_row.get("pic")),
                    today_str, shift_value,
                    staff_row.get("staffagency", ""),
                    calendar.day_name[now_dt.weekday()]
                ))

            conn.commit()
            debug("✅ Staff IN complete")

        elif staff_code == staff_id:
            debug(f"🔁 Staff OUT: {staff_id}")
            cur.execute("SELECT id FROM allcation_log WHERE employee_id=%s AND date_run=%s", (staff_id, today_str))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE allcation_log SET out_datetime=%s, status='OUT' WHERE id=%s", (now_dt, row["id"]))

            cur.execute("SELECT id FROM prod_attendance WHERE staffid=%s AND date=%s", (staff_id, today_str))
            row = cur.fetchone()
            if row:
                cur.execute("UPDATE prod_attendance SET timeout=%s WHERE id=%s", (now_dt, row["id"]))

            conn.commit()
            staff_id = None

        cur.close(); conn.close()

    except Exception as e:
        debug(f"🔥 Staff flow error: {e}")
        start_error_alert()

# -------------------- Production DB operations --------------------
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
        debug(f"⚠️ Production DB error: {e}")
        return None

# -------------------- CSV + output_log insert --------------------
csv_lock = threading.Lock()

CSV_HEADER = [
    "muf_no", "line", "fg_no", "pack_per_ctn", "pack_per_hr",
    "actual_pack", "ctn_count", "scanned_code", "scanned_count",
    "scanned_at", "scanned_by", "remarks", "is_uploaded"
]

def _csv_path_for_muf(muf_no: str) -> str:
    return os.path.join(CSV_FOLDER, f"{muf_no}_{datetime.now().strftime('%Y%m%d')}.csv")

def write_to_csv(data_11, muf_no: str, uploaded=0, remarks=""):
    if not LOGS_WRITABLE:
        debug("⚠️ logs not writable; CSV not saved.")
        return

    path = _csv_path_for_muf(muf_no)
    with csv_lock:
        is_new = not os.path.exists(path)
        try:
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if is_new:
                    w.writerow(CSV_HEADER)
                w.writerow(list(data_11) + [remarks, int(uploaded)])
            debug(f"📂 CSV saved: {path} (uploaded={uploaded})")
        except Exception as e:
            debug(f"⚠️ CSV write failed: {path} ({e})")

def insert_output_log(data_11, remarks: str) -> bool:
    try:
        conn = connect_pymysql(PRODUCTION_DB, dict_cursor=False)
        cur = conn.cursor()
        sql = (
            "INSERT INTO output_log ("
            "muf_no, line, fg_no, pack_per_ctn, pack_per_hr, actual_pack, "
            "ctn_count, scanned_code, scanned_count, scanned_at, scanned_by, remarks"
            ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        )
        cur.execute(sql, data_11 + (remarks,))
        conn.commit()
        cur.close(); conn.close()
        return True
    except Exception as e:
        debug(f"⚠️ DB insert failed: {e}")
        return False

# main scan state
current_batch = None
current_muf = None
template_code = None
muf_info = None
barcode_buffer = ""
last_scan_time = 0.0
last_barcode = None

def process_and_store(carton_barcode: str, muf_info: dict, remarks="SCAN"):
    pack_per_ctn = safe_int(muf_info.get("pack_per_ctn"))
    pack_per_hr  = safe_int(muf_info.get("pack_per_hr"))
    ctn_count = 1
    actual_pack = pack_per_ctn * ctn_count if pack_per_ctn is not None else None

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
        carton_barcode,
        1,
        ts,
        scanned_by,
    )

    ok = insert_output_log(data_11, remarks=remarks)
    if ok:
        write_to_csv(data_11, current_muf, uploaded=1, remarks=remarks)
    else:
        write_to_csv(data_11, current_muf, uploaded=0, remarks=remarks)

def upload_from_csv():
    try:
        debug("⏫ Attempting to upload cached CSV data...")

        if not os.path.isdir(CSV_FOLDER):
            return

        for file in os.listdir(CSV_FOLDER):
            if not file.endswith(".csv"):
                continue

            path = os.path.join(CSV_FOLDER, file)

            # remove 0-byte files
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
                        rows = list(csv.reader(f))
                except Exception:
                    continue

            if len(rows) <= 1:
                continue

            pending_idx = []
            pending_vals = []
            for idx, r in enumerate(rows[1:], start=1):
                if len(r) < 13:
                    continue
                if r[-1] == "0":
                    pending_idx.append(idx)
                    pending_vals.append(r[:12])

            if not pending_vals:
                continue

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
                cur.close(); conn.close()

                with csv_lock:
                    for i in pending_idx:
                        if len(rows[i]) >= 13:
                            rows[i][-1] = "1"
                    try:
                        with open(path, "w", newline="") as f:
                            w = csv.writer(f)
                            w.writerows(rows)
                    except Exception:
                        pass

            except Exception as e:
                debug(f"⚠️ Upload failed: {e}")

    finally:
        threading.Timer(UPLOAD_INTERVAL_SEC, upload_from_csv).start()

# -------------------- Keyboard handler --------------------
def on_key(event):
    global barcode_buffer, last_barcode, last_scan_time
    global current_batch, current_muf, template_code, muf_info

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

        # Stop ANY active alerts on ANY scan (perfect behavior)
        stop_error_alert()

        now = datetime.now()

        # RESET
        if is_reset_code(barcode):
            debug("🔄 RESET scanned. Starting new batch")
            current_batch = f"batch_{now.strftime('%Y%m%d_%H%M%S')}"
            current_muf = None
            template_code = None
            muf_info = None
            restart_green_blink()
            return

        # Staff
        if looks_like_staff_id(normalized):
            staff_in_out_flow(normalized)
            return

        # Must RESET first
        if not current_batch:
            debug("⚠️ Please scan RESET first.")
            start_error_alert()
            return

        # MUF stage
        if current_muf is None:
            info = fetch_muf_info(normalized)
            if info:
                current_muf = normalized
                muf_info = info
                debug(f"✅ MUF found: {current_muf}")
            else:
                debug(f"❌ MUF not found: {normalized}")
                start_error_alert()
            return

        # Template stage
        if template_code is None:
            if normalized == current_muf:
                debug("⚠️ MUF scanned again as template (invalid)")
                start_error_alert()
                return
            template_code = normalized
            debug(f"🧾 Template set: {template_code}")
            set_green_solid_on()
            process_and_store(template_code, muf_info, remarks="TEMPLATE")
            return

        # After template: mismatch
        if normalized != template_code:
            debug(f"❌ Barcode mismatch: {normalized} != {template_code}")
            start_error_alert()
            return

        # Match template
        debug(f"✅ Barcode matches template: {template_code}")
        process_and_store(template_code, muf_info, remarks="SCAN")

    elif len(event.name) == 1:
        barcode_buffer += event.name
    elif event.name == "minus":
        barcode_buffer += "-"

# -------------------- Entry point --------------------
if __name__ == "__main__":
    debug(f"🔌 GPIO initialized. ACTIVE_LOW={ACTIVE_LOW}, CHANNEL_ACTIVE_LOW={CHANNEL_ACTIVE_LOW}")
    tower_all_off()

    update_yellow_light()
    upload_from_csv()
    restart_green_blink()

    debug("🧭 Listening for barcode scan via keyboard...")
    keyboard.on_press(on_key)
    keyboard.wait()
