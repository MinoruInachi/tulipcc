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

def cardkb_callback(stuff):
    b = i2c.readfrom(0x5f,1)
    if(len(b)):
        if b[0] != 0:
            tulip.key_send(_KEYMAP.get(b[0], b[0]))
            
tulip.frame_callback(cardkb_callback)

