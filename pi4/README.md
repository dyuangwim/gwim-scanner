GWIM Scanner System - Raspberry Pi 4 Setup Guide
===============================================

This guide explains how to set up the GWIM Scanner System
on Raspberry Pi 4 Model B.

This Raspberry Pi 4 handles:
- Barcode scanning
- Production (MUF) validation
- Staff attendance and allocation
- GPIO control (Relay or LED)
- Flask API for LED display (Pico W)


Hardware Required
-----------------
- Raspberry Pi 4 Model B
- MicroSD card (16GB or above)
- USB barcode scanner
- Internet connection
- Power supply


Operating System
----------------
- Raspberry Pi OS (32-bit)
- SSH enabled
- Internet connection required during setup


Step 1: Install Raspberry Pi OS
-------------------------------
1. Use Raspberry Pi Imager
2. Select:
   - Device: Raspberry Pi 4
   - OS: Raspberry Pi OS (32-bit)
3. Enable SSH
4. Set:
   - Username
   - Password
   - Wi-Fi SSID and password
   - Timezone: Asia/Kuala_Lumpur
5. Write OS to MicroSD card
6. Boot Raspberry Pi 4


Step 2: Connect to Raspberry Pi
-------------------------------
Open terminal on Raspberry Pi or connect via SSH.

Update system:
sudo apt update
sudo apt upgrade -y


Step 3: Clone Project from GitHub
--------------------------------
Go to home directory:
cd ~

Clone repository:
git clone https://github.com/dyuangwim/gwim-scanner.git

Go to pi4 folder:
cd gwim-scanner/pi4


Step 4: Install Python Dependencies
-----------------------------------
Install pip:
sudo apt install python3-pip -y

Install required libraries:
sudo pip3 install pymysql mysql-connector-python flask keyboard RPi.GPIO pillow --break-system-packages


Step 5: Configure System
-----------------------
Edit config.py:
nano config.py

Set:
- DEVICE_LINE
- DEVICE_ID
- Database credentials

Save and exit.


Relay or LED Mode:
------------------
If using relay + tower light:
ACTIVE_LOW = True

If using direct LED (no relay):
ACTIVE_LOW = False


Step 6: Test Manually
--------------------
Run main program:
python3 main.py

In another terminal, run API server:
python3 api_server.py

Scan barcode and verify:
- GPIO response
- Database insert
- API is accessible


Step 7: Enable Auto-Run on Boot
-------------------------------
Open root crontab:
sudo crontab -e

Add the following lines:

@reboot python3 /home/pi/gwim-scanner/pi4/main.py &
@reboot python3 /home/pi/gwim-scanner/pi4/api_server.py &

Save and exit:
Ctrl + O
Enter
Ctrl + X


Step 8: Reboot and Verify
------------------------
Reboot system:
sudo reboot

After reboot:
- Green light should blink
- Scanner should respond
- API should be running


Log Files
---------
- Main log file:
  /home/pi/gwim-scanner/logs/gwim_log.txt

If log folder has permission issue:
sudo chown -R pi:pi /home/pi/gwim-scanner/logs
sudo chmod -R 775 /home/pi/gwim-scanner/logs
