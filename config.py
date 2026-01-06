# config.py
# ============================================================
# Option 1 (Line-based, independent): use INTRANET DB host.
# Do NOT use public 149.28.152.191 because port 3306 is closed for audit.
# ============================================================

# ---------- Production DB (for MUF lookup + output_log insert) ----------
# ---------- Production DB (Cloud) ----------
PRODUCTION_DB = {
    "host": "149.28.152.191",
    "port": 13306,                 
    "user": "raspberry_pi_scanner_led",
    "password": "OAbLvYkknKs*p0IQ",
    "database": "production",
}

# ---------- Staff DB (allocation_m3) ----------
STAFF_DB = {
    "host": "192.168.20.17",
    "port": 3306,
    "user": "itadmin",
    "password": "itadmin@2018",
    "database": "allocation_m3",
}

# ---------- Device identity ----------
DEVICE_LINE = "HF6"      # e.g. HF5, HF6 ...
DEVICE_ID = "RPI-01"     # unique per unit

# ---------- Local files ----------
CSV_FOLDER = "/home/pi/gwim-scanner/logs"
LOG_PATH = "/home/pi/gwim-scanner/gwim_log.txt"

# ---------- Barcode behavior ----------
RESET_CODES = {"123456789"}
SCAN_INTERVAL = 2.0  # prevent duplicate scan within this seconds

# ---------- DB timeouts ----------
DB_CONNECT_TIMEOUT = 3
DB_READ_TIMEOUT = 5
DB_WRITE_TIMEOUT = 5

# ---------- Upload interval ----------
UPLOAD_INTERVAL_SEC = 300  # 5 minutes



