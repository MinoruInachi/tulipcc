# 8angle.py - support for the m5stack 8angle
# returns the values of each knob
# you can also adapt this to light LEDs and etc

from machine import Pin
import tulip

i2c = tulip.grove_i2c()

def get(num):
    i2c.writeto(67, bytes([0x10 + num]))
    b = i2c.readfrom(67, 1)
    i = int.from_bytes(b, "big")
    return 1.0 - float(i)/255.0



