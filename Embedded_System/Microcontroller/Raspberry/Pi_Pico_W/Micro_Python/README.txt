Working with Rasberry Pi Pico W Using MicroPython

1. Download & Install Thonny https://thonny.org/

2. Flash MicroPython to Pico (W)
	
	2.1 - Unplug the Pico
	2.2 - Hold down the BOOTSEL button
	2.3 - Plug into USB — a new drive appears (e.g., RPI-RP2)
	2.4 - Download MicroPython UF2 file -> RPI_PICO_W-20250415-v1.25.0.uf2 (Present in Bootloader) - https://micropython.org/download/RPI_PICO_W/
	2.5 - Drag & drop the .uf2 file into the drive
	
	The Pico reboots into MicroPython mode.

3. Open Thonny and Set Up Pico
	
	3.1 - Open Thonny
	3.2 - Go to Tools -> Options -> Interpreter
		3.2.1 - Interpreter: MicroPython (Raspberry Pi Pico)
		3.2.2 - Port: Select the detected COM port (or /dev/ttyACM0 on Linux)
	3.3 - Click OK

4. Test With Example Code 

	4.1 - onboard_LED.py
	4.2 - Click RUN (top-left) -  the onboard LED should blink.

5. Save Your Code to Pico

	5.1 - Press Ctrl+S
	5.2 - Select "Save to Raspberry Pi Pico"
	5.3 - Name it main.py if you want it to run automatically on boot