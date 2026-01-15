GWIM Scanner System - Hardware Guide
===================================

Overview
--------
The GWIM Scanner System supports two hardware configurations:
1. Relay + Tower Light + Buzzer
2. Direct LED (No Relay)

Both configurations use the same software logic.
Only wiring and GPIO polarity are different.


Core Hardware
-------------
- Raspberry Pi 4 Model B
- MicroSD Card
- USB Barcode Scanner
- Power Supply


Hardware Version A: Relay + Tower Light + Buzzer
------------------------------------------------

Components:
- Raspberry Pi 4 Model B
- 8-channel relay module
- Tower light (Red, Yellow, Green)
- Buzzer

Relay Wiring (Raspberry Pi 4 GPIO):
- RED    -> GPIO 5
- GREEN  -> GPIO 6
- YELLOW -> GPIO 13
- BUZZER -> GPIO 19

Relay Power:
- VCC -> 3.3V
- GND -> GND

Tower Light and Buzzer:
- Wired to relay Normally Open (NO) contacts


Software Setting:
In main code:
ACTIVE_LOW = True

Reason:
- Relay is triggered when GPIO is LOW


Hardware Version B: Direct LED (No Relay)
-----------------------------------------

Components:
- Raspberry Pi 4 Model B
- LED bulbs (Red, Yellow, Green)
- Optional buzzer

LED Wiring:
- RED LED    -> GPIO 5
- GREEN LED  -> GPIO 6
- YELLOW LED -> GPIO 13
- BUZZER     -> GPIO 19 (optional)

Note:
- Use appropriate resistor for each LED
- Do NOT connect LED directly without resistor


Software Setting:
In main code:
ACTIVE_LOW = False

Reason:
- LED turns ON when GPIO is HIGH


GPIO Summary Table
------------------
GPIO 5   : Red indicator
GPIO 6   : Green indicator
GPIO 13  : Yellow indicator
GPIO 19  : Buzzer


Raspberry Pi Pico W (LED Matrix Display)
----------------------------------------

Purpose:
- Display real-time production status

Components:
- Raspberry Pi Pico W
- HUB75 LED Matrix (128x64)
- 5V high current power supply

Connection:
- Pico W connects to Raspberry Pi 4 via Wi-Fi
- Data is retrieved through Flask API

Important Notes:
- Pico W auto-discovery is based on subnet scanning
- Flask API must be running on Raspberry Pi 4


Power Notes
-----------
- Tower light and LED matrix require stable power supply
- Insufficient power may cause random reset or display failure


Maintenance Notes
-----------------
- Always power OFF before rewiring
- Double-check GPIO numbers before testing
- After hardware change, verify ACTIVE_LOW setting


<img width="1350" height="757" alt="image" src="https://github.com/user-attachments/assets/36acfce7-5a67-47a8-b8ac-878887d60f04" />
<img width="940" height="529" alt="image" src="https://github.com/user-attachments/assets/ffe54bc7-d49e-40fd-bb46-c56f88f79b95" />
