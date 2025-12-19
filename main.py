#!/usr/bin/env python3
# main.py (Option 1 - Intranet DB + Perfect tower-light behavior + Relay safe polarity)
# =================================================================================
# Matches your "perfect flow" behavior (per documentation):
# GREEN:
#   - boot: fast blink 5 times (0.2 ON / 0.1 OFF)
#   - waiting RESET: slow blink (0.5 ON / 0.5 OFF)
#   - RESET scanned: restart slow blinking (new batch)
#   - MUF valid: keep slow blinking
#   - Template (first carton): solid ON and stays ON
# RED + BUZZER:
#   - errors: continuous red blink (0.5/0.5) + continuous beeping (0.15 ON / 0.5 OFF)
#   - stop alerts on ANY next scan (like your old stop_all_alerts())
# YELLOW:
#   - internet ok: solid ON
#   - internet down: blink once (0.5 ON) every 10 seconds
#
# Also includes:
# - CSV empty/0-byte defensive handling (fix old IndexError loop)
# - logs folder writable check + clear instruction
# - DB timeouts (avoid hang)
# - Relay polarity support (avoid buzzer stuck ON)
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
# Your hardware in documentation uses 8-ch relay, typical Active-Low inputs.
# If buzzer wiring differs, override BUZZER below.
ACTIVE_LOW = True  # relay device
CHANNEL_ACTIVE_LOW = {
    "RED":    ACTIVE_LOW,
    "GREEN":  ACTIVE_LOW,
    "YELLOW": ACTIVE_LOW,
    "BUZZER": ACTIVE_LOW,
}

# -------------------- GPIO PINS (BCM) --------------------
# Per your documentation wiring:
# IN1 GPIO5, IN2 GPIO6, IN3 GPIO13, IN4 GPIO19
# Red=GPIO5, Green=GPIO6, Yellow=GPIO13, Buzzer=GPIO19:contentReference[oaicite:1]{index=1}
RED_PIN    = 5
GREEN_PIN  = 6
YELLOW_PIN = 13
BUZZER_PIN = 19

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
for p in (RED_PIN, GREEN_PIN, YELLOW_PIN, BUZZER_PIN):
    GPIO.setup(p, GPIO.OUT)

def _pin_write(pin: int, on: bool, active_low: bool):
    # active_low: ON=LOW, OFF=HIGH
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

# Safety at boot
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

# -------------------- Helper functions --------------------
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
        return subprocess.call(["ping", "-c", "1", "-W", "1", "8.8.8.8"],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL) == 0
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

# -------------------- Yellow internet behavior (exact like old) --------------------
yellow_checker_timer = None

def update_yellow_light():
    global yellow_checker_timer
    try:
        if yellow_checker_timer and yellow_checker_timer.is_alive():
            yellow_checker_timer.cancel()
    except Exception:
        pass

    if check_internet():
        yellow(True)  # solid ON
    else:
        # blink once (0.5s ON) every 10 seconds
        yellow(True)
        time.sleep(0.5)
        yellow(False)

    yellow_checker_timer = threading.Timer(10, update_yellow_light)
    yellow_checker_timer.daemon = True
    yellow_checker_timer.start()

# -------------------- Green blinking state machine (exact) --------------------
green_blink_running = True
green_blink_thread = None
green_mode = "BLINK"  # "BLINK" or "SOLID"

def continuous_green_blink():
    global green_blink_running, green_mode
    # Fast blink 5 times at boot/RESET restart (0.2 ON / 0.1 OFF)
    for _ in range(5):
        if not green_blink_running or green_mode != "BLINK":
            break
        green(True); time.sleep(0.2)
        green(False); time.sleep(0.1)

    # Slow blink until stopped (0.5/0.5)
    while green_blink_running and green_mode == "BLINK":
        green(True); time.sleep(0.5)
        green(False); time.sleep(0.5)

    # Ensure OFF if thread ends in blink mode; if SOLID set elsewhere, it stays ON.
    if green_mode == "BLINK":
        green(False)

def restart_green_blink():
    global green_blink_running, green_blink_thread, green_mode
    # stop existing
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

# -------------------- Red + buzzer persistent alerts (exact like old) --------------------
red_alert_active = False
buzzer_alert_active = False
red_alert_thread = None
buzzer_alert_thread = None

def continuous_red_blink():
    global red_alert_active
    while red_alert_active:
        red(True); time.sleep(0.5)
        red(False); time.sleep(0.5)
    red(False)

def continuous_buzz():
    global buzzer_alert_active
    while buzzer_alert_active:
        buzzer(True); time.sleep(0.15)
        buzzer(False); time.sleep(0.5)
    buzzer(False)

def stop_all_alerts():
    global red_alert_active, buzzer_alert_active, red_alert_thread, buzzer_alert_thread
    red_alert_active = False
    buzzer_alert_active = False

    if red_alert_thread and red_alert_thread.is_alive():
        red_alert_thread.join(timeout=0.6)
    red(False)

    if buzzer_alert_thread and buzzer_alert_thread.is_alive():
        buzzer_alert_thread.join(timeout=0.6)
    buzzer(False)

def start_red_buzzer_alert():
    global red_alert_active, buzzer_alert_active, red_alert_thread, buzzer_alert_thread
    red_alert_active = True
    buzzer_alert_active = True

    if not (red_alert_thread and red_alert_thread.is_alive()):
        red_alert_thread = threading.Thread(target=continuous_red_blink, daemon=True)
        red_alert_thread.start()
    if not (buzzer_alert_thread and buzzer_alert_thread.is_alive()):
        buzzer_alert_thread = threading.Thread(target=continuous_buzz, daemon=True)
        buzzer_alert_thread.start()

# -------------------- Staff validation (match your old intent) --------------------
def resolve_image_url(path):
    path = (path or "").strip().lstrip("../")
    return f"http://192.168.20.17/{path}"

def is_valid_staff_id(staff_id: str) -> bool:
    """
    Old perfect code: pull staff_list where staffpos='OPERATOR' and validate.
    """
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

def staff_in_out_flow(staff_code: str):
    """
    Replicates your old 'perfect' staff IN/OUT logic:
    - first scan sets IN and writes allocation_temp, allcation_log, prod_attendance
    - scanning same staff again sets OUT and clears session
    """
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
                start_red_buzzer_alert()
                return

            staff_id = staff_code
            debug(f"✅ Valid staff ID detected: {staff_id}")

            cur.execute("SELECT * FROM staff_list WHERE staffid=%s", (staff_id,))
            staff_row = cur.fetchone()
            if not staff_row:
                debug("❌ Staff ID not found in DB after validation")
                start_red_buzzer_alert()
                staff_id = None
                return

            shift = (staff_row.get("shift") or "").upper()
            shift_value = "DAY" if "DAY" in shift else "NIGHT" if "NIGHT" in shift else ""

            # allocation_temp
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

            # allcation_log (spelling as in your old code)
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

            # prod_attendance
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
            debug("✅ Staff IN logic complete")

        elif staff_code == staff_id:
            debug(f"🔁 OUT scan for {staff_id}, clearing session")

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

        cur.close()
        conn.close()

    except Exception as e:
        debug(f"🔥 Error during staff ID scan: {e}")
        start_red_buzzer_alert()

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

# -------------------- CSV caching & upload (fixed) --------------------
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
            debug(f"📂 Written to CSV: {path} (uploaded={uploaded})")
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
        debug("✅ DB insert successful")
        write_to_csv(data_11, current_muf, uploaded=1, remarks=remarks)
    else:
        debug("⚠️ DB insert failed. Cached locally.")
        write_to_csv(data_11, current_muf, uploaded=0, remarks=remarks)

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

            # Remove 0-byte file (fix your old crash loop)
            try:
                if os.path.getsize(path) == 0:
                    debug(f"🧹 Empty (0-byte) CSV found, removing: {path}")
                    try:
                        os.remove(path)
                    except Exception as e:
                        debug(f"⚠️ Cannot remove empty CSV: {path} ({e})")
                    continue
            except Exception:
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

            pending_idx = []
            pending_vals = []
            data_rows = rows[1:]
            for idx, r in enumerate(data_rows, start=1):
                if len(r) < 13:
                    continue
                if r[-1] == "0":
                    pending_idx.append(idx)
                    pending_vals.append(r[:12])

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
                cur.close(); conn.close()

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

                debug(f"✅ Upload complete and marked: {path}")

            except Exception as e:
                debug(f"⚠️ Upload failed: {e}")

    finally:
        threading.Timer(UPLOAD_INTERVAL_SEC, upload_from_csv).start()

# -------------------- Main scanning state --------------------
current_batch = None
current_muf = None
template_code = None
muf_info = None
barcode_buffer = ""
last_scan_time = 0.0
last_barcode = None
staff_id = None

def on_key(event):
    global barcode_buffer, last_barcode, last_scan_time
    global current_batch, current_muf, template_code, muf_info, staff_id

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
        debug(f"📥 Scanned barcode: '{barcode}' → normalized: '{normalized}'")

        # IMPORTANT: stop alerts on ANY scan (matches your perfect flow)
        stop_all_alerts()

        now = datetime.now()

        # RESET
        if is_reset_code(barcode):
            debug("🔄 RESET scanned. Starting new batch")
            current_batch = f"batch_{now.strftime('%Y%m%d_%H%M%S')}"
            current_muf = None
            template_code = None
            muf_info = None

            # Restart green blink (fast then slow)
            restart_green_blink()
            return

        # Staff ID
        if looks_like_staff_id(normalized):
            staff_in_out_flow(normalized)
            # On valid staff actions, brief green feedback is not needed because green is already blinking/solid;
            # Your old code did a single blink + buzz; we keep the alert system logic and keep green state.
            return

        # Must scan RESET first
        if not current_batch:
            debug("⚠️ Please scan RESET first.")
            start_red_buzzer_alert()
            return

        # MUF stage
        if current_muf is None:
            info = fetch_muf_info(normalized)
            if info:
                current_muf = normalized
                muf_info = info
                debug(f"✅ MUF found: {current_muf}")
                # Green stays blinking (no change):contentReference[oaicite:2]{index=2}
            else:
                debug(f"❌ MUF not found: {normalized}")
                start_red_buzzer_alert()
            return

        # Template stage
        if template_code is None:
            if normalized == current_muf:
                debug("⚠️ MUF scanned again as template (invalid)")
                start_red_buzzer_alert()
                return

            template_code = normalized
            debug(f"🧾 Template barcode set: {template_code}")

            # Stop blinking and set green solid ON (exact behavior):contentReference[oaicite:3]{index=3}
            set_green_solid_on()

            process_and_store(template_code, muf_info, remarks="TEMPLATE")
            return

        # After template: mismatch => persistent error
        if normalized != template_code:
            debug(f"❌ Barcode mismatch: {normalized} != {template_code}")
            start_red_buzzer_alert()
            return

        # Match template => store
        debug(f"✅ Barcode matches template: {template_code}")
        process_and_store(template_code, muf_info, remarks="SCAN")
        # Green stays solid ON:contentReference[oaicite:4]{index=4}

    elif len(event.name) == 1:
        barcode_buffer += event.name
    elif event.name == "minus":
        barcode_buffer += "-"

# -------------------- Entry point --------------------
if __name__ == "__main__":
    debug(f"🔌 GPIO initialized. ACTIVE_LOW={ACTIVE_LOW}, CHANNEL_ACTIVE_LOW={CHANNEL_ACTIVE_LOW}")
    tower_all_off()

    # Start yellow internet status checker (exact behavior):contentReference[oaicite:5]{index=5}
    update_yellow_light()

    # Start periodic CSV upload
    upload_from_csv()

    # Start initial green blinking (boot behavior):contentReference[oaicite:6]{index=6}
    restart_green_blink()
    debug("Initial green light blinking started.")

    debug("🧭 Listening for barcode scan via keyboard...")
    keyboard.on_press(on_key)
    keyboard.wait()
