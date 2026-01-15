import time
import wifi
import socketpool
import adafruit_requests
import board
import displayio
import framebufferio
import rgbmatrix
import traceback
import gc
import microcontroller

from adafruit_display_text.label import Label
from adafruit_bitmap_font import bitmap_font

# =========================
# Wi-Fi credentials
# =========================
SSID = "GWI_IOT"
PASSWORD = "G@wi.20_25"

# =========================
# Discovery Config
# =========================
BASE_IP = "10.3.0."     # fixed subnet
LINE_NAME = "HF5"       # set per Pico
PORT = 5002             # scanner api_server.py port

# Endpoints
def url_health(ip):
    return f"http://{ip}:{PORT}/health"

def url_summary(ip):
    return f"http://{ip}:{PORT}/summary/{LINE_NAME}"

API_HOST = None
requests = None

# Stability controls
FAIL_COUNT = 0
MAX_FAIL_BEFORE_REBOOT = 25   # ~25*3s ≈ 75s, you can tune
FETCH_INTERVAL_SEC = 3

# =========================
# Display setup
# =========================
displayio.release_displays()
matrix = rgbmatrix.RGBMatrix(
    width=128,
    height=64,
    bit_depth=6,
    rgb_pins=[board.GP2, board.GP3, board.GP6, board.GP7, board.GP8, board.GP9],
    addr_pins=[board.GP10, board.GP16, board.GP18, board.GP20],
    clock_pin=board.GP11,
    latch_pin=board.GP12,
    output_enable_pin=board.GP13,
    tile=2,
    serpentine=True,
    doublebuffer=False,
)
display = framebufferio.FramebufferDisplay(matrix)

font_big = bitmap_font.load_font("/fonts/helvB12-vp.bdf")
gc.collect()

def display_summary_quadrants(data):
    group = displayio.Group()

    muf_full = str(data.get("muf_no", "-"))
    muf = muf_full[-6:] if len(muf_full) > 6 else muf_full

    total = str(data.get("total_carton_needed", "-"))
    target = str(data.get("target_hour", "-"))
    avg = str(data.get("avg_hourly_output", "-"))
    bal_carton = str(data.get("balance_carton", "-"))
    bal_hour = str(data.get("balance_hours", "-"))

    try:
        target_val = float(target)
        avg_val = float(avg)
        avg_color = 0x00FF00 if avg_val >= target_val else 0xFF0000
    except Exception:
        avg_color = 0xAAAAAA

    label_muf = Label(font_big, text=muf, color=0x00FFFF); label_muf.x = 2; label_muf.y = 20

    label_total = Label(font_big, text=total, color=0xFFFFFF)
    total_width = label_total.bounding_box[2]
    label_total.x = 64 + max(0, (64 - total_width) // 2); label_total.y = 20

    label_target = Label(font_big, text=target, color=0xFFFF00); label_target.x = 2; label_target.y = 52
    label_avg = Label(font_big, text=avg, color=avg_color); label_avg.x = 34; label_avg.y = 52

    label_bal_carton = Label(font_big, text=bal_carton, color=0xFFFF00); label_bal_carton.x = 56; label_bal_carton.y = 52
    label_bal_hour = Label(font_big, text=bal_hour, color=0x00008B); label_bal_hour.x = 96; label_bal_hour.y = 52

    group.append(label_muf)
    group.append(label_total)
    group.append(label_target)
    group.append(label_avg)
    group.append(label_bal_carton)
    group.append(label_bal_hour)

    display.root_group = group
    gc.collect()

def connect_wifi_and_setup_session():
    global requests
    while True:
        try:
            print("Connecting to WiFi...")
            wifi.radio.connect(SSID, PASSWORD)
            print("Connected to", SSID)
            print("IP:", wifi.radio.ipv4_address)

            pool = socketpool.SocketPool(wifi.radio)
            requests = adafruit_requests.Session(pool)
            gc.collect()
            return
        except Exception as e:
            print("Wi-Fi connection failed, retrying...")
            traceback.print_exception(e)
            time.sleep(2)

def session_reset():
    """Drop requests session; next loop will recreate it."""
    global requests
    requests = None
    gc.collect()

def find_scanner_pi():
    """
    Discover Scanner Pi by calling /health (always returns 200 if API is alive).
    This avoids false negatives when /summary/<line> has no data yet (404).
    """
    global API_HOST

    while True:
        display_summary_quadrants({"muf_no": "SCANNING"})
        print("Scanning subnet for Scanner Pi (/health)...")

        for i in range(1, 255):
            ip = f"{BASE_IP}{i}"
            url = url_health(ip)
            print("Trying", url)
            display_summary_quadrants({"muf_no": f"Try {i}"})

            try:
                r = requests.get(url, timeout=2)
                ok = (r.status_code == 200)
                r.close()

                if ok:
                    API_HOST = ip
                    print("Scanner Pi found:", API_HOST)
                    return
            except Exception:
                pass

            gc.collect()

        print("No Scanner Pi found. Retry scan...")
        display_summary_quadrants({"muf_no": "NO PI"})
        time.sleep(3)

def fetch_and_display():
    """
    Fetch /summary/<LINE_NAME>.
    - If 200: display data.
    - If 404 (no WIP yet): display NO WIP (NOT PI N/A).
    """
    global FAIL_COUNT, API_HOST

    url = url_summary(API_HOST)
    r = None
    try:
        r = requests.get(url, timeout=4)

        if r.status_code == 200:
            data = r.json()
            display_summary_quadrants(data)
            FAIL_COUNT = 0
            return

        if r.status_code == 404:
            # Pi is alive but this line has no data yet
            display_summary_quadrants({"muf_no": "NO WIP"})
            FAIL_COUNT = 0
            return

        # other status -> treat as error
        display_summary_quadrants({"muf_no": "API ERR"})
        FAIL_COUNT += 1

    finally:
        try:
            if r:
                r.close()
        except Exception:
            pass
        gc.collect()

# =========================
# Main
# =========================
connect_wifi_and_setup_session()
find_scanner_pi()

while True:
    try:
        if requests is None:
            connect_wifi_and_setup_session()

        if API_HOST is None:
            find_scanner_pi()

        fetch_and_display()
        time.sleep(FETCH_INTERVAL_SEC)

    except Exception as e:
        print("Error:", e)
        traceback.print_exception(e)

        # Show PI N/A only when we truly cannot reach the Pi
        display_summary_quadrants({"muf_no": "PI N/A"})
        FAIL_COUNT += 1

        # Force rebuild session (very important for CircuitPython)
        session_reset()

        # If too many consecutive failures, reboot the Pico to self-heal
        if FAIL_COUNT >= MAX_FAIL_BEFORE_REBOOT:
            display_summary_quadrants({"muf_no": "REBOOT"})
            time.sleep(2)
            microcontroller.reset()

        time.sleep(3)
