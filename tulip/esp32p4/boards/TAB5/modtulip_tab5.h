#ifndef __TAB5_MODTULIP_H__
#define __TAB5_MODTULIP_H__

#include <stdint.h>

// The C entry points modtulip_tab5.c publishes to the rest of the board.
// Everything else in that file is static or reached through the _tulip module.
//
// (tab5_keyboard_deliver_key() is declared in keyboard_tab5.h, next to the
// keyboard code that calls it, and tulip_amy_sequencer_hook() in
// tsequencer_tab5.h.)

// End-of-frame hook. Called from shared/display.c on the display task; pumps
// LVGL and any Python frame callback onto the MicroPython scheduler.
void tulip_frame_isr(void);

// Hands a touch up/down edge to the Python callback registered with
// tulip.touch_callback(). Called from shared_renderer_tab5_glue.c.
void tab5_schedule_touch_callback(uint8_t up);

// AMY's external MIDI input hook, registered in audio_tab5.c. Queues the message
// for tulip.midi_in()/tulip.sysex_in() and schedules tulip.midi_callback().
// Runs on whichever task delivered the MIDI (the USB host task today).
void tulip_amy_midi_hook(uint8_t *data, uint16_t len, uint8_t is_sysex);

// AMY's external file-I/O hooks, defined by shared/amy_file_hooks.inc where
// modtulip_tab5.c includes it and registered into amy_config in audio_tab5.c.
// They call into MicroPython's VFS, so they may only run on the MP thread.
uint32_t mp_fopen_hook(char *filename, const char *mode);
uint32_t mp_fwrite_hook(uint32_t fptr, uint8_t *bytes, uint32_t len);
uint32_t mp_fread_hook(uint32_t fptr, uint8_t *bytes, uint32_t len);
void mp_fseek_hook(uint32_t fptr, uint32_t pos);
void mp_fclose_hook(uint32_t fptr);

#endif
