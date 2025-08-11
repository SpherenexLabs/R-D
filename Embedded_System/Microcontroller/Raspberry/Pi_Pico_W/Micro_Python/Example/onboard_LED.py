from machine import Pin
from time import sleep

led = Pin("LED", Pin.OUT)  # onboard LED for Pico W

while True:
    led.toggle()
    sleep(0.5)