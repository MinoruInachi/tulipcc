// keyscan.c
#include "keyscan.h"

#ifndef ESP_PLATFORM
#include <SDL.h>
#endif

uint8_t keyboard_send_keys_to_micropython = 1;
int8_t keyboard_grab_ui_focus = -1;
uint8_t last_held_code = 0;
uint16_t last_held_modifier = 0;

extern int mp_interrupt_char;
uint8_t last_scan[8] = {0,0,0,0,0,0,0,0};

// Keep track of key_remaps
key_remap key_remaps[MAX_KEY_REMAPS];

/*
 * The Japanese IME's key queue.
 *
 * While ime_active the key path hands every key to the IME and to nothing else --
 * not the REPL, not LVGL, not the editor -- because a key the IME is going to
 * turn into part of 「日本語」 must not also arrive somewhere as a Latin letter.
 * The IME itself is Python (shared/py/ime.py): the dictionary is a deflate stream
 * that only MicroPython's deflate module can open, and once the dictionary is in
 * Python the conversion belongs there too.
 *
 * A queue rather than a callback per key, because mp_sched_schedule() silently
 * fails when its queue is full and MICROPY_SCHEDULER_DEPTH is 4. Keys land here
 * from the keyboard task and the IME drains them from a callback scheduled once
 * per frame, so a full scheduler queue costs a frame of latency instead of a lost
 * keystroke. 64 is far more than the deepest burst a person can type in 16ms.
 */
uint8_t ime_active = 0;
#define IME_KEY_RING 64
static volatile uint16_t ime_ring[IME_KEY_RING];
static volatile uint8_t ime_ring_w = 0;
static volatile uint8_t ime_ring_r = 0;

void ime_push_key(uint16_t key) {
    uint8_t next = (uint8_t)((ime_ring_w + 1) % IME_KEY_RING);
    // Full. Drop the newest rather than advancing the read pointer: losing the
    // key just pressed is confusing, but losing one from the middle of a
    // half-composed reading is worse.
    if(next == ime_ring_r) return;
    ime_ring[ime_ring_w] = key;
    ime_ring_w = next;
}

// -1 when the queue is empty.
int32_t ime_take_key(void) {
    if(ime_ring_r == ime_ring_w) return -1;
    uint16_t key = ime_ring[ime_ring_r];
    ime_ring_r = (uint8_t)((ime_ring_r + 1) % IME_KEY_RING);
    return (int32_t)key;
}

void ime_flush_keys(void) {
    ime_ring_r = ime_ring_w;
}


// Go _FROM_ cp437 to utf8 bytes
const uint8_t cp437_to_utf8[] = {
0xc3,0x87,0x0,
0xc3,0xbc,0x0,
0xc3,0xa9,0x0,
0xc3,0xa2,0x0,
0xc3,0xa4,0x0,
0xc3,0xa0,0x0,
0xc3,0xa5,0x0,
0xc3,0xa7,0x0,
0xc3,0xaa,0x0,
0xc3,0xab,0x0,
0xc3,0xa8,0x0,
0xc3,0xaf,0x0,
0xc3,0xae,0x0,
0xc3,0xac,0x0,
0xc3,0x84,0x0,
0xc3,0x85,0x0,
0xc3,0x89,0x0,
0xc3,0xa6,0x0,
0xc3,0x86,0x0,
0xc3,0xb4,0x0,
0xc3,0xb6,0x0,
0xc3,0xb2,0x0,
0xc3,0xbb,0x0,
0xc3,0xb9,0x0,
0xc3,0xbf,0x0,
0xc3,0x96,0x0,
0xc3,0x9c,0x0,
0xc2,0xa2,0x0,
0xc2,0xa3,0x0,
0xc2,0xa5,0x0,
0xe2,0x82,0xa7,
0xc6,0x92,0x0,
0xc3,0xa1,0x0,
0xc3,0xad,0x0,
0xc3,0xb3,0x0,
0xc3,0xba,0x0,
0xc3,0xb1,0x0,
0xc3,0x91,0x0,
0xc2,0xaa,0x0,
0xc2,0xba,0x0,
0xc2,0xbf,0x0,
0xe2,0x8c,0x90,
0xc2,0xac,0x0,
0xc2,0xbd,0x0,
0xc2,0xbc,0x0,
0xc2,0xa1,0x0,
0xc2,0xab,0x0,
0xc2,0xbb,0x0,
0xe2,0x96,0x91,
0xe2,0x96,0x92,
0xe2,0x96,0x93,
0xe2,0x94,0x82,
0xe2,0x94,0xa4,
0xe2,0x95,0xa1,
0xe2,0x95,0xa2,
0xe2,0x95,0x96,
0xe2,0x95,0x95,
0xe2,0x95,0xa3,
0xe2,0x95,0x91,
0xe2,0x95,0x97,
0xe2,0x95,0x9d,
0xe2,0x95,0x9c,
0xe2,0x95,0x9b,
0xe2,0x94,0x90,
0xe2,0x94,0x94,
0xe2,0x94,0xb4,
0xe2,0x94,0xac,
0xe2,0x94,0x9c,
0xe2,0x94,0x80,
0xe2,0x94,0xbc,
0xe2,0x95,0x9e,
0xe2,0x95,0x9f,
0xe2,0x95,0x9a,
0xe2,0x95,0x94,
0xe2,0x95,0xa9,
0xe2,0x95,0xa6,
0xe2,0x95,0xa0,
0xe2,0x95,0x90,
0xe2,0x95,0xac,
0xe2,0x95,0xa7,
0xe2,0x95,0xa8,
0xe2,0x95,0xa4,
0xe2,0x95,0xa5,
0xe2,0x95,0x99,
0xe2,0x95,0x98,
0xe2,0x95,0x92,
0xe2,0x95,0x93,
0xe2,0x95,0xab,
0xe2,0x95,0xaa,
0xe2,0x94,0x98,
0xe2,0x94,0x8c,
0xe2,0x96,0x88,
0xe2,0x96,0x84,
0xe2,0x96,0x8c,
0xe2,0x96,0x90,
0xe2,0x96,0x80,
0xce,0xb1,0x0,
0xc3,0x9f,0x0,
0xce,0x93,0x0,
0xcf,0x80,0x0,
0xce,0xa3,0x0,
0xcf,0x83,0x0,
0xc2,0xb5,0x0,
0xcf,0x84,0x0,
0xce,0xa6,0x0,
0xce,0x98,0x0,
0xce,0xa9,0x0,
0xce,0xb4,0x0,
0xe2,0x88,0x9e,
0xcf,0x86,0x0,
0xce,0xb5,0x0,
0xe2,0x88,0xa9,
0xe2,0x89,0xa1,
0xc2,0xb1,0x0,
0xe2,0x89,0xa5,
0xe2,0x89,0xa4,
0xe2,0x8c,0xa0,
0xe2,0x8c,0xa1,
0xc3,0xb7,0x0,
0xe2,0x89,0x88,
0xc2,0xb0,0x0,
0xe2,0x88,0x99,
0xc2,0xb7,0x0,
0xe2,0x88,0x9a,
0xe2,0x81,0xbf,
0xc2,0xb2,0x0,
0xe2,0x96,0xa0,
0xc2,0xa0,0x0
};



const uint8_t code_points_00a0[] = {
   255, 173, 155, 156,   7, 157,   7,  21,
     7,   7, 166, 174, 170,   7,   7,   7,
   248, 241, 253,   7,   7, 230,  20, 250,
     7,   7, 167, 175, 172, 171,   7, 168,
     7,   7,   7,   7, 142, 143, 146, 128,
     7, 144,   7,   7,   7,   7,   7,   7,
     7, 165,   7,   7,   7,   7, 153,   7,
     7,   7,   7,   7, 154,   7,   7, 225,
   133, 160, 131,   7, 132, 134, 145, 135,
   138, 130, 136, 137, 141, 161, 140, 139,
     7, 164, 149, 162, 147,   7, 148, 246,
     7, 151, 163, 150, 129,   7,   7, 152,
};

const uint8_t code_points_2550[] = {
   205, 186, 213, 214, 201, 184, 183, 187,
   212, 211, 200, 190, 189, 188, 198, 199,
   204, 181, 182, 185, 209, 210, 203, 207,
   208, 202, 216, 215, 206
};

static uint8_t utf8_convert_11bit(uint16_t code)
{
   switch (code) {
   case 0x0192: return 159;
   case 0x0393: return 226;
   case 0x0398: return 233;
   case 0x03A3: return 228;
   case 0x03A6: return 232;
   case 0x03A9: return 234;
   case 0x03B1: return 224;
   case 0x03B4: return 235;
   case 0x03B5: return 238;
   case 0x03C0: return 227;
   case 0x03C3: return 229;
   case 0x03C4: return 231;
   case 0x03C6: return 237;
   }

   return 0;
};

static uint8_t utf8_convert_2xxx(uint16_t code)
{
   switch (code) {
   case 0x2022: return 7;
   case 0x203C: return 19;
   case 0x207F: return 252;
   case 0x20A7: return 158;
   case 0x2190: return 27;
   case 0x2191: return 24;
   case 0x2192: return 26;
   case 0x2193: return 25;
   case 0x2194: return 29;
   case 0x2195: return 18;
   case 0x21A8: return 23;
   case 0x2219: return 249;
   case 0x221A: return 251;
   case 0x221E: return 236;
   case 0x221F: return 28;
   case 0x2229: return 239;
   case 0x2248: return 247;
   case 0x2261: return 240;
   case 0x2264: return 243;
   case 0x2265: return 242;
   case 0x2310: return 169;
   case 0x2320: return 244;
   case 0x2321: return 245;
   case 0x2500: return 196;
   case 0x2502: return 179;
   case 0x250C: return 218;
   case 0x2510: return 191;
   case 0x2514: return 192;
   case 0x2518: return 217;
   case 0x251C: return 195;
   case 0x2524: return 180;
   case 0x252C: return 194;
   case 0x2534: return 193;
   case 0x253C: return 197;
   case 0x2580: return 223;
   case 0x2584: return 220;
   case 0x2588: return 219;
   case 0x258C: return 221;
   case 0x2590: return 222;
   case 0x2591: return 176;
   case 0x2592: return 177;
   case 0x2593: return 178;
   case 0x25A0: return 254;
   case 0x25AC: return 22;
   case 0x25B2: return 30;
   case 0x25BA: return 16;
   case 0x25BC: return 31;
   case 0x25C4: return 17;
   case 0x25CB: return 9;
   case 0x25D8: return 8;
   case 0x25D9: return 10;
   case 0x263A: return 1;
   case 0x263B: return 2;
   case 0x263C: return 15;
   case 0x2640: return 12;
   case 0x2642: return 11;
   case 0x2660: return 6;
   case 0x2663: return 5;
   case 0x2665: return 3;
   case 0x2666: return 4;
   case 0x266A: return 13;
   case 0x266B: return 14;
   }

   return 0;
}

uint8_t convert_uc16_to_cp437(uint16_t code)
{
   if (code < 0x7f)        // ASCII
       return code;
   if (code < 0xa0)        // high control characters
       return code;
   if (code < 0x100)       // international characters
       return code_points_00a0[code - 0xa0];
   if (code < 0x800)
       return utf8_convert_11bit(code);
   if (code >= 0x2550 && code < 0x256d)    // block graphics
       return code_points_2550[code - 0x2550];

   return utf8_convert_2xxx(code);
}

// The UTF-8 state machine, stopping at the codepoint. This used to be inlined in
// convert_utf8_to_cp437() below and there was no way to get at the codepoint on
// the way past -- which is all a Japanese console wants, since folding to CP437
// is exactly the step that throws Japanese away.
//
// *esc carries the partial sequence between calls: byte count remaining in bits
// 31..24, accumulated codepoint in the low 24. Returns 1 and stores the
// codepoint in *ucs on the byte that completes a sequence, 0 otherwise. Only the
// low 16 bits of a 4-byte sequence survive, so this is a BMP decoder; nothing in
// Tulip draws astral-plane glyphs.
uint8_t convert_utf8_to_ucs(uint8_t c, uint32_t *esc, uint16_t *ucs)
{
   int shift;
   uint16_t acc;

   if (c < 0x80) {         // ASCII, and it ends any sequence in progress
       *esc = 0;
       *ucs = c;
       return 1;
   }

   switch (c & 0xf0) {
   case 0xc0: case 0xd0:       // two bytes sequence
       *esc = (1U << 24) | ((c & 0x1f) << 6);
       return 0;
   case 0xe0:          // three bytes sequence
       *esc = (2U << 24) | ((c & 0x0f) << 12);
       return 0;
   case 0xf0:          // four bytes sequence
       *esc = (3U << 24) | ((c & 0x07) << 18);
       return 0;
   case 0x80: case 0x90: case 0xa0: case 0xb0: // continuation
       shift = (*esc >> 24) - 1;
       // A continuation byte with no lead byte in front of it. There is nothing
       // to add it to, so drop it rather than shifting a negative count and
       // emitting a codepoint assembled out of nothing.
       if (shift < 0) {
           *esc = 0;
           return 0;
       }
       acc = *esc & 0xffffff;
       if (shift) {
           *esc = (shift << 24) | acc | (c & 0x3f) << (shift * 6);
           return 0;
       }
       *esc = 0;
       *ucs = acc | (c & 0x3f);
       return 1;
   }

   return 0;
}

// Encode one BMP codepoint as UTF-8. out must have room for 3 bytes plus the
// terminator. Returns the byte count written.
uint8_t convert_ucs_to_utf8(uint16_t ucs, char *out)
{
   if (ucs < 0x80) {
       out[0] = (char)ucs;
       out[1] = 0;
       return 1;
   }
   if (ucs < 0x800) {
       out[0] = (char)(0xc0 | (ucs >> 6));
       out[1] = (char)(0x80 | (ucs & 0x3f));
       out[2] = 0;
       return 2;
   }
   out[0] = (char)(0xe0 | (ucs >> 12));
   out[1] = (char)(0x80 | ((ucs >> 6) & 0x3f));
   out[2] = (char)(0x80 | (ucs & 0x3f));
   out[3] = 0;
   return 3;
}

uint8_t convert_utf8_to_cp437(uint8_t c, uint32_t *esc)
{
   uint16_t ucs;

   if (c < 127)            // ASCII
       return c;
   if (c == 127)
       return 8;       // DEL (?)

   if (!convert_utf8_to_ucs(c, esc, &ucs))
       return 0;
   return convert_uc16_to_cp437(ucs);
}


uint32_t keycode_to_ctrl_key(uint16_t key)
{
    switch(key) {
        case 261:
            return LV_KEY_RIGHT;
        case 260:
            return LV_KEY_LEFT;
        case 259:
            return LV_KEY_UP;
        case 258:
            return LV_KEY_DOWN;
        case 27:
            return LV_KEY_ESC;
        case 8:
            return LV_KEY_BACKSPACE;
        case 13:
            return LV_KEY_ENTER;
        case 9:
            return LV_KEY_NEXT;
        case 22:
            return LV_KEY_NEXT;
        case 25:
            return LV_KEY_PREV;
        default:
            return '\0';
    }
}

uint16_t scan_ascii(uint8_t code, uint32_t modifier) {
    last_held_code = code;
    last_held_modifier = (uint16_t)modifier;

    // Check for key remap
    for(uint8_t i=0;i<MAX_KEY_REMAPS;i++) {
        if(code == key_remaps[i].scan && modifier == key_remaps[i].mod) {
            //fprintf(stderr, "remap returning %d\n", key_remaps[i].code);
            return key_remaps[i].code;
        }
    }


#ifndef ESP_PLATFORM
    uint8_t shift = (modifier & KMOD_LSHIFT || modifier & KMOD_RSHIFT);
    uint8_t ctrl  = (modifier & KMOD_LCTRL || modifier & KMOD_RCTRL);
#else
    uint8_t shift = (modifier & KEY_MOD_LSHIFT || modifier & KEY_MOD_RSHIFT);
    uint8_t ctrl  = (modifier & KEY_MOD_LCTRL || modifier & KEY_MOD_RCTRL);
#endif    
    //uint8_t alt   = (modifier & KEY_MOD_LALT || modifier & KEY_MOD_RALT);
    //uint8_t meta  = (modifier & KEY_MOD_LMETA || modifier & KEY_MOD_RMETA);

    if(code >= 0x04 && code <= 0x1d) {
        // control-chars
        if(ctrl) return (code-0x04)+ 1;
        // capital letters
        if(shift) return (code-0x04)+'A';
        return code-0x04 + 'a';
    }
    if(code >= 0x1e && code <= 0x27) {
        if(shift) {
            switch(code) {
                case 0x1e: return '!'; 
                case 0x1f: return '@'; 
                case 0x20: return '#'; 
                case 0x21: return '$'; 
                case 0x22: return '%'; 
                case 0x23: return '^'; 
                case 0x24: return '&'; 
                case 0x25: return '*'; 
                case 0x26: return '('; 
                case 0x27: return ')'; 
            }
        } else {
            if(code == 0x27) return '0';
            return (code-0x1e) + '1';
        }
    }
    switch(code) {
        case KEY_ENTER: return 13;  
        case KEY_ESC: return 27; 
        case KEY_BACKSPACE: return 8; 
        case KEY_TAB: if(ctrl) return 263; else return 9; 
        case KEY_PAGEUP: return 25;
        case KEY_PAGEDOWN: return 22;

        case KEY_SPACE: return ' '; 
        case KEY_MINUS: if(shift) return '_'; else return '-';
        case KEY_EQUAL: if(shift) return '+'; else return '=';
        // Ctrl on these three produces a real ASCII control code, the same as
        // every terminal: 27, 28, 29. The block above only applies ctrl to the
        // letters, which is why Ctrl-\ -- documented as the IME toggle -- could
        // not be typed at all.
        case KEY_LEFTBRACE: if(ctrl) return 27; if(shift) return '{'; else return '[';
        case KEY_RIGHTBRACE: if(ctrl) return 29; if(shift) return '}'; else return ']';
        case KEY_BACKSLASH: if(ctrl) return 28; if(shift) return '|'; else return '\\';
        case KEY_HASHTILDE: if(shift) return '~'; else return '#';
        case KEY_SEMICOLON: if(shift) return ':'; else return ';';
        case KEY_APOSTROPHE: if(shift) return '\"'; else return '\'';
        case KEY_GRAVE: if(shift) return '~'; else return '`';
        case KEY_COMMA: if(shift) return '<'; else return ',';
        case KEY_DOT: if(shift) return '>'; else return '.';
        case KEY_SLASH: if(shift) return '?'; else return '/';

        // The JIS-only keys, which decoded to nothing at all before -- they are
        // absent from the US layout this table otherwise describes, so they fell
        // through to `return 0`.
        //
        // 変換 and かな are what a Japanese keyboard has where other layouts have
        // nothing, and switching input is exactly what they mean, so they are the
        // IME toggle without any configuration. 無変換 is left returning 0 for
        // tulip.key_remap() to claim.
        case KEY_HENKAN: return TULIP_IME_TOGGLE;
        case KEY_KATAKANAHIRAGANA: return TULIP_IME_TOGGLE;
        // ろ and ¥ are where a JIS keyboard keeps its backslash and underscore, and
        // a Python user needs both. This is what Windows and macOS do with them.
        // They take ctrl for the same reason KEY_BACKSLASH above does: on a JIS
        // keyboard these ARE the backslash key, so Ctrl-\ is only reachable here.
        case KEY_RO: if(ctrl) return 28; if(shift) return '_'; else return '\\';
        case KEY_YEN: if(ctrl) return 28; if(shift) return '|'; else return '\\';

        // We return extended codes for these, and get converted to ANSI for the repl down the line
        case KEY_UP: return 259; 
        case KEY_DOWN: return 258; 
        case KEY_LEFT: return 260; 
        case KEY_RIGHT: return 261;
        case KEY_DELETE: return 262; 

    }
    return 0;
}

// TAB5 compiles this file for the HID scan-code decoder (scan_ascii,
// keycode_to_ctrl_key) -- both its USB keyboard and its built-in I2C keyboard
// report HID scan codes -- but not for key delivery: it has its own path in
// modtulip_tab5.c (tab5_keyboard_deliver_key). The globals below live in
// shared/modtulip.c, which TAB5 does not build yet, so this half would not link
// there.
#ifndef TAB5

extern int16_t lvgl_is_repl;
extern mp_obj_t keyboard_callback, ui_quit_callback, ui_switch_callback;

// allow_ime is false for keys the IME itself is forwarding on to the app. Without
// it, tulip.key_send() would hand every forwarded key straight back to the IME.
extern mp_obj_t ime_callback;
static void deliver_key(uint16_t c, uint8_t allow_ime) {
    // The IME comes before everything except the interrupt char: a key it is
    // going to fold into a Japanese character must not also reach the REPL as a
    // Latin letter. Ctrl-C still gets through, so a wedged IME is escapable.
    if(allow_ime && c != mp_interrupt_char) {
        if(c == TULIP_IME_TOGGLE && ime_callback != NULL) {
            // Arm the flag so the frame ISR starts scheduling the drain, then let
            // the IME see the key and decide whether this turned it on or off --
            // it has a preedit line to clean up on the way out. Ignored entirely
            // when no IME is loaded, so this key cannot deafen the keyboard.
            ime_active = 1;
            ime_push_key(c);
            return;
        }
        if(ime_active) {
            ime_push_key(c);
            return;
        }
    }
    // handle the global system hotkeys before anything else. we have two, ctrl-tab and ctrl-q 
    if(c==17) {
        if(ui_quit_callback != NULL) 
            mp_sched_schedule(ui_quit_callback, NULL);
    } else if (c==263) {
        if(ui_switch_callback != NULL) 
            mp_sched_schedule(ui_switch_callback, NULL);
    } else {
        // Call the callback if set
        if(keyboard_callback != NULL)  {
            mp_sched_schedule(keyboard_callback, mp_obj_new_int(c));
        }

        // If something is taking in chars from LVGL (text area etc), don't send the char to MP
        if (c==mp_interrupt_char) {
            // Send a ctrl-C to Micropython if sent 
            mp_sched_keyboard_interrupt();
        } else if (c==4) { // control-D
            tx_char(c );
        } else {
            if(lvgl_is_repl) {
                if(c>257 && c<263) { 
                    tx_char(27);
                    tx_char('[');
                    if(c==258) tx_char('B');
                    if(c==259) tx_char('A');
                    if(c==260) tx_char('D');
                    if(c==261) tx_char('C');
                    if(c==262) { tx_char('3'); tx_char(126); }
                } else {
                    tx_char(c );
                }
            }
        }
    }
}

void send_key_to_micropython(uint16_t c) {
    deliver_key(c, 1);
}

// What tulip.key_send() calls: injection, and the path the IME forwards through.
void send_key_to_micropython_no_ime(uint16_t c) {
    deliver_key(c, 0);
}

#endif // !TAB5
