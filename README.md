GWIM Raspberry Pi Scanner System
================================

Overview
--------
GWIM Raspberry Pi Scanner System is an industrial barcode scanning
and production tracking system designed for manufacturing lines.

Main features:
- Barcode scanning
- MUF validation
- Carton verification
- Staff attendance and allocation
- Tower light / LED / buzzer alert
- Real-time production display via LED matrix

The system consists of:
- Raspberry Pi 4 Model B (Main Controller)
- Raspberry Pi Pico W (LED Display Controller)


System Architecture
-------------------
Barcode Scanner
  -> Raspberry Pi 4
     - Scanner logic
     - Staff logic
     - Database connection
     - GPIO control
     - Flask API
  -> Tower Light / LED / Buzzer
  -> Wi-Fi API
     -> Raspberry Pi Pico W
        - HUB75 LED Matrix display


Hardware Versions
-----------------

Version A: Relay + Tower Light + Buzzer
- Raspberry Pi 4 Model B
- 8-channel relay module
- Tower light (Red / Yellow / Green)
- Buzzer
- USB barcode scanner

Used in production lines that require strong visual and audio alerts.


Version B: Direct LED (No Relay)
- Raspberry Pi 4 Model B
- GPIO LED bulbs (Red / Yellow / Green)
- USB barcode scanner

Used for smaller lines or simplified setup.


Hardware Requirements
---------------------
- Raspberry Pi 4 Model B
- MicroSD card (16GB or above)
- USB barcode scanner
- Power supply

Optional:
- Raspberry Pi Pico W
- HUB75 LED matrix
- 5V high current power supply


Software Components
-------------------
main_staff_fixed_v4_debug.py
- Main scanner logic
- Production scanning
- Staff attendance
- GPIO control

api_server.py
- Flask API
- Provides production summary for LED display

config.py
- Line configuration
- Device ID
- Database configuration

code.py (Pico W)
- Connects to Raspberry Pi 4 via Wi-Fi
- Displays production data on LED matrix


Installation
------------
1. Install Raspberry Pi OS (32-bit)
2. Enable SSH
3. Clone project repository
4. Install Python dependencies
5. Configure auto-run on boot


Configuration
-------------
Edit config.py:
- DEVICE_LINE
- DEVICE_ID
- Database credentials

Relay or LED mode:
- ACTIVE_LOW = True   (Relay)
- ACTIVE_LOW = False  (Direct LED)


Usage Flow
----------
1. Power ON system
2. Green light blinks (system ready)
3. Scan RESET barcode
4. Scan MUF barcode
5. Scan first carton as template
6. Scan cartons
7. Error -> red light + buzzer


LED and Buzzer Behavior
-----------------------
Green LED:
- Fast blink: boot
- Slow blink: waiting for MUF/template
- Solid ON: normal scanning

Red LED:
- Blink or solid: error

Yellow LED:
- ON: internet connected
- Blink: internet disconnected

Buzzer:
- Sounds on error


Project Structure
-----------------
gwim-scanner/
- main_staff_fixed_v4_debug.py
- api_server.py
- config.py
- code.py
- stretch_bdf_vertically.py
- logs/
- README.md
