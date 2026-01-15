GWIM Scanner System - User Guide
================================

Purpose
-------
This guide explains how to use the GWIM Scanner System on the production line.
No technical knowledge is required.


System Indicators
-----------------
Green Light
- Fast blinking: System booting
- Slow blinking: System ready / waiting for scan
- Solid ON: Normal scanning (template already set)

Yellow Light
- Solid ON: Internet connected
- Blink once every 10 seconds: Internet disconnected

Red Light
- Blinking or solid ON: Error detected

Buzzer
- Beeping sound indicates an error


Normal Operation Flow
---------------------

Step 1: Turn ON the Scanner
- Power ON the system
- Green light will blink fast 5 times
- Then green light will blink slowly
- Yellow light ON means internet connected

If yellow light is blinking:
- Internet is disconnected
- MUF scanning may not work
- Carton scanning can continue after MUF is set


Step 2: Scan RESET Barcode
- Scan the RESET barcode to start a new batch
- Green light will restart slow blinking

If red light or buzzer turns ON:
- Scan RESET again
- If still fail, restart the scanner


Step 3: Scan MUF Barcode
- Scan the MUF barcode
- System will check MUF in database

If MUF is valid:
- Green light continues slow blinking

If MUF is NOT found:
- Red light and buzzer will activate
- Scan MUF again
- If still fail, request new MUF barcode


Step 4: Scan First Carton (Template)
- Scan the first carton barcode
- This barcode becomes the template
- Green light will turn solid ON

Important:
- Template barcode can only be scanned once


Step 5: Scan Cartons
- Scan cartons one by one

If carton matches template:
- Green light stays ON
- Quantity will increase

If carton does NOT match:
- Red light and buzzer will activate
- Scan the correct carton again
- If error continues, go back to Step 2


Step 6: Change MUF
- Scan RESET barcode
- Repeat Step 3 to Step 5


Common Problems and Solutions
-----------------------------

Problem: LED shows "PI N/A"
Solution:
- Turn OFF the system
- Wait 30 seconds
- Turn ON again

Problem: LED shows "TRY ..."
Solution:
- Wait around 5 minutes
- If still showing, restart the system

Problem: LED shows "Failure connecting"
Solution:
- Wait a few minutes
- Restart the system if needed

Problem: Scanner no response
Solution:
- Restart the scanner
- Repeat Step 1 to Step 5

Problem: Red light keeps blinking
Solution:
- Scan RESET barcode
- Repeat MUF and template steps

Problem: Quantity does not increase after scanning
Solution 1:
- Wait 2 seconds and scan again (duplicate scan protection)
Solution 2:
- Restart the scanner
