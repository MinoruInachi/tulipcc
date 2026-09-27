# m5joy.py - support for the m5stack joystick
# returns 0-1 for x, y and 0/1 for button press

from machine import Pin
import time
import tulip

i2c = tulip.grove_i2c()

def get():
    i2c.writeto(0x52, bytes([3]))
    # The joystick's MEGA8A NACKs a read that follows the write straight away;
    # on the Tab5's SoftI2C about half of all reads failed with ENODEV.
    time.sleep_us(100)
    b = i2c.readfrom(0x52, 3)
    x = float(int.from_bytes(bytes([b[0]]), 'big')/255.0)
    y = float(int.from_bytes(bytes([b[1]]), 'big')/255.0)
    z = int.from_bytes(bytes([b[2]]), 'big')
    return (x,y,z)
