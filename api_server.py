import math

from flask import Flask, jsonify
import mysql.connector
from datetime import datetime as dt, timedelta
from contextlib import contextmanager

app = Flask(__name__)

def connect_production_db():
    return mysql.connector.connect(
        host="149.28.152.191",
        user="root",
        password="fb267146d223afbd24e88d5ea8090b0b8cdc4254146fa62d",
        database="production",
        charset='utf8mb4'
    )

@contextmanager
def get_cursor(dict_mode=False):
    conn = connect_production_db()
    cursor = conn.cursor(dictionary=dict_mode)
    try:
        yield cursor
    finally:
        cursor.close()
        conn.close()

def query_database(line):
    try:
        with get_cursor(dict_mode=True) as cursor:
            cursor.execute("""
                            SELECT muf_no FROM output_log
                            WHERE muf_no IS NOT NULL AND muf_no != '' AND line = %s
                            ORDER BY id DESC
                            LIMIT 1
                        """, (line,))
            result = cursor.fetchone()
            return result if result else {"message": "No recent muf_no found"}

    except mysql.connector.Error as err:
        return {"error": str(err)}

def get_output_info(muf_no):
    with get_cursor(dict_mode=True) as cursor:
        # 1. Get qty_done, pack_per_ctn, pack_per_hr from main
        cursor.execute("""
            SELECT qty_done, pack_per_ctn, pack_per_hr
            FROM main
            WHERE muf_no = %s
            LIMIT 1
        """, (muf_no,))
        main_data = cursor.fetchone()

        if not main_data:
            return None

        # 2. Get total cartons done from output_log
        cursor.execute("""
            SELECT SUM(ctn_count) AS done_cartons
            FROM output_log
            WHERE muf_no = %s AND (remarks IS NULL OR LOWER(remarks) NOT LIKE '%%template%%')
        """, (muf_no,))
        done_result = cursor.fetchone()
        main_data['done_cartons'] = done_result['done_cartons'] or 0

        return main_data


def get_average_hourly_output(muf_no, line):
    try:
        now = dt.now()
        hour_start = now.replace(minute=1, second=0, microsecond=0)
        hour_end = hour_start + timedelta(minutes=59)
        query = """
            SELECT SUM(ctn_count) FROM output_log
            WHERE muf_no = %s AND line = %s AND scanned_at BETWEEN %s AND %s AND (remarks IS NULL OR LOWER(remarks) NOT LIKE '%%template%%')
        """
        with get_cursor(dict_mode=True) as cursor:
            cursor.execute(query, (muf_no, line, hour_start, hour_end))
            result = cursor.fetchone()
            if result:
                # Dynamically get first column name
                key = cursor.column_names[0] # type: ignore[attr-defined]
                return int(result[key]) if result[key] else 0
            return 0
    except mysql.connector.Error as err:
        return {"error": str(err)}

@app.route('/query', methods=['GET'])
def get_query():
    data = query_database()
    if isinstance(data, dict) and "muf_no" in data:
        return data["muf_no"]
    else:
        return "No WIP found"

@app.route('/summary/<line>', methods=['GET'])
def get_summary(line):
    data = query_database(line)
    if not isinstance(data, dict) or "muf_no" not in data:
        return jsonify({"error": "No WIP muf_no found"}), 404

    muf_no = data["muf_no"]
    output_info = get_output_info(muf_no)
    if not output_info:
        return jsonify({"error": "No output data found"}), 404
    qty_done = int(output_info['qty_done'] or 0)
    pack_per_ctn = float(output_info['pack_per_ctn'] or 1)
    pack_per_hr = float(output_info['pack_per_hr'] or 1)
    done_cartons = int(output_info['done_cartons'] or 0)

    balance_cartons = qty_done - done_cartons
    balance_hours = round((balance_cartons * pack_per_ctn) / pack_per_hr, 1) if pack_per_hr else 0

    summary = {
        "muf_no": muf_no,
        "total_carton_needed": qty_done,
        "target_hour": math.ceil(pack_per_hr / pack_per_ctn),
        "avg_hourly_output": get_average_hourly_output(muf_no, line),
        "balance_carton": balance_cartons,
        "balance_hours": balance_hours
    }
    return jsonify(summary)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
