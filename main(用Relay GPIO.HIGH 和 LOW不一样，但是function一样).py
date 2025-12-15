import os
import csv
import time
import pymysql
import threading
from datetime import datetime, date
from config import MYSQL_CONFIG, DEVICE_LINE, DEVICE_ID
import sys
import keyboard
import RPi.GPIO as GPIO
import mysql.connector
import calendar

# --- Debug mode switch ---
DEBUG_MODE = True

def debug(msg):
    if DEBUG_MODE:
        print(f"[DEBUG] {msg}")

# --- GPIO Setup ---
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# === Tower Light & Buzzer GPIO Setup ===
RED_PIN = 5       # Red tower light
GREEN_PIN = 6     # Green tower light
YELLOW_PIN = 13   # Yellow (Internet)
BUZZER_PIN = 19   # Buzzer for alerts

GPIO.setup(RED_PIN, GPIO.OUT)
GPIO.setup(GREEN_PIN, GPIO.OUT)
GPIO.setup(YELLOW_PIN, GPIO.OUT)
GPIO.setup(BUZZER_PIN, GPIO.OUT)

# 初始化所有灯和 buzzer 为 OFF（HIGH 表示 OFF）
GPIO.output(RED_PIN, GPIO.HIGH)
GPIO.output(GREEN_PIN, GPIO.HIGH)
GPIO.output(YELLOW_PIN, GPIO.HIGH)
GPIO.output(BUZZER_PIN, GPIO.HIGH)

# === State Control ===
# --- Startup blinking thread (Fast then slow until correct MUF and first carton（carton！=muf）) ---
green_blink_running = True
green_blink_thread = None

# --- Persistent Red Light & Buzzer Alert Control ---
red_alert_active = False
red_alert_thread = None
buzzer_alert_active = False
buzzer_alert_thread = None

def set_light(pin, state=True):
    GPIO.output(pin, GPIO.LOW if state else GPIO.HIGH)

def blink_light(pin, duration=0.3, times=3): # This function will now only be used for yellow light if needed
    for _ in range(times):
        set_light(pin, True)
        time.sleep(duration)
        set_light(pin, False)
        time.sleep(duration)

def buzz(times=1, duration=0.15): # This function will now only be used for initial buzz if needed
    for _ in range(times):
        GPIO.output(BUZZER_PIN, GPIO.LOW)
        time.sleep(duration)
        GPIO.output(BUZZER_PIN, GPIO.HIGH)
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
    set_light(GREEN_PIN, False) # Ensure light is off when thread terminates

def continuous_red_blink():
    global red_alert_active
    while red_alert_active:
        set_light(RED_PIN, True)
        time.sleep(0.5) # Blink every 0.5 seconds
        set_light(RED_PIN, False)
        time.sleep(0.5)
    set_light(RED_PIN, False) # Ensure light is off when thread terminates

def continuous_buzz():
    global buzzer_alert_active
    while buzzer_alert_active:
        GPIO.output(BUZZER_PIN, GPIO.LOW)
        time.sleep(0.15) # Beep for 0.15 seconds
        GPIO.output(BUZZER_PIN, GPIO.HIGH)
        time.sleep(0.5) # Pause for 0.5 seconds between beeps
    GPIO.output(BUZZER_PIN, GPIO.HIGH) # Ensure buzzer is off when thread terminates

def stop_all_alerts():
    global red_alert_active, buzzer_alert_active, red_alert_thread, buzzer_alert_thread
    debug("Stopping all active alerts...")
    red_alert_active = False
    buzzer_alert_active = False

    if red_alert_thread and red_alert_thread.is_alive():
        red_alert_thread.join(timeout=0.6) # Give it time to finish its current cycle
    set_light(RED_PIN, False) # Ensure red light is off

    if buzzer_alert_thread and buzzer_alert_thread.is_alive():
        buzzer_alert_thread.join(timeout=0.6) # Give it time to finish its current cycle
    GPIO.output(BUZZER_PIN, GPIO.HIGH) # Ensure buzzer is off
    debug("All alerts stopped.")

# New function to start persistent red light and buzzer alerts
def start_red_buzzer_alert():
    global red_alert_active, buzzer_alert_active, red_alert_thread, buzzer_alert_thread
    red_alert_active = True
    buzzer_alert_active = True
    if not (red_alert_thread and red_alert_thread.is_alive()):
        red_alert_thread = threading.Thread(target=continuous_red_blink)
        red_alert_thread.daemon = True
        red_alert_thread.start()
    if not (buzzer_alert_thread and buzzer_alert_thread.is_alive()):
        buzzer_alert_thread = threading.Thread(target=continuous_buzz)
        buzzer_alert_thread.daemon = True
        buzzer_alert_thread.start()


def check_internet():
    try:
        response = os.system("ping -c 1 8.8.8.8 > /dev/null 2>&1")
        return response == 0
    except:
        return False

def update_yellow_light():
    # Make sure we don't start multiple timers
    global yellow_checker_timer
    if 'yellow_checker_timer' in globals() and yellow_checker_timer.is_alive():
        yellow_checker_timer.cancel()

    if check_internet():
        set_light(YELLOW_PIN, True)
    else:
        blink_light(YELLOW_PIN, duration=0.5, times=1) # A single blink for internet status
    yellow_checker_timer = threading.Timer(10, update_yellow_light)
    yellow_checker_timer.daemon = True
    yellow_checker_timer.start()

# Initial call to start the yellow light check thread
update_yellow_light()

# --- Redirect stdout/stderr to log file ---
try:
    log_path = "/home/pi/gwim-scanner/gwim_log.txt"
    sys.stdout = open(log_path, "a", buffering=1)
    sys.stderr = sys.stdout
    debug("🔁 Script started (log ready)")
except Exception as e:
    with open("/home/pi/gwim-scanner/gwim_fallback.txt", "a") as f:
        f.write(f"Logging failed: {e}\n")

# --- Helper functions ---
def safe_int(value):
    try:
        return int(value)
    except:
        return None
        
def is_valid_staff_id(staff_id):
    try:
        debug("Connecting to allocation_m3 for staff verification...")
        connection = mysql.connector.connect(
            host="192.168.20.17",
            user="itadmin",
            password="itadmin@2018",
            database="allocation_m3"
        )
        cursor = connection.cursor()
        query = """
                SELECT staffid FROM staff_list
                WHERE staffpos = 'OPERATOR'
        """
        cursor.execute(query)
        valid_ids = {row[0].strip().upper() for row in cursor.fetchall()}
        connection.close()
        debug(f"Retrieved {len(valid_ids)} staff IDs")
        
        return staff_id.upper() in valid_ids
    except Excpetion as e:
        debug(f"Staff DB connection error: {e}")
        return False

def normalize_barcode(code):
    return (
        code.strip()
            .replace("–", "-")
            .replace("−", "-")
            .replace("—", "-")
            .replace("_", "-")
            .upper()
    )

# --- Global variables ---
CSV_FOLDER = "/home/pi/gwim-scanner/logs"
os.makedirs(CSV_FOLDER, exist_ok=True)

RESET_CODES = {"123456789"}
SCAN_INTERVAL = 2.0  # seconds

current_batch = None
current_muf = None
template_code = None
muf_info = None
last_scan_time = 0
last_barcode = None
barcode_buffer = ""
staff_id = None

csv_lock = threading.Lock()

# --- Database operations ---
def fetch_muf_info(cursor, muf_code):
    debug(f"Querying table 'main' for muf_no = '{muf_code}'")
    cursor.execute("SELECT * FROM main WHERE muf_no = %s", (muf_code,))
    return cursor.fetchone()

def write_to_csv(data, muf_no, uploaded=0, remarks=''):
    with csv_lock:
        filename = os.path.join(CSV_FOLDER, f"{muf_no}_{datetime.now().strftime('%Y%m%d')}.csv")
        is_new = not os.path.exists(filename)
        with open(filename, 'a', newline='') as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow([
                    "muf_no", "line", "fg_no", "pack_per_ctn", "pack_per_hr",
                    "actual_pack", "ctn_count", "scanned_code", "scanned_count",
                    "scanned_at", "scanned_by", "is_uploaded", "remarks"
                ])
            writer.writerow(data + (uploaded, remarks))
        debug(f"📂 Written to CSV: {filename} (uploaded={uploaded})")

def process_and_store(barcode, muf_info, remarks=''):
    pack_per_ctn = safe_int(muf_info["pack_per_ctn"])
    ctn_count = 1
    actual_pack = pack_per_ctn * ctn_count if pack_per_ctn is not None else None

    now = datetime.now()
    timestamp = now.strftime('%Y-%m-%d %H:%M:%S')

    data = (
        current_muf,
        DEVICE_LINE,
        muf_info["fg_no"],
        pack_per_ctn,
        safe_int(muf_info["pack_per_hr"]),
        actual_pack,
        ctn_count,
        barcode,
        1,
        timestamp,
        DEVICE_ID
    )

    try:
        conn = pymysql.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        sql = (
            "INSERT INTO output_log ("
            "muf_no, line, fg_no, pack_per_ctn, pack_per_hr, actual_pack, "
            "ctn_count, scanned_code, scanned_count, scanned_at, scanned_by, remarks"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        cursor.execute(sql, data + (remarks,))
        conn.commit()
        conn.close()
        debug("✅ DB insert successful")
        write_to_csv(data, current_muf, uploaded=1, remarks=remarks)

    except Exception as e:
        debug(f"⚠️ DB insert failed. Cached locally: {e}")
        write_to_csv(data, current_muf, uploaded=0, remarks=remarks)

# --- Upload pending CSV data every 5 minutes ---
def upload_from_csv():
    debug("⏫ Attempting to upload cached CSV data...")
    for file in os.listdir(CSV_FOLDER):
        if not file.endswith(".csv"):
            continue
        path = os.path.join(CSV_FOLDER, file)
        rows = []
        updated = False

        with csv_lock:
            with open(path, 'r', newline='') as f:
                reader = list(csv.reader(f))
                headers = reader[0]
                for row in reader[1:]:
                    # Ensure row has enough columns and is not already marked as uploaded
                    if len(row) > headers.index("is_uploaded") and row[headers.index("is_uploaded")] == "0":
                        rows.append(row)

        if not rows:
            continue

        try:
            conn = pymysql.connect(**MYSQL_CONFIG)
            cursor = conn.cursor()
            for row in rows:
                sql = (
                    "INSERT INTO output_log ("
                    "muf_no, line, fg_no, pack_per_ctn, pack_per_hr, actual_pack, "
                    "ctn_count, scanned_code, scanned_count, scanned_at, scanned_by, remarks"
                    ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                )
                # Map row data to SQL parameters based on headers
                muf_no_idx = headers.index("muf_no")
                line_idx = headers.index("line")
                fg_no_idx = headers.index("fg_no")
                pack_per_ctn_idx = headers.index("pack_per_ctn")
                pack_per_hr_idx = headers.index("pack_per_hr")
                actual_pack_idx = headers.index("actual_pack")
                ctn_count_idx = headers.index("ctn_count")
                scanned_code_idx = headers.index("scanned_code")
                scanned_count_idx = headers.index("scanned_count")
                scanned_at_idx = headers.index("scanned_at")
                scanned_by_idx = headers.index("scanned_by")
                remarks_idx = headers.index("remarks") # Assuming remarks is always the last field before is_uploaded

                data_to_insert = (
                    row[muf_no_idx], row[line_idx], row[fg_no_idx], row[pack_per_ctn_idx],
                    row[pack_per_hr_idx], row[actual_pack_idx], row[ctn_count_idx],
                    row[scanned_code_idx], row[scanned_count_idx], row[scanned_at_idx],
                    row[scanned_by_idx], row[remarks_idx] # remarks is part of data to insert
                )
                cursor.execute(sql, data_to_insert)
            conn.commit()
            conn.close()
            updated = True
        except Exception as e:
            debug(f"⚠️ Upload failed: {e}")

        if updated:
            with csv_lock:
                with open(path, 'r', newline='') as f:
                    reader = list(csv.reader(f))
                with open(path, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(headers) # Write headers back
                    for i in range(1, len(reader)):
                        if len(reader[i]) > headers.index("is_uploaded") and reader[i][headers.index("is_uploaded")] == "0":
                            reader[i][headers.index("is_uploaded")] = "1" # Mark as uploaded
                        writer.writerow(reader[i]) # Write all rows back
                debug(f"✅ Upload complete and marked: {path}")

    # Restart the timer for the next upload attempt
    threading.Timer(300, upload_from_csv).start()


# --- Check if barcode is a RESET code ---
def is_reset_code(barcode):
    normalized = normalize_barcode(barcode)
    return normalized in {normalize_barcode(r) for r in RESET_CODES}
    
def resolve_image_url(path):
    path = path.strip().lstrip("../")
    return f"http://192.168.20.17/{path}"

# --- Barcode scan listener ---
def on_key(event):
    global barcode_buffer, last_barcode, last_scan_time
    global current_batch, current_muf, template_code, muf_info, staff_id
    global green_blink_running, green_blink_thread
    global red_alert_active, red_alert_thread, buzzer_alert_active, buzzer_alert_thread # Add new globals

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

        # --- Always try to stop alerts on any scan, successful or not, before processing ---
        stop_all_alerts()

        if is_reset_code(barcode):
            debug(f"🔄 RESET scanned. Starting new batch")
            current_batch = f"batch_{now.strftime('%Y%m%d_%H%M%S')}"
            current_muf = None
            template_code = None
            muf_info = None

            # Stop any existing green blinking and ensure light is off before restarting
            green_blink_running = False
            if green_blink_thread and green_blink_thread.is_alive():
                green_blink_thread.join(timeout=1) # Wait for thread to finish its current cycle
            set_light(GREEN_PIN, False) # Ensure it's off before starting new blink

            green_blink_running = True  # Enable blinking for the new thread

            # Restart the green blinking thread
            green_blink_thread = threading.Thread(target=continuous_green_blink)
            green_blink_thread.daemon = True
            green_blink_thread.start()
            debug("✅ Green light blinking (RESET)")
        elif any(c.isalpha() for c in normalized_barcode):  # scanned input contains a letter → treat as staff ID
            try:
                conn = mysql.connector.connect(
                    host="192.168.20.17",
                    user="itadmin",
                    password="itadmin@2018",
                    database="allocation_m3"
                )
                cursor = conn.cursor(dictionary=True)
                today_str = datetime.now().strftime('%Y-%m-%d')
                now_dt = datetime.now()

                if staff_id is None:
                    if not is_valid_staff_id(normalized_barcode):
                        debug(f"Invalid staff ID: {normalized_barcode}")
                        blink_light(RED_PIN, times=1)
                        buzz(times=1)
                        return

                    staff_id = normalized_barcode
                    debug(f"✅ Valid staff ID detected: {staff_id}")

                    cursor.execute("SELECT * FROM staff_list WHERE staffid = %s", (staff_id,))
                    staff_row = cursor.fetchone()
                    if not staff_row:
                        debug("❌ Staff ID not found in DB after validation")
                        blink_light(RED_PIN, times=1)
                        buzz(times=1)
                        return

                    shift = staff_row.get("shift", "").upper()
                    shift_value = "DAY" if "DAY" in shift else "NIGHT" if "NIGHT" in shift else ""

                    # allocation_temp
                    cursor.execute("DELETE FROM allocation_temp WHERE staffid = %s", (staff_id,))
                    cursor.execute("""
                            INSERT INTO allocation_temp (staffid, line, staffname, staffpos, staffdept, status, remark, created_date, pic, flg)
                            VALUES (%s, %s, %s, %s, %s, 'IN', '', %s, %s, NULL)
                        """, (
                        staff_id, DEVICE_LINE, staff_row["staffname"], staff_row["staffpos"],
                        staff_row["staffdept"], now_dt.date(), resolve_image_url(staff_row["pic"])
                    ))

                    # allcation_log
                    cursor.execute("SELECT id FROM allcation_log WHERE employee_id = %s AND date_run = %s",
                                   (staff_id, today_str))
                    log_row = cursor.fetchone()
                    if log_row:
                        cursor.execute("UPDATE allcation_log SET out_datetime = %s, status = 'OUT' WHERE id = %s",
                                       (now_dt, log_row["id"]))
                    else:
                        cursor.execute("""
                                INSERT INTO allcation_log (
                                    line, employee_id, name, job_title, department, datetime_log, status, remark,
                                    file_path, date_run, in_datetime, out_datetime, time_taken, shift
                                ) VALUES (%s, %s, %s, %s, %s, %s, 'IN', '', %s, %s, %s, NULL, 0.00, %s)
                            """, (
                            DEVICE_LINE, staff_id, staff_row["staffname"], staff_row["staffpos"],
                            staff_row["staffdept"], now_dt, resolve_image_url(staff_row["pic"]), today_str, now_dt, shift_value
                        ))

                    # prod_attendance
                    cursor.execute("SELECT id FROM prod_attendance WHERE staffid = %s AND date = %s",
                                   (staff_id, today_str))
                    att_row = cursor.fetchone()
                    if att_row:
                        cursor.execute("UPDATE prod_attendance SET timeout = %s WHERE id = %s",
                                       (now_dt, att_row["id"]))
                    else:
                        cursor.execute("""
                                INSERT INTO prod_attendance (
                                    staffid, name, staffpos, staffdept, timein, timeout, work_hr, pic, staffic,
                                    date, shift, flg, staffagency, day
                                ) VALUES (%s, %s, %s, %s, %s, NULL, 0.00, %s, NULL, %s, %s, NULL, %s, %s)
                            """, (
                            staff_id, staff_row["staffname"], staff_row["staffpos"], staff_row["staffdept"],
                            now_dt, resolve_image_url(staff_row["pic"]), today_str, shift_value,
                            staff_row.get("staffagency", ""), calendar.day_name[now_dt.weekday()]
                        ))

                    conn.commit()
                    debug("✅ Staff IN logic complete")
                    blink_light(GREEN_PIN, times=1)
                    buzz(times=1)

                elif normalized_barcode == staff_id:
                    debug(f"🔁 OUT scan for {staff_id}, clearing session")

                    cursor.execute("SELECT id FROM allcation_log WHERE employee_id = %s AND date_run = %s",
                                   (staff_id, today_str))
                    row = cursor.fetchone()
                    if row:
                        cursor.execute("UPDATE allcation_log SET out_datetime = %s, status = 'OUT' WHERE id = %s",
                                       (now_dt, row["id"]))

                    cursor.execute("SELECT id FROM prod_attendance WHERE staffid = %s AND date = %s",
                                   (staff_id, today_str))
                    row = cursor.fetchone()
                    if row:
                        cursor.execute("UPDATE prod_attendance SET timeout = %s WHERE id = %s",
                                       (now_dt, row["id"]))

                    conn.commit()
                    staff_id = None
                    blink_light(GREEN_PIN, times=1)
                    buzz(times=1)

                conn.close()

            except Exception as e:
                debug(f"🔥 Error during staff ID scan: {e}")
                blink_light(RED_PIN, times=1)
                buzz(times=1)

        elif not current_batch:
            debug("⚠️ Please scan RESET first.")
            start_red_buzzer_alert() # Optimized call


        elif current_muf is None:
            try:
                clean_barcode = normalize_barcode(barcode)
                conn = pymysql.connect(**MYSQL_CONFIG, cursorclass=pymysql.cursors.DictCursor)
                cursor = conn.cursor()
                muf_info = fetch_muf_info(cursor, clean_barcode)
                conn.close()
                if muf_info:
                    current_muf = clean_barcode
                    debug(f"✅ MUF found: {current_muf}")
                    # Green light should continue blinking here. No change needed.
                    # Alerts would have been stopped by stop_all_alerts()
                else:
                    debug(f"❌ MUF not found: {clean_barcode}")
                    start_red_buzzer_alert() # Optimized call
            except Exception as e:
                debug(f"⚠️ DB connection error: {e}")
                start_red_buzzer_alert() # Optimized call


        elif template_code is None:
            normalized = normalize_barcode(barcode)
            if normalized == current_muf:
                debug(f"⚠️ Duplicate MUF barcode: {barcode}, ignoring as template")
                start_red_buzzer_alert()
                return   # Keep existing template if duplicate MUF scanned as template
            
            template_code = normalized
            debug(f"🧾 Template barcode set: {template_code}")

            # Stop the green blinking thread gracefully
            green_blink_running = False
            if green_blink_thread and green_blink_thread.is_alive():
                green_blink_thread.join(timeout=1) # Wait for thread to finish its current cycle

            # Ensure it's solid ON after blinking stops
            set_light(GREEN_PIN, True)
            debug("✅ Green light solid ON (Template Set)")
            process_and_store(barcode, muf_info, remarks="TEMPLATE")
            # Alerts would have been stopped by stop_all_alerts()

        elif normalize_barcode(barcode) != template_code:
            debug(f"❌ Barcode mismatch: {normalize_barcode(barcode)} ≠ {template_code}, skipped DB")
            # Green light should remain solid ON if it was. No change needed here.
            start_red_buzzer_alert() # Optimized call

        else: # Barcode matches template_code
            debug(f"✅ Barcode matches template: {template_code}")
            # Green light should remain solid ON. No change needed here.
            process_and_store(template_code, muf_info)
            # Alerts would have been stopped by stop_all_alerts()

    elif len(event.name) == 1:
        barcode_buffer += event.name
    elif event.name == "minus":
        barcode_buffer += "-"

# --- Main entry ---
if __name__ == '__main__':
    # Initialize GPIO outputs to off
    GPIO.output(RED_PIN, GPIO.HIGH)
    GPIO.output(GREEN_PIN, GPIO.HIGH)
    GPIO.output(YELLOW_PIN, GPIO.HIGH)
    GPIO.output(BUZZER_PIN, GPIO.HIGH)

    debug("🔌 GPIO initialized")

    # Start CSV upload thread
    upload_from_csv()

    # Start green blinking thread initially
    green_blink_thread = threading.Thread(target=continuous_green_blink)
    green_blink_thread.daemon = True
    green_blink_thread.start()
    debug("Initial green light blinking started.")

    # Start network checker thread
    # The update_yellow_light function already handles starting a new timer thread, no explicit start needed here.

    # Start keyboard listener
    debug("🧭 Listening for barcode scan via keyboard...")
    keyboard.on_press(on_key)
    keyboard.wait()
