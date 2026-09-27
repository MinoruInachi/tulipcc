# m5cardkb.py - driver for CardKB keyboard

# If you import m5cardkb.py, Tulip will take input from the cardKB
# it uses the frame callback to "thread" out the keyboard listener
# this means that a game or etc may take control of the KB until you add it back 
# You can adapt this to read characters without sending them to Tulip

from machine import Pin
import tulip

i2c = tulip.grove_i2c()

# The CardKB sends its arrow keys as single bytes 0xB4-0xB7; Tulip's arrow keys
# are 258-261 (see keyscan.c), so without this they arrived as nothing at all.
_KEYMAP = {0xB4: 260, 0xB5: 259, 0xB6: 258, 0xB7: 261}  # left, up, down, right

# Frames left before looking for an unplugged CardKB again. An unplugged one
# makes readfrom() raise OSError, and letting that out of a frame callback
# printed it every frame; checking about twice a second instead also picks the
# keyboard back up when it is plugged in again.
_skip = 0

def cardkb_callback(stuff):
    global _skip
    if _skip:
        _skip -= 1
        return
    try:
        b = i2c.readfrom(0x5f,1)
    except OSError:
        _skip = 30
        return
    if(len(b)):
        if b[0] != 0:
            tulip.key_send(_KEYMAP.get(b[0], b[0]))
            
tulip.frame_callback(cardkb_callback)

