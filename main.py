#!/usr/bin/env python3
# main.py  (Option 1 - Intranet DB; per-line independent)
# ==========================================================
# Key fixes:
# - No more IndexError from empty CSV (skip & optionally remove 0-byte CSV)
# - Startup logs folder writability check (prints exact chown command)
# - DB connect timeouts to avoid long hangs
# - Keeps original scanning logic: RESET -> staff -> MUF -> TEMPLATE -> SCAN
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

# -------------------- Debug --------------------
DEBUG_MODE = True

def debug(msg: str):
    if DEBUG_MODE:
        print(f"[DEBUG] {msg}")

# -------------------- Relay Tower Lamp (LOW=ON / HIGH=OFF) --------------------
# Adjust pins if needed
GPIO_GREEN  = 6
GPIO_RED    = 13
GPIO_YELLOW = 19
GPIO_BUZZER = 26

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

GPIO.setup(GPIO_GREEN, GPIO.OUT)
GPIO.setup(GPIO_RED, GPIO.OUT)
GPIO.setup(GPIO_YELLOW, GPIO.OUT)
GPIO.setup(GPIO_BUZZER, GPIO.OUT)

def tower_all_off():
    GPIO.output(GPIO_GREEN, GPIO.HIGH)
    GPIO.output(GPIO_RED, GPIO.HIGH)
    GPIO.output(GPIO_YELLOW, GPIO.HIGH)
    GPIO.output(GPIO_BUZZER, GPIO.HIGH)

def green_on():   GPIO.output(GPIO_GREEN, GPIO.LOW)
def green_off():  GPIO.output(GPIO_GREEN, GPIO.HIGH)
def red_on():     GPIO.output(GPIO_RED, GPIO.LOW)
def red_off():    GPIO.output(GPIO_RED, GPIO.HIGH)
def yellow_on():  GPIO.output(GPIO_YELLOW, GPIO.LOW)
def yellow_off(): GPIO.output(GPIO_YELLOW, GPIO.HIGH)

def buzzer_beep(seconds=0.25):
    GPIO.output(GPIO_BUZZER, GPIO.LOW)
    time.sleep(seconds)
    GPIO.output(GPIO_BUZZER, GPIO.HIGH)

tower_all_off()

# -------------------- File/Log setup --------------------
os.makedirs(CSV_FOLDER, exist_ok=True)

# Redirect stdout/stderr to log file (line-buffered)
try:
    sys.stdout = open(LOG_PATH, "a", buffering=1)
    sys.stderr = sys.stdout
except Exception:
    # If we can't open log, we still continue with default stdout
    pass

debug("🔁 Script started (log ready)")
debug("🟩 GPIO initialized (relay LOW=ON, HIGH=OFF)")

def ensure_logs_writable() -> bool:
    """
    Prevent your old 'permission + empty csv' loop.
    If folder isn't writable, we don't crash; we warn clearly.
    """
    test_path = os.path.join(CSV_FOLDER, ".write_test")
    try:
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        debug(f"✅ Logs folder writable: {CSV_FOLDER}")
        return True
    except Exception as e:
        debug(f"❌ Logs folder NOT writable: {CSV_FOLDER} ({e})")
        debug("Fix by running on Raspberry Pi terminal:")
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
            .replace("–", "-")
            .replace("−", "-")
            .replace("—", "-")
            .replace("_", "-")
            .upper()
    )

RESET_SET = {normalize_barcode(x) for x in RESET_CODES}

def is_reset_code(barcode: str) -> bool:
    return normalize_barcode(barcode) in RESET_SET

def looks_like_staff_id(barcode: str) -> bool:
    # Your original logic: contains letters
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
    """
    Always cache to CSV (your original design).
    If logs folder not writable, just log warning and return.
    """
    if not LOGS_WRITABLE:
        debug("⚠️ CSV not written because logs folder not writable.")
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

def _read_csv_rows(path: str):
    """
    Return (rows, header_ok).
    rows includes header as rows[0] if present.
    """
    try:
        with open(path, "r", newline="") as f:
            rows = list(csv.reader(f))
        if not rows:
            return [], False
        if rows[0] != CSV_HEADER:
            # allow old header, but still treat as readable; we just won't validate strictly
            return rows, True
        return rows, True
    except Exception:
        return [], False

def _write_csv_rows(path: str, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerows(rows)

def upload_from_csv():
    """
    Periodically:
    - Skip / remove 0-byte files
    - Skip empty or header-only CSV
    - Insert pending rows (is_uploaded=0) into production.output_log
    - Mark those rows as uploaded=1 in the CSV
    """
    try:
        debug("⏫ Attempting to upload cached CSV data...")

        if not os.path.isdir(CSV_FOLDER):
            debug(f"⚠️ CSV folder not found: {CSV_FOLDER}")
            return

        for file in os.listdir(CSV_FOLDER):
            if not file.endswith(".csv"):
                continue

            path = os.path.join(CSV_FOLDER, file)

            # --- Fix for your old bug: empty file -> IndexError in old code ---
            # If 0 bytes, remove it to avoid repeat crashes.
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
                rows, ok = _read_csv_rows(path)

            if not ok or len(rows) <= 1:
                # empty or header-only (or unreadable) -> skip; do not crash
                debug(f"ℹ️ Skip CSV (empty/header-only/unreadable): {path}")
                continue

            header = rows[0]
            data_rows = rows[1:]

            # Find pending rows: last column is is_uploaded
            pending_indices = []
            pending_values = []

            for idx, r in enumerate(data_rows, start=1):
                if len(r) < 13:
                    continue
                if r[-1] == "0":
                    # first 12 columns match DB insert, r[11] is remarks
                    pending_indices.append(idx)
                    pending_values.append(r[:12])

            if not pending_values:
                continue

            debug(f"⏫ Pending rows: {len(pending_values)} in {path}")

            # Insert all pending
            try:
                conn = connect_pymysql(PRODUCTION_DB, dict_cursor=False)
                cur = conn.cursor()
                sql = (
                    "INSERT INTO output_log ("
                    "muf_no, line, fg_no, pack_per_ctn, pack_per_hr, actual_pack, "
                    "ctn_count, scanned_code, scanned_count, scanned_at, scanned_by, remarks"
                    ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                )
                for v in pending_values:
                    cur.execute(sql, v)
                conn.commit()
                cur.close()
                conn.close()

                # Mark uploaded
                with csv_lock:
                    for i in pending_indices:
                        if len(rows[i]) >= 13:
                            rows[i][-1] = "1"
                    try:
                        _write_csv_rows(path, rows)
                    except Exception as e:
                        debug(f"⚠️ Failed to mark uploaded in CSV: {path} ({e})")

                debug(f"✅ Uploaded & marked: {path}")

            except Exception as e:
                debug(f"⚠️ Upload failed: {path} ({e})")

    except Exception as e:
        debug(f"⚠️ upload_from_csv unexpected error: {e}")

    # schedule next run
    threading.Timer(UPLOAD_INTERVAL_SEC, upload_from_csv).start()

# -------------------- Staff IN/OUT --------------------
def is_valid_staff_id(staff_id: str) -> bool:
    staff_id = normalize_barcode(staff_id)
    try:
        conn = connect_pymysql(STAFF_DB, dict_cursor=False)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM staff WHERE staff_id=%s LIMIT 1", (staff_id,))
        ok = cur.fetchone() is not None
        cur.close()
        conn.close()
        return ok
    except Exception as e:
        debug(f"Staff DB connection error: {e}")
        return False

def toggle_staff_status(staff_id: str) -> str:
    """
    - If no row: INSERT IN
    - If IN -> OUT, else OUT -> IN
    """
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
            cur.execute(
                "UPDATE staff_status SET status='OUT', updated_at=%s WHERE staff_id=%s",
                (now_str, staff_id)
            )
            conn.commit()
            cur.close(); conn.close()
            return "OUT"
        else:
            cur.execute(
                "UPDATE staff_status SET status='IN', updated_at=%s WHERE staff_id=%s",
                (now_str, staff_id)
            )
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

def insert_output_log(data_tuple_11_fields, remarks: str) -> bool:
    """
    data_tuple_11_fields:
      (muf_no, line, fg_no, pack_per_ctn, pack_per_hr, actual_pack,
       ctn_count, scanned_code, scanned_count, scanned_at, scanned_by)
    """
    try:
        conn = connect_pymysql(PRODUCTION_DB, dict_cursor=False)
        cur = conn.cursor()
        sql = (
            "INSERT INTO output_log ("
            "muf_no, line, fg_no, pack_per_ctn, pack_per_hr, actual_pack, "
            "ctn_count, scanned_code, scanned_count, scanned_at, scanned_by, remarks"
            ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        )
        cur.execute(sql, data_tuple_11_fields + (remarks,))
        conn.commit()
        cur.close(); conn.close()
        return True
    except Exception as e:
        debug(f"⚠️ DB insert failed: {e}")
        return False

# -------------------- Network indicator (yellow lamp) --------------------
def network_indicator_loop():
    while True:
        try:
            if ping_ok("8.8.8.8"):
                yellow_on()
            else:
                yellow_off()
                time.sleep(0.2)
                yellow_on()
                time.sleep(0.2)
                yellow_off()
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

staff_id = None  # current staff who is IN

def set_state_reset():
    global current_batch, current_muf, muf_info, template_code
    now = datetime.now()
    current_batch = f"batch_{now.strftime('%Y%m%d_%H%M%S')}"
    current_muf = None
    muf_info = None
    template_code = None
    debug(f"🔄 RESET scanned. New batch: {current_batch}")

    red_off()
    green_off()

def process_and_store(scanned_barcode: str, remarks: str):
    """
    Keep original behavior: insert output_log + write CSV (fallback).
    """
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
        green_on()
        red_off()
        buzzer_beep(0.08)
    else:
        debug("⚠️ DB insert failed. Cached locally.")
        write_to_csv(data_11, current_muf, uploaded=0, remarks=remarks)
        # keep minimal beep so operator feedback still exists
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

        # Prevent duplicate scan within interval
        if barcode == last_barcode and (now_ts - last_scan_time) < float(SCAN_INTERVAL):
            debug(f"⏱️ Duplicate scan ignored: {barcode}")
            return

        last_barcode = barcode
        last_scan_time = now_ts

        debug(f"📥 Scanned: '{barcode}' -> '{normalized}'")

        # 1) RESET
        if is_reset_code(barcode):
            set_state_reset()
            # green blink to show ready
            green_on(); time.sleep(0.2); green_off(); time.sleep(0.2); green_on()
            return

        # Require RESET first
        if not current_batch:
            debug("⚠️ Please scan RESET first.")
            red_on()
            buzzer_beep(0.30)
            return

        # 2) Staff ID
        if looks_like_staff_id(barcode):
            candidate = normalize_barcode(barcode)
            if not is_valid_staff_id(candidate):
                debug(f"❌ Invalid staff ID: {candidate}")
                red_on()
                buzzer_beep(0.30)
                return

            status = toggle_staff_status(candidate)
            if status == "IN":
                staff_id = candidate
                debug(f"👤 Staff IN: {staff_id}")
                green_on(); red_off()
                buzzer_beep(0.08)
            elif status == "OUT":
                debug(f"👤 Staff OUT: {candidate}")
                if staff_id == candidate:
                    staff_id = None
                green_off(); red_off()
                buzzer_beep(0.08)
            else:
                debug("⚠️ Staff status update ERROR")
                red_on()
                buzzer_beep(0.30)
            return

        # 3) MUF not set yet -> treat this as MUF
        if current_muf is None:
            info = fetch_muf_info(normalized)
            if not info:
                debug(f"❌ MUF not found: {normalized}")
                red_on()
                buzzer_beep(0.30)
                return

            current_muf = normalized
            muf_info = info
            template_code = None
            debug(f"✅ MUF set: {current_muf} (fg_no={muf_info.get('fg_no')})")

            # green blink to prompt template
            green_on(); time.sleep(0.2); green_off(); time.sleep(0.2); green_on()
            red_off()
            return

        # 4) TEMPLATE not set yet
        if template_code is None:
            if normalized == current_muf:
                debug("⚠️ MUF scanned again; ignoring as template.")
                return

            template_code = normalized
            debug(f"🧾 Template set: {template_code}")

            # record template as one carton (original behavior)
            process_and_store(template_code, remarks="TEMPLATE")
            return

        # 5) Subsequent scans must match template
        if normalized != template_code:
            debug(f"❌ Barcode mismatch: {normalized} != {template_code}")
            red_on()
            green_off()
            buzzer_beep(0.35)
            return

        # match template => store
        process_and_store(normalized, remarks="SCAN")

    elif len(event.name) == 1:
        barcode_buffer += event.name
    elif event.name == "minus":
        barcode_buffer += "-"

# -------------------- Entry point --------------------
if __name__ == "__main__":
    debug(f"🚀 Starting scanner: line={DEVICE_LINE}, device={DEVICE_ID}")
    debug(f"📁 CSV folder: {CSV_FOLDER}")

    # Start periodic upload thread
    upload_from_csv()

    # Start network indicator
    threading.Thread(target=network_indicator_loop, daemon=True).start()

    # Start barcode listening
    debug("🧭 Listening for barcode scan via keyboard...")
    keyboard.on_press(on_key)
    keyboard.wait()
