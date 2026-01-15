# api_server.py
from flask import Flask, jsonify
import mysql.connector
from datetime import datetime as dt, timedelta

from config import PRODUCTION_DB

app = Flask(__name__)

# -------------------- DB --------------------
def connect_production_db():
    return mysql.connector.connect(
        host=PRODUCTION_DB["host"],
        port=int(PRODUCTION_DB.get("port", 3306)),
        user=PRODUCTION_DB["user"],
        password=PRODUCTION_DB["password"],
        database=PRODUCTION_DB["database"],
        charset="utf8mb4",
    )

# -------------------- Helpers --------------------
EXCLUDE_TEMPLATE_SQL = """
AND (remarks IS NULL OR LOWER(remarks) NOT LIKE '%template%')
"""

def query_latest_muf(line: str):
    conn = connect_production_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT muf_no
            FROM output_log
            WHERE muf_no IS NOT NULL
              AND muf_no <> ''
              AND line = %s
            ORDER BY id DESC
            LIMIT 1
        """, (line,))
        r = cur.fetchone()
        return r["muf_no"] if r else None
    finally:
        cur.close()
        conn.close()

# -------------------- Summary Parts --------------------
def get_total_carton_needed(muf_no: str):
    conn = connect_production_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT qty_done FROM main WHERE muf_no = %s LIMIT 1", (muf_no,))
        r = cur.fetchone()
        return int(r[0]) if r and r[0] is not None else 0
    finally:
        cur.close()
        conn.close()

def get_target_hour(muf_no: str):
    conn = connect_production_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT pack_per_ctn, pack_per_hr
            FROM output_log
            WHERE muf_no = %s
            ORDER BY id DESC
            LIMIT 1
        """, (muf_no,))
        r = cur.fetchone()
        if r and r["pack_per_ctn"] and r["pack_per_hr"]:
            return int(round(float(r["pack_per_hr"]) / float(r["pack_per_ctn"]), 0))
        return 0
    finally:
        cur.close()
        conn.close()

def get_average_hourly_output(muf_no: str, line: str):
    conn = connect_production_db()
    cur = conn.cursor()
    try:
        now = dt.now()
        hour_start = now.replace(minute=0, second=0, microsecond=0)
        hour_end = hour_start + timedelta(hours=1)

        cur.execute(f"""
            SELECT SUM(ctn_count)
            FROM output_log
            WHERE muf_no = %s
              AND line = %s
              AND scanned_at >= %s
              AND scanned_at < %s
              {EXCLUDE_TEMPLATE_SQL}
        """, (muf_no, line, hour_start, hour_end))

        r = cur.fetchone()
        return int(r[0]) if r and r[0] else 0
    finally:
        cur.close()
        conn.close()

def get_balance_carton(muf_no: str):
    conn = connect_production_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT qty_done FROM main WHERE muf_no = %s LIMIT 1", (muf_no,))
        r = cur.fetchone()
        total_needed = int(r[0]) if r and r[0] is not None else 0

        cur.execute(f"""
            SELECT SUM(ctn_count)
            FROM output_log
            WHERE muf_no = %s
            {EXCLUDE_TEMPLATE_SQL}
        """, (muf_no,))
        done = cur.fetchone()
        total_done = int(done[0]) if done and done[0] else 0

        return total_needed - total_done
    finally:
        cur.close()
        conn.close()

def get_balance_hours(muf_no: str):
    conn = connect_production_db()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT pack_per_ctn, pack_per_hr
            FROM output_log
            WHERE muf_no = %s
            ORDER BY id DESC
            LIMIT 1
        """, (muf_no,))
        r = cur.fetchone()
        if not r:
            return 0.0

        pack_per_ctn = float(r["pack_per_ctn"] or 0)
        pack_per_hr = float(r["pack_per_hr"] or 0)
        if pack_per_ctn <= 0 or pack_per_hr <= 0:
            return 0.0

        balance_cartons = get_balance_carton(muf_no)
        return round((balance_cartons * pack_per_ctn) / pack_per_hr, 1)
    finally:
        cur.close()
        conn.close()

# -------------------- API --------------------
@app.route("/summary/<line>", methods=["GET"])
def summary(line):
    muf_no = query_latest_muf(line)
    if not muf_no:
        return jsonify({"error": "No WIP muf_no found"}), 404

    return jsonify({
        "muf_no": muf_no,
        "total_carton_needed": get_total_carton_needed(muf_no),
        "target_hour": get_target_hour(muf_no),
        "avg_hourly_output": get_average_hourly_output(muf_no, line),
        "balance_carton": get_balance_carton(muf_no),
        "balance_hours": get_balance_hours(muf_no),
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
