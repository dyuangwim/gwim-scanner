# main.py
import os
import csv
import time
import threading
import sys
import traceback
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
# Change GPIO pins if your hardware wiring differs
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

tower_all_off()

def buzzer_beep(seconds=0.25):
    GPIO.output(GPIO_BUZZER, GPIO.LOW)
    time.sleep(seconds)
    GPIO.output(GPIO_BUZZER, GPIO.HIGH)

def red_on():
    GPIO.output(GPIO_RED, GPIO.LOW)

def red_off():
    GPIO.output(GPIO_RED, GPIO.HIGH)

def green_on():
    GPIO.output(GPIO_GREEN, GPIO.LOW)

def green_off():
    GPIO.output(GPIO_GREEN, GPIO.HIGH)

def yellow_on():
    GPIO.output(GPIO_YELLOW, GPIO.LOW)

def yellow_off():
    GPIO.output(GPIO_YELLOW, GPIO.HIGH)

# -------------------- Logging to file --------------------
os.makedirs(CSV_FOLDER, exist_ok=True)

try:
    sys.stdout = open(LOG_PATH, "a", buffering=1)
    sys.stderr = sys.stdout
    debug("🔁 Script started (log ready)")
except Exception as e:
    # last-resort fallback
    with open("/home/pi/gwim-scanner/gwim_fallback.txt", "a") as f:
        f.write(f"Logging failed: {e}\n")

# -------------------- Helpers --------------------
def safe_int(value):
    try:
        return int(value)
    except:
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

def is_reset_code(barcode: str) -> bool:
    normalized = normalize_barcode(barcode)
    return normalized in {normalize_barcode(r) for r in RESET_CODES}

def looks_like_staff_id(barcode: str) -> bool:
    """
    Your original logic: staff ID contains letters.
    E.g. 'AB1234' → staff id
    """
    b = normalize_barcode(barcode)
    return any(c.isalpha() for c in b)

def ping_ok(ip="8.8.8.8") -> bool:
    try:
        subprocess.check_output(["ping", "-c", "1", "-W", "1", ip])
        return True
    except:
        return False

def connect_pymysql(db_cfg: dict, dict_cursor=False):
    kwargs = dict(
        host=db_cfg["host"],
        user=db_cfg["user"],
        password=db_cfg["password"],
        database=db_cfg["database"],
        port=int(db_cfg.get("port", 3306)),
        connect_timeout=DB_CONNECT_TIMEOUT,
        read_timeout=DB_READ_TIMEOUT,
        write_timeout=DB_WRITE_TIMEOUT,
        autocommit=True,
    )
    if dict_cursor:
        kwargs["cursorclass"] = pymysql.cursors.DictCursor
    return pymysql.connect(**kwargs)

# -------------------- CSV write + upload --------------------
csv_lock = threading.Lock()

def write_to_csv(row_tuple, muf_no: str, uploaded: int, remarks: str):
    """
    Keep your caching logic: always write a row to CSV.
    If uploaded=1 means already inserted into DB successfully.
    """
    with csv_lock:
        filename = os.path.join(CSV_FOLDER, f"{muf_no}_{datetime.now().strftime('%Y%m%d')}.csv")
        is_new = not os.path.exists(filename)

        with open(filename, "a", newline="") as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow([
                    "muf_no", "line", "fg_no", "pack_per_ctn", "pack_per_hr",
                    "actual_pack", "ctn_count", "scanned_code", "scanned_count",
                    "scanned_at", "scanned_by", "remarks", "is_uploaded"
                ])
            writer.writerow(list(row_tuple) + [remarks, int(uploaded)])

        debug(f"📂 Written CSV: {filename} (uploaded={uploaded}, remarks={remarks})")

def upload_from_csv():
    """
    Every UPLOAD_INTERVAL_SEC:
    - Find CSV rows where is_uploaded=0
    - Insert them into output_log
    - Mark uploaded rows as 1
    """
    try:
        debug("⏫ Upload thread: attempting to upload cached CSV data...")

        for file in os.listdir(CSV_FOLDER):
            if not file.endswith(".csv"):
                continue

            path = os.path.join(CSV_FOLDER, file)

            with csv_lock:
                with open(path, "r", newline="") as f:
                    rows = list(csv.reader(f))
                if len(rows) <= 1:
                    continue
                header = rows[0]
                data_rows = rows[1:]

            # collect not uploaded
            pending = []
            for r in data_rows:
                if len(r) < 13:
                    continue
                if r[-1] == "0":
                    pending.append(r)

            if not pending:
                continue

            debug(f"⏫ Found {len(pending)} pending rows: {path}")

            try:
                conn = connect_pymysql(PRODUCTION_DB, dict_cursor=False)
                cur = conn.cursor()
                sql = (
                    "INSERT INTO output_log ("
                    "muf_no, line, fg_no, pack_per_ctn, pack_per_hr, actual_pack, "
                    "ctn_count, scanned_code, scanned_count, scanned_at, scanned_by, remarks"
                    ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                )

                for r in pending:
                    # r: 0..10 are original fields, r[11]=remarks, r[12]=is_uploaded
                    cur.execute(sql, r[:12])

                conn.commit()
                cur.close()
                conn.close()

                # mark as uploaded
                with csv_lock:
                    for i in range(1, len(rows)):
                        if len(rows[i]) >= 13 and rows[i][-1] == "0":
                            rows[i][-1] = "1"
                    with open(path, "w", newline="") as f:
                        writer = csv.writer(f)
                        writer.writerows(rows)

                debug(f"✅ Uploaded & marked: {path}")

            except Exception as e:
                debug(f"⚠️ Upload failed for {path}: {e}")

    except Exception as e:
        debug(f"⚠️ upload_from_csv unexpected error: {e}")

    threading.Timer(UPLOAD_INTERVAL_SEC, upload_from_csv).start()

# -------------------- Staff IN/OUT logic --------------------
def is_valid_staff_id(staff_id: str) -> bool:
    """
    Valid staff ID must exist in allocation_m3.staff table.
    """
    staff_id = normalize_barcode(staff_id)
    try:
        conn = connect_pymysql(STAFF_DB, dict_cursor=False)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM staff WHERE staff_id=%s LIMIT 1", (staff_id,))
        ok = cur.fetchone() is not None
        cur.close()
        conn.close()
        return ok
    except Exception as e:   # <-- fixed typo
        debug(f"Staff DB connection error: {e}")
        return False

def toggle_staff_status(staff_id: str) -> str:
    """
    Mimic your logic:
    - If no status row: INSERT IN
    - If status = IN: set OUT
    - If status = OUT: set IN
    Return "IN" or "OUT" or "ERROR"
    """
    staff_id = normalize_barcode(staff_id)
    try:
        conn = connect_pymysql(STAFF_DB, dict_cursor=False)
        cur = conn.cursor()

        cur.execute("SELECT status FROM staff_status WHERE staff_id=%s LIMIT 1", (staff_id,))
        row = cur.fetchone()

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

# -------------------- Production DB: MUF + insert --------------------
def fetch_muf_info(muf_code: str):
    """
    Query production.main for muf_no.
    Return dict row or None.
    """
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

def insert_output_log(data_tuple, remarks: str) -> bool:
    """
    Insert one row into output_log.
    Return True if success else False.
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
        cur.execute(sql, data_tuple + (remarks,))
        conn.commit()
        cur.close(); conn.close()
        return True
    except Exception as e:
        debug(f"⚠️ DB insert failed: {e}")
        return False

# -------------------- Network indicator thread --------------------
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
        except:
            pass
        time.sleep(10)

# -------------------- Main scanning state --------------------
current_batch = None
current_muf = None
muf_info = None
template_code = None
barcode_buffer = ""
last_barcode = None
last_scan_time = 0.0
staff_id = None  # current staff logged IN

def set_state_reset():
    global current_batch, current_muf, muf_info, template_code
    global staff_id
    now = datetime.now()
    current_batch = f"batch_{now.strftime('%Y%m%d_%H%M%S')}"
    current_muf = None
    muf_info = None
    template_code = None
    debug(f"🔄 RESET scanned. New batch: {current_batch}")
    # green blink to indicate need MUF
    green_off()
    red_off()

def process_and_store(scanned_barcode: str, remarks: str):
    """
    Preserve your data shape + logic.
    """
    global current_muf, muf_info, staff_id

    pack_per_ctn = safe_int(muf_info.get("pack_per_ctn"))
    pack_per_hr  = safe_int(muf_info.get("pack_per_hr"))
    ctn_count = 1
    actual_pack = (pack_per_ctn * ctn_count) if pack_per_ctn is not None else None

    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

    scanned_by = staff_id if staff_id else DEVICE_ID

    row_tuple = (
        current_muf,
        DEVICE_LINE,
        muf_info.get("fg_no"),
        pack_per_ctn,
        pack_per_hr,
        actual_pack,
        ctn_count,
        scanned_barcode,
        1,
        timestamp,
        scanned_by,
    )

    ok = insert_output_log(row_tuple, remarks=remarks)
    if ok:
        debug("✅ DB insert successful")
        write_to_csv(row_tuple, current_muf, uploaded=1, remarks=remarks)
        green_on()
        red_off()
        buzzer_beep(0.08)
    else:
        debug("⚠️ DB insert failed. Cached locally.")
        write_to_csv(row_tuple, current_muf, uploaded=0, remarks=remarks)
        # still show success beep (your original did 1 blink); keep minimal beep
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

        # Prevent duplicate scans within SCAN_INTERVAL
        if barcode == last_barcode and (now_ts - last_scan_time) < SCAN_INTERVAL:
            debug(f"⏱️ Duplicate scan ignored: {barcode}")
            return

        last_barcode = barcode
        last_scan_time = now_ts

        debug(f"📥 Scanned: '{barcode}' -> '{normalized}'")

        # 1) RESET
        if is_reset_code(barcode):
            set_state_reset()
            # blink green a bit to show ready
            green_on(); time.sleep(0.2); green_off(); time.sleep(0.2); green_on()
            return

        # Must RESET first
        if not current_batch:
            debug("⚠️ Please scan RESET first.")
            red_on()
            buzzer_beep(0.3)
            return

        # 2) Staff ID (contains letters)
        if looks_like_staff_id(barcode):
            candidate = normalize_barcode(barcode)
            if not is_valid_staff_id(candidate):
                debug(f"❌ Invalid staff ID: {candidate}")
                red_on()
                buzzer_beep(0.3)
                return

            status = toggle_staff_status(candidate)
            if status == "IN":
                staff_id = candidate
                debug(f"👤 Staff IN: {staff_id}")
                green_on()
                red_off()
                buzzer_beep(0.08)
            elif status == "OUT":
                debug(f"👤 Staff OUT: {candidate}")
                # if current staff same, clear
                if staff_id == candidate:
                    staff_id = None
                green_off()
                red_off()
                buzzer_beep(0.08)
            else:
                debug("⚠️ Staff status update ERROR")
                red_on()
                buzzer_beep(0.3)
            return

        # 3) MUF not set yet -> treat next barcode as MUF
        if current_muf is None:
            info = fetch_muf_info(normalized)
            if not info:
                debug(f"❌ MUF not found: {normalized}")
                red_on()
                buzzer_beep(0.3)
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
            # ignore if someone scanned MUF again
            if normalized == current_muf:
                debug("⚠️ Duplicate MUF scanned, ignoring as template.")
                return
            template_code = normalized
            debug(f"🧾 Template set: {template_code}")
            # record template as one carton
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

# -------------------- Main --------------------
if __name__ == "__main__":
    debug(f"🚀 Starting scanner: line={DEVICE_LINE}, device={DEVICE_ID}")
    # upload pending every 5 min
    upload_from_csv()

    # network indicator
    threading.Thread(target=network_indicator_loop, daemon=True).start()

    # start listening
    debug("🧭 Listening for barcode scan via keyboard...")
    keyboard.on_press(on_key)
    keyboard.wait()
