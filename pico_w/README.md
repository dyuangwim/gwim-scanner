GWIM Scanner System - Raspberry Pi Pico W Setup Guide
====================================================

This guide explains how to set up the LED matrix display
using Raspberry Pi Pico W.

The Pico W handles:
- Wi-Fi connection
- Fetching data from Raspberry Pi 4 Flask API
- Displaying production data on HUB75 LED matrix


Hardware Required
-----------------
- Raspberry Pi Pico W
- Micro USB cable
- HUB75 LED matrix (128x64)
- 5V high current power supply
- Computer (Windows / Mac / Linux)


Important Notes
---------------
- Pico W runs CircuitPython
- Only "code.py" in root directory will run automatically
- Folder structure inside Pico W is critical


Step 1: Install CircuitPython on Pico W
---------------------------------------
1. Hold BOOTSEL button on Pico W
2. Connect Pico W to computer using USB cable
3. Pico W will appear as USB drive

Download CircuitPython UF2 file for Pico W:
https://circuitpython.org/board/raspberry_pi_pico_w/

Drag and drop the UF2 file into the Pico W drive

Pico W will reboot automatically.


Step 2: Prepare Files for Pico W
--------------------------------
From this repository, use files inside "pico_w" folder only.

Required files:
- code.py
- stretch_bdf_vertically.py (tool only, not auto-run)
- fonts/helvB12-vp.bdf

IMPORTANT:
- code.py MUST be in the root of Pico W
- fonts folder must exist


Step 3: Copy Files to Pico W
----------------------------
Open Pico W drive on your computer.

Copy:
- code.py          -> Pico W root
- fonts/           -> Pico W root
- stretch_bdf_vertically.py (optional, for development)

Do NOT rename code.py.


Step 4: Configure code.py
-------------------------
Open code.py using a text editor.

Set:
- Wi-Fi SSID
- Wi-Fi PASSWORD
- LINE_NAME (must match Pi 4 DEVICE_LINE)
- API port (default: 5001 or 5002)

Save file.


Step 5: Wiring LED Matrix
------------------------
Wire Pico W to HUB75 LED matrix according to HUB75 standard.

Refer to:
https://github.com/gallaugher/pico-and-hub75-led-matrix

Ensure:
- Correct RGB pins
- Correct address pins
- Stable 5V power supply


Step 6: Power On and Test
------------------------
Disconnect Pico W from computer.

Power Pico W and LED matrix.

Expected behavior:
- Pico W connects to Wi-Fi
- Searches for Raspberry Pi 4 API
- LED displays production data


Common Issues
-------------
Problem: LED shows "PI N/A"
Solution:
- Check Raspberry Pi 4 is powered ON
- Check Flask API is running
- Check Wi-Fi network

Problem: LED keeps showing "TRY"
Solution:
- Wait a few minutes
- Pico W is scanning subnet for API

Problem: LED not displaying
Solution:
- Check power supply
- Check HUB75 wiring
- Re-copy code.py
