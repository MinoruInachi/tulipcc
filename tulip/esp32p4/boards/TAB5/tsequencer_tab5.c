// TAB5's replacement for tulip/shared/tsequencer.c.
//
// The shared file is not compiled on this board: it also carries the C defer
// queue, which TAB5 replaces with the Python-side one in tulip.py, and it is
// written against the ESP32-S3 port's task layout. What Python actually needs
// from it is the sequencer callback table -- what sequencer.py's TulipSequence
// is built on, and what drums.py uses to walk its beat LEDs -- so that is what
// lives here, bound into the _tulip module by modtulip_tab5.c.

#include "py/runtime.h"
#include "py/smallint.h"     // MP_SMALL_INT_MAX, for the hook's tick argument
#include "mphalport.h"

#include "../../../../amy/src/amy.h"
#include "../../../../amy/src/sequencer.h"

#include "tsequencer_tab5.h"

// AMY's sequencer tick counter, used to schedule notes at an absolute tick.
static mp_obj_t tulip_amy_sequencer_ticks(void) {
    return mp_obj_new_int_from_uint(sequencer_ticks());
}
MP_DEFINE_CONST_FUN_OBJ_0(tulip_amy_sequencer_ticks_obj, tulip_amy_sequencer_ticks);

// Same counter under the name Tulip's own code uses (sequencer.py, drums.py).
static mp_obj_t tulip_seq_ticks(void) {
    return mp_obj_new_int_from_uint(sequencer_ticks());
}
MP_DEFINE_CONST_FUN_OBJ_0(tulip_seq_ticks_obj, tulip_seq_ticks);

#define TAB5_SEQUENCER_SLOTS 8
MP_REGISTER_ROOT_POINTER(mp_obj_t tab5_seq_callbacks[8]);
#define s_tab5_seq_callbacks MP_STATE_PORT(tab5_seq_callbacks)
static volatile uint32_t s_tab5_seq_period[TAB5_SEQUENCER_SLOTS];
static volatile uint32_t s_tab5_seq_tick[TAB5_SEQUENCER_SLOTS];
// Non-zero only while at least one slot is armed. The hook runs on the audio
// task from the moment AMY starts, which is before mp_init() has zeroed the
// root pointers -- this counter is what keeps it from reading them until a
// callback has actually been registered from Python.
static volatile uint8_t s_tab5_seq_armed = 0;

// Called by AMY from tab5_audio_task (core 0) once per sequencer tick.
void tulip_amy_sequencer_hook(uint32_t tick_count) {
    if (!s_tab5_seq_armed) return;
    for (uint8_t i = 0; i < TAB5_SEQUENCER_SLOTS; i++) {
        uint32_t period = s_tab5_seq_period[i];
        if (period == 0 || (tick_count % period) != s_tab5_seq_tick[i]) continue;
        // Read the callback after the period test, and test it again: Python
        // may have cleared this slot in between, and scheduling the None it
        // leaves behind would raise on the MicroPython task instead.
        mp_obj_t cb = s_tab5_seq_callbacks[i];
        if (cb == MP_OBJ_NULL || cb == mp_const_none) continue;
        // MP_OBJ_NEW_SMALL_INT, not mp_obj_new_int: we are not on the
        // MicroPython task and must not allocate from its heap. Masking to the
        // small-int range costs a wrapped tick number after ~4000 hours of
        // uptime, which callers already treat modulo a bar anyway.
        if (mp_sched_schedule(cb, MP_OBJ_NEW_SMALL_INT(tick_count & MP_SMALL_INT_MAX))) {
            mp_hal_wake_main_task();
        }
    }
}

static mp_obj_t tulip_seq_add_callback(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    for (uint8_t slot = 0; slot < TAB5_SEQUENCER_SLOTS; slot++) {
        if (s_tab5_seq_callbacks[slot] == MP_OBJ_NULL || s_tab5_seq_callbacks[slot] == mp_const_none) {
            s_tab5_seq_callbacks[slot] = args[0];
            s_tab5_seq_tick[slot] = mp_obj_get_int(args[1]);
            // Period last: it is what the hook tests, so the slot is not live
            // until the callback and tick it needs are both in place.
            s_tab5_seq_period[slot] = mp_obj_get_int(args[2]);
            s_tab5_seq_armed = 1;
            return mp_obj_new_int(slot);
        }
    }
    return mp_obj_new_int(-1);
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_seq_add_callback_obj, 3, 3, tulip_seq_add_callback);

static void tab5_seq_clear_slot(uint8_t slot) {
    s_tab5_seq_period[slot] = 0;
    s_tab5_seq_tick[slot] = 0;
    s_tab5_seq_callbacks[slot] = mp_const_none;
}

static mp_obj_t tulip_seq_remove_callback(mp_obj_t tag_obj) {
    int tag = mp_obj_get_int(tag_obj);
    if (tag >= 0 && tag < TAB5_SEQUENCER_SLOTS) tab5_seq_clear_slot((uint8_t)tag);
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_1(tulip_seq_remove_callback_obj, tulip_seq_remove_callback);

static mp_obj_t tulip_seq_remove_callbacks(void) {
    for (uint8_t i = 0; i < TAB5_SEQUENCER_SLOTS; i++) tab5_seq_clear_slot(i);
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_0(tulip_seq_remove_callbacks_obj, tulip_seq_remove_callbacks);

// MIDI-style transport restart, so amyboard.py and sequencer.start() have the
// same entry point here as on the other boards.
static mp_obj_t tulip_sequencer_start(void) {
    sequencer_midi_start();
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_0(tulip_sequencer_start_obj, tulip_sequencer_start);
