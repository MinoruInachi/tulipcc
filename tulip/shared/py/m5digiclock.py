# control an m5stack digi-clock 
from machine import Pin
import tulip
i2c = tulip.grove_i2c()

# Set with a 4 character string
def set(s):
    i2c.writeto_mem(0x30, 0x20, bytes(s.encode('ascii')))
