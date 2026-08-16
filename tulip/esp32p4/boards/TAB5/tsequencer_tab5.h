#ifndef __TAB5_TSEQUENCER_H__
#define __TAB5_TSEQUENCER_H__

#include <stdint.h>
#include "py/obj.h"

// AMY calls this from tab5_audio_task (core 0) once per sequencer tick;
// audio_tab5.c installs it as amy_config.amy_external_sequencer_hook.
void tulip_amy_sequencer_hook(uint32_t tick_count);

// Bound into the _tulip module by modtulip_tab5.c.
extern const mp_obj_fun_builtin_fixed_t tulip_amy_sequencer_ticks_obj;
extern const mp_obj_fun_builtin_fixed_t tulip_seq_ticks_obj;
extern const mp_obj_fun_builtin_var_t tulip_seq_add_callback_obj;
extern const mp_obj_fun_builtin_fixed_t tulip_seq_remove_callback_obj;
extern const mp_obj_fun_builtin_fixed_t tulip_seq_remove_callbacks_obj;
extern const mp_obj_fun_builtin_fixed_t tulip_sequencer_start_obj;

#endif
