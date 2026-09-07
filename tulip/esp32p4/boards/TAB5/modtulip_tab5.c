#include "py/runtime.h"
#include "py/nlr.h"
#include "py/objstr.h"
#include "mphalport.h"
#include "genhdr/mpversion.h"
#include <string.h>

#include "esp_timer.h"
#include "esp_system.h"
#include "esp_heap_caps.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "../../../../amy/src/amy.h"
// SYSEX_COPY_SLOTS / sysex_message_copies / sysex_copy_read_idx, for the
// deferred sysex dispatch below, plus midi_out() for its ACK.
#include "../../../../amy/src/amy_midi.h"

#include "../../../shared/display.h"
#include "../../../shared/bresenham.h"
#include "../../../shared/keyscan.h"
#include "../../../shared/jpfont.h"
#include "../../../shared/lodepng.h"
#include "../../../shared/tulip_helpers.h"
#include "display_tab5.h"
#include "audio_tab5.h"

#include "keyboard_tab5.h"
#include "touch_tab5.h"
#include "tab5_revision.h"
#include "modtulip_tab5.h"
#include "tsequencer_tab5.h"
#include "power_tab5.h"
#include "usb_host_tab5.h"
#include "camera_tab5.h"
#include "mic_tab5.h"
#include "imu_tab5.h"

extern int16_t lvgl_is_repl;

static bool s_tab5_lvgl_initialized = false;
static bool s_tab5_lvgl_running = false;
static volatile uint32_t s_tab5_frame_callbacks = 0;
static volatile uint32_t s_tab5_lvgl_handlers = 0;

MP_REGISTER_ROOT_POINTER(mp_obj_t tab5_process_defers_cb);
MP_REGISTER_ROOT_POINTER(mp_obj_t tab5_frame_cb);
MP_REGISTER_ROOT_POINTER(mp_obj_t tab5_frame_arg);
MP_REGISTER_ROOT_POINTER(mp_obj_t tab5_touch_cb);
MP_REGISTER_ROOT_POINTER(mp_obj_t tab5_midi_cb);
MP_REGISTER_ROOT_POINTER(mp_obj_t tab5_ime_cb);
MP_REGISTER_ROOT_POINTER(mp_obj_t tab5_keyboard_cb);
MP_REGISTER_ROOT_POINTER(mp_obj_t tab5_amy_overload_cb);

#define s_tab5_process_defers_cb MP_STATE_PORT(tab5_process_defers_cb)
#define s_tab5_frame_cb MP_STATE_PORT(tab5_frame_cb)
#define s_tab5_frame_arg MP_STATE_PORT(tab5_frame_arg)
#define s_tab5_touch_cb MP_STATE_PORT(tab5_touch_cb)
#define s_tab5_midi_cb MP_STATE_PORT(tab5_midi_cb)
#define s_tab5_ime_cb MP_STATE_PORT(tab5_ime_cb)
#define _tab5_keyboard_cb MP_STATE_PORT(tab5_keyboard_cb)
#define s_tab5_amy_overload_cb MP_STATE_PORT(tab5_amy_overload_cb)

static void tab5_process_python_defers(void) {
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        if (s_tab5_process_defers_cb == MP_OBJ_NULL) {
            mp_obj_t module_name = mp_obj_new_str("tulip", 5);
            mp_obj_t callback_name = mp_obj_new_str("_process_defers", 15);
            qstr q_module = mp_obj_str_get_qstr(module_name);
            qstr q_callback = mp_obj_str_get_qstr(callback_name);
            mp_obj_t module = mp_import_name(q_module, mp_const_none, MP_OBJ_NEW_SMALL_INT(0));
            s_tab5_process_defers_cb = mp_load_attr(module, q_callback);
        }
        mp_call_function_0(s_tab5_process_defers_cb);
        nlr_pop();
    } else {
        s_tab5_process_defers_cb = MP_OBJ_NULL;
        mp_obj_print_exception(&mp_plat_print, MP_OBJ_FROM_PTR(nlr.ret_val));
    }
}

static mp_obj_t tab5_lv_task_handler(mp_obj_t ignored) {
    (void)ignored;
    s_tab5_lvgl_handlers++;
    lv_task_handler();
    tab5_process_python_defers();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(tab5_lv_task_handler_obj, tab5_lv_task_handler);

void tulip_frame_isr(void) {
    s_tab5_frame_callbacks++;
    if (s_tab5_lvgl_running &&
        mp_sched_schedule(MP_OBJ_FROM_PTR(&tab5_lv_task_handler_obj), mp_const_none)) {
        mp_hal_wake_main_task();
    }
    if (s_tab5_frame_cb != MP_OBJ_NULL && s_tab5_frame_cb != mp_const_none &&
        mp_sched_schedule(s_tab5_frame_cb, s_tab5_frame_arg)) {
        mp_hal_wake_main_task();
    }
    // The IME drains its key queue from here rather than being called once per
    // key: mp_sched_schedule() fails silently when its queue is full, and this way
    // that costs a frame of latency instead of a keystroke. It has its own slot
    // because tulip.frame_callback() is a single slot that belongs to the app.
    if (ime_active && s_tab5_ime_cb != MP_OBJ_NULL && s_tab5_ime_cb != mp_const_none &&
        mp_sched_schedule(s_tab5_ime_cb, mp_const_none)) {
        mp_hal_wake_main_task();
    }
}

void tab5_schedule_touch_callback(uint8_t up) {
    if (s_tab5_touch_cb != MP_OBJ_NULL && s_tab5_touch_cb != mp_const_none &&
        mp_sched_schedule(s_tab5_touch_cb, mp_obj_new_int(up))) {
        mp_hal_wake_main_task();
    }
}

static mp_obj_t tulip_board(void) {
    return mp_obj_new_str("TAB5", 4);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_board_obj, tulip_board);

static mp_obj_t tulip_ticks_ms(void) {
    return mp_obj_new_int_from_ull((uint64_t)esp_timer_get_time() / 1000ULL);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_ticks_ms_obj, tulip_ticks_ms);

static mp_obj_t tulip_amy_ticks_ms(void) {
    return mp_obj_new_int_from_uint(amy_sysclock());
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_amy_ticks_ms_obj, tulip_amy_ticks_ms);

// The AMY surface the `amy` Python package binds to on MicroPython. amy's
// _capi_resolve() looks each one up as tulip.amy_<name> independently and
// substitutes a stub that raises NotImplementedError for the names a board does
// not have, so an unbound function costs only itself. The other Tulip targets
// take these from amy's generated amy_c_api_mp.inc (see shared/modtulip.c); TAB5
// writes its own so that everything reaching into AMY can check first that AMY
// is there -- see tab5_amy_require_audio().
static mp_obj_t tulip_amy_get_synth_commands(size_t n_args, const mp_obj_t *args) {
    char cmd[MAX_MESSAGE_LEN];
    void *state = NULL;
    int synth = mp_obj_get_int(args[0]);
    bool include_fx = true;
    if (n_args > 1) include_fx = mp_obj_get_int(args[1]);
    mp_obj_t list = mp_obj_new_list(0, NULL);
    do {
        state = yield_synth_commands(synth, cmd, MAX_MESSAGE_LEN, include_fx, state);
        int len = strlen(cmd);
        if (len) mp_obj_list_append(list, mp_obj_new_str(cmd, len));
    } while (state != NULL);
    return list;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_amy_get_synth_commands_obj, 1, 2, tulip_amy_get_synth_commands);

static mp_obj_t tulip_amy_render_load(void) {
    return mp_obj_new_float(amy_get_render_load());
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_amy_render_load_obj, tulip_amy_render_load);

static mp_obj_t tulip_amy_set_render_load_threshold(mp_obj_t threshold_obj) {
    amy_set_render_load_threshold(mp_obj_get_float(threshold_obj));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(tulip_amy_set_render_load_threshold_obj, tulip_amy_set_render_load_threshold);

// AMY exists only once amy_start() has run, which is what tab5_audio_ready()
// reports; the board reaches Python with it false if the codec, I2S or the
// render task failed to come up. Reading uninitialised AMY state is merely
// meaningless, but amy_get_input_buffer() walks a block amy_start() mallocs, so
// there it is a null dereference. Raise rather than crash.
static void tab5_amy_require_audio(void) {
    if (!tab5_audio_ready()) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("AMY audio is not ready"));
    }
}

static mp_obj_t tulip_amy_send(mp_obj_t message_obj) {
    tab5_amy_require_audio();
    amy_add_message((char *)mp_obj_str_get_str(message_obj));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(tulip_amy_send_obj, tulip_amy_send);

// A wire message that arrived over sysex: the file-transfer routing in
// transfer.c applies to it, which is how amy.send_wire_from_sysex() differs
// from amy.send().
static mp_obj_t tulip_amy_send_wire_from_sysex(mp_obj_t message_obj) {
    tab5_amy_require_audio();
    amy_send_wire_from_sysex((char *)mp_obj_str_get_str(message_obj));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(tulip_amy_send_wire_from_sysex_obj, tulip_amy_send_wire_from_sysex);

// The startup chime. `start` is the tick to play it at; 0 means now.
static mp_obj_t tulip_amy_bleep(size_t n_args, const mp_obj_t *args) {
    tab5_amy_require_audio();
    amy_bleep(n_args > 0 ? (uint32_t)mp_obj_get_int(args[0]) : (uint32_t)0);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_amy_bleep_obj, 0, 1, tulip_amy_bleep);

// Feed one byte to AMY's MIDI stream parser. The Tab5's own USB-A MIDI already
// goes straight into AMY (tulip_amy_midi_hook); this is for Python-side sources.
static mp_obj_t tulip_amy_process_single_midi_byte(size_t n_args, const mp_obj_t *args) {
    tab5_amy_require_audio();
    uint8_t byte = (uint8_t)mp_obj_get_int(args[0]);
    uint8_t from_web_or_usb = (n_args > 1) ? (uint8_t)mp_obj_get_int(args[1]) : (uint8_t)1;
    amy_process_single_midi_byte(byte, from_web_or_usb);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_amy_process_single_midi_byte_obj, 1, 2, tulip_amy_process_single_midi_byte);

// Drive a CV channel from a mod oscillator. The Tab5 has no CV jacks, but AMY's
// test suite and any patch built on an AMYboard sketch expect the call to work.
static mp_obj_t tulip_amy_set_cv_from_osc(mp_obj_t cv_channel_obj, mp_obj_t osc_obj) {
    tab5_amy_require_audio();
    set_cv_from_osc(mp_obj_get_int(cv_channel_obj), mp_obj_get_int(osc_obj));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(tulip_amy_set_cv_from_osc_obj, tulip_amy_set_cv_from_osc);

// AMY's whole state as a replayable wire string. amy_dump_state_to_string()
// mallocs, so the copy into a Python str has to be followed by a free.
static mp_obj_t tulip_amy_dump_state(void) {
    tab5_amy_require_audio();
    int len = 0;
    char *dump = amy_dump_state_to_string(&len);
    if (dump == NULL) mp_raise_msg(&mp_type_MemoryError, NULL);
    mp_obj_t result = mp_obj_new_str(dump, len);
    free(dump);
    return result;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_amy_dump_state_obj, tulip_amy_dump_state);

// One block of interleaved stereo samples, as bytes. Sized from AMY's own
// constants rather than a literal: the generated wrappers assume a 256-frame
// stereo int16 block fits in 1KB, and it does exactly.
#define TAB5_AMY_BLOCK_SAMPLES (AMY_BLOCK_SIZE * AMY_NCHANS)

static mp_obj_t tulip_amy_get_output_buffer(void) {
    tab5_amy_require_audio();
    output_sample_type samples[TAB5_AMY_BLOCK_SAMPLES];
    int n = amy_get_output_buffer(samples);
    // 0 means amy_fill_buffer() has not run yet, so there is no block to read.
    if (n == 0) return mp_const_none;
    return mp_obj_new_bytes((const uint8_t *)samples, n);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_amy_get_output_buffer_obj, tulip_amy_get_output_buffer);

// The audio-in block. The Tab5 renders with AMY_AUDIO_IS_NONE and feeds the
// codec itself, so nothing fills this yet and it reads as silence -- it is bound
// so that code written against the AMYboard runs here rather than raising.
static mp_obj_t tulip_amy_get_input_buffer(void) {
    tab5_amy_require_audio();
    output_sample_type samples[TAB5_AMY_BLOCK_SAMPLES];
    int n = amy_get_input_buffer(samples);
    if (n == 0) return mp_const_none;
    return mp_obj_new_bytes((const uint8_t *)samples, n);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_amy_get_input_buffer_obj, tulip_amy_get_input_buffer);

static mp_obj_t tulip_midi_callback(size_t n_args, const mp_obj_t *args) {
    s_tab5_midi_cb = n_args == 0 ? mp_const_none : args[0];
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_midi_callback_obj, 0, 1, tulip_midi_callback);

/*
 * MIDI in, from AMY to Python.
 *
 * The other targets get this from shared/amy_connector.c, which TAB5 does not
 * build (it is written around the ESP32-S3's MIDI UART pins). The queue below is
 * the same shape and depth, so midi.py behaves identically: tulip_amy_midi_hook()
 * is registered as AMY's external MIDI input hook, drops each message into the
 * ring, and schedules the Python callback midi.py installed with
 * tulip.midi_callback(). Sysex takes the separate buffer, and the callback's
 * argument says which of the two to read -- True for sysex_in(), False for
 * midi_in(), exactly as amy_connector.c does it.
 *
 * The hook runs on the USB host task, so it may only touch the queue and call
 * mp_sched_schedule(); everything else waits for the scheduled callback.
 */
#define TAB5_MIDI_QUEUE_DEPTH 1024
#define TAB5_MAX_MIDI_BYTES_PER_MESSAGE 3

static uint8_t s_last_midi[TAB5_MIDI_QUEUE_DEPTH][TAB5_MAX_MIDI_BYTES_PER_MESSAGE];
static uint8_t s_last_midi_len[TAB5_MIDI_QUEUE_DEPTH];
static volatile int16_t s_midi_queue_head = 0;
static volatile int16_t s_midi_queue_tail = 0;

static uint8_t s_sysex_in[MAX_MESSAGE_LEN + 2];
static volatile uint16_t s_sysex_in_len = 0;

// midi_msg_handler() is AMY's CC-mapping dispatcher (src/midi_mappings.c).
extern void midi_msg_handler(uint8_t *bytes, uint16_t len, uint8_t is_sysex, uint32_t time);

void tulip_amy_midi_hook(uint8_t *data, uint16_t len, uint8_t is_sysex) {
    uint32_t time;
    AMY_UNSET(time);
    midi_msg_handler(data, len, is_sysex, time);

    if (is_sysex) {
        // Some transports strip the F0/F7 wrapper; put it back so Python always
        // sees a complete sysex message.
        uint16_t c = 0;
        if (len > 0 && data[0] != 0xf0 && c < sizeof(s_sysex_in)) {
            s_sysex_in[c++] = 0xf0;
        }
        for (uint16_t i = 0; i < len && c < sizeof(s_sysex_in); i++) {
            s_sysex_in[c++] = data[i];
        }
        if (c > 0 && s_sysex_in[c - 1] != 0xf7 && c < sizeof(s_sysex_in)) {
            s_sysex_in[c++] = 0xf7;
        }
        s_sysex_in_len = c;
        if (s_tab5_midi_cb != MP_OBJ_NULL && s_tab5_midi_cb != mp_const_none) {
            mp_sched_schedule(s_tab5_midi_cb, mp_const_true);
        }
        return;
    }

    const int16_t tail = s_midi_queue_tail;
    for (uint16_t i = 0; i < len && i < TAB5_MAX_MIDI_BYTES_PER_MESSAGE; i++) {
        s_last_midi[tail][i] = data[i];
    }
    s_last_midi_len[tail] = (len > TAB5_MAX_MIDI_BYTES_PER_MESSAGE)
        ? TAB5_MAX_MIDI_BYTES_PER_MESSAGE : (uint8_t)len;
    s_midi_queue_tail = (int16_t)((tail + 1) % TAB5_MIDI_QUEUE_DEPTH);
    if (s_midi_queue_tail == s_midi_queue_head) {
        // Wrapped: drop the oldest rather than the newest.
        s_midi_queue_head = (int16_t)((s_midi_queue_head + 1) % TAB5_MIDI_QUEUE_DEPTH);
    }

    if (s_tab5_midi_cb != MP_OBJ_NULL && s_tab5_midi_cb != mp_const_none) {
        mp_sched_schedule(s_tab5_midi_cb, mp_const_false);
    }
}

static mp_obj_t tulip_midi_in(void) {
    if (s_midi_queue_head == s_midi_queue_tail) {
        return mp_const_none;
    }
    const int16_t prev_head = s_midi_queue_head;
    s_midi_queue_head = (int16_t)((s_midi_queue_head + 1) % TAB5_MIDI_QUEUE_DEPTH);
    return mp_obj_new_bytes(s_last_midi[prev_head], s_last_midi_len[prev_head]);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_midi_in_obj, tulip_midi_in);

static mp_obj_t tulip_sysex_in(void) {
    if (s_sysex_in_len == 0) {
        return mp_const_none;
    }
    mp_obj_t bytes = mp_obj_new_bytes(s_sysex_in, s_sysex_in_len);
    s_sysex_in_len = 0;
    return bytes;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_sysex_in_obj, tulip_sysex_in);

static mp_obj_t tulip_midi_out(mp_obj_t data_obj) {
    mp_buffer_info_t bufinfo;
    if (mp_get_buffer(data_obj, &bufinfo, MP_BUFFER_READ)) {
        send_usb_midi_out((uint8_t *)bufinfo.buf, (uint16_t)bufinfo.len);
        return mp_const_none;
    }

    size_t len;
    mp_obj_t *items;
    mp_obj_get_array(data_obj, &len, &items);
    if (len == 0) {
        return mp_const_none;
    }
    uint8_t *bytes = m_new(uint8_t, len);
    for (size_t i = 0; i < len; i++) {
        bytes[i] = (uint8_t)mp_obj_get_int(items[i]);
    }
    send_usb_midi_out(bytes, (uint16_t)len);
    m_del(uint8_t, bytes, len);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(tulip_midi_out_obj, tulip_midi_out);

// Feed bytes to AMY as if they had arrived over MIDI, without echoing them back
// out the USB port. midi.py uses this for its own note generation.
static mp_obj_t tulip_midi_local(mp_obj_t data_obj) {
    mp_buffer_info_t bufinfo;
    if (mp_get_buffer(data_obj, &bufinfo, MP_BUFFER_READ)) {
        convert_midi_bytes_to_messages((uint8_t *)bufinfo.buf, bufinfo.len, 0);
        return mp_const_none;
    }

    size_t len;
    mp_obj_t *items;
    mp_obj_get_array(data_obj, &len, &items);
    if (len == 0) {
        return mp_const_none;
    }
    uint8_t *bytes = m_new(uint8_t, len);
    for (size_t i = 0; i < len; i++) {
        bytes[i] = (uint8_t)mp_obj_get_int(items[i]);
    }
    convert_midi_bytes_to_messages(bytes, len, 0);
    m_del(uint8_t, bytes, len);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(tulip_midi_local_obj, tulip_midi_local);

/*
 * Drain one sysex message on the MicroPython thread.
 *
 * AMY's parse_sysex() runs on whatever task the MIDI arrived on -- the USB host
 * task here -- so it copies the payload into a ring slot and schedules this
 * instead of dispatching inline. That matters because a wire command can reach
 * the file hooks (zL, zT), and those call mp_vfs_open() and allocate on the MP
 * heap; neither is safe off this thread. amy_midi.h gives TAB5 the slots, and
 * amy_midi.c's deferred branch names TAB5 alongside TULIP and AMYBOARD.
 *
 * Not static: amy_midi.c declares `extern ... tulip_amy_send_sysex_obj` and
 * schedules it by address.
 */
static mp_obj_t tulip_amy_send_sysex(size_t n_args, const mp_obj_t *args) {
    (void)n_args; (void)args;
#if SYSEX_COPY_SLOTS > 0
    char *slot = sysex_message_copies[sysex_copy_read_idx];
    sysex_copy_read_idx = (sysex_copy_read_idx + 1) % SYSEX_COPY_SLOTS;
#else
    char *slot = NULL;
#endif
    if (slot) {
        // _from_sysex, not amy_add_message: during a file transfer this routes
        // the payload to parse_transfer_message() instead of being read as a
        // wire command. amy.send() from Python keeps the direct path.
        amy_send_wire_from_sysex(slot);
    }
    // ACK only after the slot is drained, so a flow-controlled sender never runs
    // more than the ring depth ahead. Reaches the USB port through the external
    // MIDI output hook audio_tab5.c installs -- amy_config.midi is
    // AMY_MIDI_IS_NONE here, so AMY's own midi_out() has no device to write to.
    {
        uint8_t ack[] = { 0xF0, 0x00, 0x03, 0x45, 'A', 'K', 0xF7 };
        midi_out(ack, sizeof(ack));
    }
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_amy_send_sysex_obj, 0, 1, tulip_amy_send_sysex);

static mp_obj_t tulip_build_strings(void) {
    mp_obj_t tuple[] = {
        mp_obj_new_str(MICROPY_GIT_TAG, strlen(MICROPY_GIT_TAG)),
        mp_obj_new_str(MICROPY_GIT_HASH, strlen(MICROPY_GIT_HASH)),
        mp_obj_new_str(MICROPY_BUILD_DATE, strlen(MICROPY_BUILD_DATE)),
    };
    return mp_obj_new_tuple(MP_ARRAY_SIZE(tuple), tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_build_strings_obj, tulip_build_strings);

static mp_obj_t tulip_screen_size(void) {
    mp_obj_t tuple[2];
    tuple[0] = mp_obj_new_int(H_RES);
    tuple[1] = mp_obj_new_int(V_RES);
    return mp_obj_new_tuple(2, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_screen_size_obj, tulip_screen_size);

static void tab5_require_bg(void) {
    if (bg == NULL) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("display is not ready"));
    }
}

static void tab5_require_xy(int x, int y) {
    if (x < 0 || x >= H_RES + OFFSCREEN_X_PX || y < 0 || y >= V_RES + OFFSCREEN_Y_PX) {
        mp_raise_ValueError(MP_ERROR_TEXT("coordinates out of range"));
    }
}

// A colour argument to anything that draws. The BG plane on this board is
// RGB565, so besides the 0-255 palette index every Tulip takes -- expanded to
// the pixel the palette entry names, the same one LVGL paints for it -- an
// (r, g, b) tuple or list of 0-255 picks the colour directly. An int outside
// 0-255 is refused rather than read as a raw pixel: the two ranges overlap, and
// a value that silently meant a palette index on one board and a dark blue on
// another is exactly the bug this avoids.
static tulip_px_t tab5_color_arg(mp_obj_t obj) {
    if (mp_obj_is_int(obj)) {
        mp_int_t v = mp_obj_get_int(obj);
        if (v < 0 || v > 255) {
            mp_raise_ValueError(MP_ERROR_TEXT("color must be a palette index 0-255 or an (r, g, b) tuple"));
        }
        return PX((uint8_t)v);
    }
    if (mp_obj_is_type(obj, &mp_type_tuple) || mp_obj_is_type(obj, &mp_type_list)) {
        size_t len;
        mp_obj_t *items;
        mp_obj_get_array(obj, &len, &items);
        if (len == 3) {
            mp_int_t r = mp_obj_get_int(items[0]);
            mp_int_t g = mp_obj_get_int(items[1]);
            mp_int_t b = mp_obj_get_int(items[2]);
            if (r < 0 || r > 255 || g < 0 || g > 255 || b < 0 || b > 255) {
                mp_raise_ValueError(MP_ERROR_TEXT("r, g, b must each be 0-255"));
            }
            return px_from_rgb((uint8_t)r, (uint8_t)g, (uint8_t)b);
        }
    }
    mp_raise_TypeError(MP_ERROR_TEXT("color must be a palette index 0-255 or an (r, g, b) tuple"));
}

static mp_obj_t tulip_bg_pixel(size_t n_args, const mp_obj_t *args) {
    tab5_require_bg();
    int x = mp_obj_get_int(args[0]);
    int y = mp_obj_get_int(args[1]);
    tab5_require_xy(x, y);
    if (n_args == 3) {
        display_set_bg_pixel_px((uint16_t)x, (uint16_t)y, tab5_color_arg(args[2]));
        return mp_const_none;
    }
    // The nearest palette entry, as on every Tulip; bg_pixel_rgb() reads the
    // pixel exactly.
    return mp_obj_new_int(display_get_bg_pixel_pal((uint16_t)x, (uint16_t)y));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_bg_pixel_obj, 2, 3, tulip_bg_pixel);

// (r, g, b) = tulip.bg_pixel_rgb(x, y): the pixel as the panel shows it,
// 5-6-5 widened to 8 bits. The only exact reading of a pixel set from a tuple.
static mp_obj_t tulip_bg_pixel_rgb(mp_obj_t x_obj, mp_obj_t y_obj) {
    tab5_require_bg();
    int x = mp_obj_get_int(x_obj);
    int y = mp_obj_get_int(y_obj);
    tab5_require_xy(x, y);
    uint8_t r, g, b;
    display_get_bg_pixel((uint16_t)x, (uint16_t)y, &r, &g, &b);
    mp_obj_t tuple[] = { mp_obj_new_int(r), mp_obj_new_int(g), mp_obj_new_int(b) };
    return mp_obj_new_tuple(3, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_2(tulip_bg_pixel_rgb_obj, tulip_bg_pixel_rgb);

static mp_obj_t tulip_bg_clear(size_t n_args, const mp_obj_t *args) {
    tab5_require_bg();
    tulip_px_t color = n_args == 0 ? PX(bg_pal_color) : tab5_color_arg(args[0]);
    const size_t count = (size_t)(H_RES + OFFSCREEN_X_PX) * (V_RES + OFFSCREEN_Y_PX);
    for (size_t i = 0; i < count; i++) bg[i] = color;
    display_mark_dirty();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_bg_clear_obj, 0, 1, tulip_bg_clear);

static mp_obj_t tulip_bg_bitmap(size_t n_args, const mp_obj_t *args) {
    tab5_require_bg();
    int x = mp_obj_get_int(args[0]);
    int y = mp_obj_get_int(args[1]);
    int w = mp_obj_get_int(args[2]);
    int h = mp_obj_get_int(args[3]);
    if (w <= 0 || h <= 0 || x < 0 || y < 0 || x + w > H_RES + OFFSCREEN_X_PX ||
        y + h > V_RES + OFFSCREEN_Y_PX) {
        mp_raise_ValueError(MP_ERROR_TEXT("bitmap rectangle out of range"));
    }
    // Native pixels: w*h*BYTES_PER_PIXEL bytes, RGB565 little-endian here.
    size_t length = (size_t)w * (size_t)h * BYTES_PER_PIXEL;
    if (n_args == 5) {
        mp_buffer_info_t buffer;
        mp_get_buffer_raise(args[4], &buffer, MP_BUFFER_READ);
        if (buffer.len != length) {
            mp_raise_ValueError(MP_ERROR_TEXT("bitmap length does not match rectangle"));
        }
        display_set_bg_bitmap_raw(x, y, w, h, buffer.buf);
        return mp_const_none;
    }
    vstr_t result;
    vstr_init_len(&result, length);
    display_get_bg_bitmap_raw(x, y, w, h, (uint8_t *)result.buf);
    return mp_obj_new_bytes_from_vstr(&result);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_bg_bitmap_obj, 4, 5, tulip_bg_bitmap);

static mp_obj_t tulip_bg_blit(size_t n_args, const mp_obj_t *args) {
    uint16_t x = mp_obj_get_int(args[0]);
    uint16_t y = mp_obj_get_int(args[1]);
    uint16_t w = mp_obj_get_int(args[2]);
    uint16_t h = mp_obj_get_int(args[3]);
    uint16_t x1 = mp_obj_get_int(args[4]);
    uint16_t y1 = mp_obj_get_int(args[5]);
    if (n_args == 7) {
        display_bg_bitmap_blit_alpha(x, y, w, h, x1, y1);
    } else {
        display_bg_bitmap_blit(x, y, w, h, x1, y1);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_bg_blit_obj, 6, 7, tulip_bg_blit);

static mp_obj_t tulip_bg_png(size_t n_args, const mp_obj_t *args) {
    int x = mp_obj_get_int(args[1]);
    int y = mp_obj_get_int(args[2]);
    if (x < 0 || y < 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("PNG position out of range"));
    }

    mp_buffer_info_t png = {0};
    bool free_png = false;
    if (mp_obj_is_str(args[0])) {
        const char *filename = mp_obj_str_get_str(args[0]);
        int32_t size = file_size(filename);
        if (size < 0) {
            mp_raise_OSError(MP_ENOENT);
        }
        if (size == 0) {
            mp_raise_ValueError(MP_ERROR_TEXT("PNG file is empty"));
        }
        png.buf = malloc_caps((size_t)size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (png.buf == NULL) {
            mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("unable to allocate PNG input"));
        }
        png.len = (size_t)size;
        free_png = true;
        if (read_file(filename, png.buf, size, 1) != (uint32_t)size) {
            free_caps(png.buf);
            mp_raise_OSError(MP_EIO);
        }
    } else {
        mp_get_buffer_raise(args[0], &png, MP_BUFFER_READ);
        if (png.len == 0) {
            mp_raise_ValueError(MP_ERROR_TEXT("PNG data is empty"));
        }
    }

    unsigned char *image = NULL;
    unsigned width = 0;
    unsigned height = 0;
    unsigned error = lodepng_decode32(&image, &width, &height, png.buf, png.len);
    if (free_png) {
        free_caps(png.buf);
    }
    if (error != 0) {
        free_caps(image);
        mp_raise_ValueError(MP_ERROR_TEXT("invalid PNG data"));
    }
    if (width == 0 || height == 0 || width > UINT16_MAX || height > UINT16_MAX) {
        free_caps(image);
        mp_raise_ValueError(MP_ERROR_TEXT("invalid PNG dimensions"));
    }

    display_set_bg_bitmap_rgba(x, y, width, height, image);
    free_caps(image);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_bg_png_obj, 3, 3, tulip_bg_png);

static mp_obj_t tulip_bg_bezier(size_t n_args, const mp_obj_t *args) {
    plotQuadBezier(mp_obj_get_int(args[0]), mp_obj_get_int(args[1]),
                   mp_obj_get_int(args[2]), mp_obj_get_int(args[3]),
                   mp_obj_get_int(args[4]), mp_obj_get_int(args[5]),
                   tab5_color_arg(args[6]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_bg_bezier_obj, 7, 7, tulip_bg_bezier);

static mp_obj_t tulip_bg_line(size_t n_args, const mp_obj_t *args) {
    uint16_t width = n_args == 6 ? mp_obj_get_int(args[5]) : 1;
    drawLine_scanline(mp_obj_get_int(args[0]), mp_obj_get_int(args[1]),
                      mp_obj_get_int(args[2]), mp_obj_get_int(args[3]),
                      tab5_color_arg(args[4]), width);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_bg_line_obj, 5, 6, tulip_bg_line);

static mp_obj_t tulip_bg_roundrect(size_t n_args, const mp_obj_t *args) {
    int16_t x = mp_obj_get_int(args[0]);
    int16_t y = mp_obj_get_int(args[1]);
    int16_t w = mp_obj_get_int(args[2]);
    int16_t h = mp_obj_get_int(args[3]);
    int16_t radius = mp_obj_get_int(args[4]);
    tulip_px_t color = tab5_color_arg(args[5]);
    if (n_args == 7 && mp_obj_is_true(args[6])) fillRoundRect(x, y, w, h, radius, color);
    else drawRoundRect(x, y, w, h, radius, color);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_bg_roundrect_obj, 6, 7, tulip_bg_roundrect);

static mp_obj_t tulip_bg_rect(size_t n_args, const mp_obj_t *args) {
    int16_t x = mp_obj_get_int(args[0]);
    int16_t y = mp_obj_get_int(args[1]);
    int16_t w = mp_obj_get_int(args[2]);
    int16_t h = mp_obj_get_int(args[3]);
    tulip_px_t color = tab5_color_arg(args[4]);
    if (n_args == 6 && mp_obj_is_true(args[5])) fillRect(x, y, w, h, color);
    else drawRect(x, y, w, h, color);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_bg_rect_obj, 5, 6, tulip_bg_rect);

static mp_obj_t tulip_bg_circle(size_t n_args, const mp_obj_t *args) {
    int16_t x = mp_obj_get_int(args[0]);
    int16_t y = mp_obj_get_int(args[1]);
    int16_t radius = mp_obj_get_int(args[2]);
    tulip_px_t color = tab5_color_arg(args[3]);
    if (n_args == 5 && mp_obj_is_true(args[4])) fillCircle(x, y, radius, color);
    else drawCircle(x, y, radius, color);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_bg_circle_obj, 4, 5, tulip_bg_circle);

static mp_obj_t tulip_bg_triangle(size_t n_args, const mp_obj_t *args) {
    int16_t x0 = mp_obj_get_int(args[0]);
    int16_t y0 = mp_obj_get_int(args[1]);
    int16_t x1 = mp_obj_get_int(args[2]);
    int16_t y1 = mp_obj_get_int(args[3]);
    int16_t x2 = mp_obj_get_int(args[4]);
    int16_t y2 = mp_obj_get_int(args[5]);
    tulip_px_t color = tab5_color_arg(args[6]);
    if (n_args == 8 && mp_obj_is_true(args[7])) fillTriangle(x0, y0, x1, y1, x2, y2, color);
    else drawTriangle(x0, y0, x1, y1, x2, y2, color);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_bg_triangle_obj, 7, 8, tulip_bg_triangle);

static mp_obj_t tulip_bg_fill(mp_obj_t x, mp_obj_t y, mp_obj_t color) {
    fill(mp_obj_get_int(x), mp_obj_get_int(y), tab5_color_arg(color));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(tulip_bg_fill_obj, tulip_bg_fill);

static mp_obj_t tulip_bg_str(size_t n_args, const mp_obj_t *args) {
    const char *text = mp_obj_str_get_str(args[0]);
    uint16_t x = mp_obj_get_int(args[1]);
    uint16_t y = mp_obj_get_int(args[2]);
    tulip_px_t color = tab5_color_arg(args[3]);
    uint8_t font = mp_obj_get_int(args[4]);
    if (n_args == 7) {
        return mp_obj_new_int(draw_new_str(text, x, y, color, font,
                                           mp_obj_get_int(args[5]), mp_obj_get_int(args[6]), 1));
    }
    return mp_obj_new_int(draw_new_str(text, x, y, color, font, 0, 0, 0));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_bg_str_obj, 5, 7, tulip_bg_str);

static void tab5_require_scroll_ready(void) {
    if (x_offsets == NULL || y_offsets == NULL || x_speeds == NULL || y_speeds == NULL) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("background scroll is not ready"));
    }
}

static size_t tab5_scroll_line(mp_obj_t line_obj) {
    int line = mp_obj_get_int(line_obj);
    if (line < 0 || line >= V_RES) {
        mp_raise_ValueError(MP_ERROR_TEXT("background scroll line out of range"));
    }
    return (size_t)line;
}

static int16_t tab5_scroll_offset(mp_obj_t offset_obj, int extent) {
    int offset = mp_obj_get_int(offset_obj) % extent;
    return offset < 0 ? offset + extent : offset;
}

static mp_obj_t tulip_bg_scroll(size_t n_args, const mp_obj_t *args) {
    tab5_require_scroll_ready();
    if (n_args == 0) {
        for (size_t line = 0; line < V_RES; line++) {
            x_offsets[line] = 0;
            y_offsets[line] = line;
            x_speeds[line] = 0;
            y_speeds[line] = 0;
        }
        display_rows_trackable = 1;
        display_mark_dirty();
        return mp_const_none;
    }
    size_t line = tab5_scroll_line(args[0]);
    x_offsets[line] = tab5_scroll_offset(args[1], H_RES + OFFSCREEN_X_PX);
    y_offsets[line] = tab5_scroll_offset(args[2], V_RES + OFFSCREEN_Y_PX);
    x_speeds[line] = mp_obj_get_int(args[3]);
    y_speeds[line] = mp_obj_get_int(args[4]);
    display_mark_rows_untrackable();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_bg_scroll_obj, 0, 5, tulip_bg_scroll);

static mp_obj_t tulip_bg_scroll_x_speed(mp_obj_t line_obj, mp_obj_t speed_obj) {
    tab5_require_scroll_ready();
    x_speeds[tab5_scroll_line(line_obj)] = mp_obj_get_int(speed_obj);
    display_mark_rows_untrackable();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(tulip_bg_scroll_x_speed_obj, tulip_bg_scroll_x_speed);

static mp_obj_t tulip_bg_scroll_y_speed(mp_obj_t line_obj, mp_obj_t speed_obj) {
    tab5_require_scroll_ready();
    y_speeds[tab5_scroll_line(line_obj)] = mp_obj_get_int(speed_obj);
    display_mark_rows_untrackable();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(tulip_bg_scroll_y_speed_obj, tulip_bg_scroll_y_speed);

static mp_obj_t tulip_bg_scroll_x_offset(mp_obj_t line_obj, mp_obj_t offset_obj) {
    tab5_require_scroll_ready();
    x_offsets[tab5_scroll_line(line_obj)] = tab5_scroll_offset(offset_obj, H_RES + OFFSCREEN_X_PX);
    display_mark_rows_untrackable();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(tulip_bg_scroll_x_offset_obj, tulip_bg_scroll_x_offset);

static mp_obj_t tulip_bg_scroll_y_offset(mp_obj_t line_obj, mp_obj_t offset_obj) {
    tab5_require_scroll_ready();
    y_offsets[tab5_scroll_line(line_obj)] = tab5_scroll_offset(offset_obj, V_RES + OFFSCREEN_Y_PX);
    display_mark_rows_untrackable();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(tulip_bg_scroll_y_offset_obj, tulip_bg_scroll_y_offset);

static mp_obj_t tulip_bg_swap(void) {
    tab5_require_scroll_ready();
    display_swap();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_bg_swap_obj, tulip_bg_swap);

extern uint8_t spriteno_activated;

static void tab5_require_sprites_ready(void) {
    if (sprite_ram == NULL || sprite_x_px == NULL || sprite_y_px == NULL ||
        sprite_w_px == NULL || sprite_h_px == NULL || sprite_vis == NULL ||
        sprite_mem == NULL || collision_bitfield == NULL) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("sprites are not ready"));
    }
}

static size_t tab5_sprite_index(mp_obj_t sprite_obj) {
    int sprite = mp_obj_get_int(sprite_obj);
    if (sprite < 0 || sprite >= SPRITES) {
        mp_raise_ValueError(MP_ERROR_TEXT("sprite index out of range"));
    }
    return (size_t)sprite;
}

// mem_pos and `pixels` count pixels of sprite RAM (SPRITE_RAM_BYTES is the
// pixel count too, whatever its name says).
static size_t tab5_sprite_mem_pos(mp_obj_t mem_obj, size_t pixels) {
    int mem_pos = mp_obj_get_int(mem_obj);
    if (mem_pos < 0 || (size_t)mem_pos > SPRITE_RAM_BYTES || pixels > SPRITE_RAM_BYTES - (size_t)mem_pos) {
        mp_raise_ValueError(MP_ERROR_TEXT("sprite RAM range out of bounds"));
    }
    return (size_t)mem_pos;
}

// Raw sprite bytes are native pixels, like bg_bitmap()'s: BYTES_PER_PIXEL
// bytes each, so a bg_bitmap() of w x h goes straight in and takes w*h of
// sprite RAM.
static mp_obj_t tulip_sprite_bitmap(mp_obj_t data_or_pos, mp_obj_t pos_or_length) {
    tab5_require_sprites_ready();
    mp_buffer_info_t bitmap;
    if (mp_get_buffer(data_or_pos, &bitmap, MP_BUFFER_READ)) {
        if (bitmap.len % BYTES_PER_PIXEL != 0) {
            mp_raise_ValueError(MP_ERROR_TEXT("sprite bitmap must be whole pixels"));
        }
        size_t mem_pos = tab5_sprite_mem_pos(pos_or_length, bitmap.len / BYTES_PER_PIXEL);
        memcpy(sprite_ram + mem_pos, bitmap.buf, bitmap.len);
        display_mark_dirty();
        return mp_obj_new_int_from_uint(bitmap.len);
    }
    int length = mp_obj_get_int(pos_or_length);
    if (length < 0 || length % BYTES_PER_PIXEL != 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("sprite bitmap length must be whole pixels"));
    }
    size_t mem_pos = tab5_sprite_mem_pos(data_or_pos, (size_t)length / BYTES_PER_PIXEL);
    return mp_obj_new_bytes((const uint8_t *)(sprite_ram + mem_pos), (size_t)length);
}
static MP_DEFINE_CONST_FUN_OBJ_2(tulip_sprite_bitmap_obj, tulip_sprite_bitmap);

static mp_obj_t tulip_sprite_png(mp_obj_t png_obj, mp_obj_t mem_obj) {
    tab5_require_sprites_ready();
    mp_buffer_info_t png = {0};
    bool free_png = false;
    if (mp_obj_is_str(png_obj)) {
        const char *filename = mp_obj_str_get_str(png_obj);
        int32_t size = file_size(filename);
        if (size < 0) {
            mp_raise_OSError(MP_ENOENT);
        }
        if (size == 0) {
            mp_raise_ValueError(MP_ERROR_TEXT("PNG file is empty"));
        }
        png.buf = malloc_caps((size_t)size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (png.buf == NULL) {
            mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("unable to allocate PNG input"));
        }
        png.len = (size_t)size;
        free_png = true;
        if (read_file(filename, png.buf, size, 1) != (uint32_t)size) {
            free_caps(png.buf);
            mp_raise_OSError(MP_EIO);
        }
    } else {
        mp_get_buffer_raise(png_obj, &png, MP_BUFFER_READ);
        if (png.len == 0) {
            mp_raise_ValueError(MP_ERROR_TEXT("PNG data is empty"));
        }
    }

    unsigned char *image = NULL;
    unsigned width = 0;
    unsigned height = 0;
    unsigned error = lodepng_decode32(&image, &width, &height, png.buf, png.len);
    if (free_png) {
        free_caps(png.buf);
    }
    if (error != 0) {
        free_caps(image);
        mp_raise_ValueError(MP_ERROR_TEXT("invalid PNG data"));
    }
    if (width == 0 || height == 0 || width > SIZE_MAX / height) {
        free_caps(image);
        mp_raise_ValueError(MP_ERROR_TEXT("invalid sprite dimensions"));
    }

    size_t pixels = (size_t)width * height;
    size_t mem_pos;
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mem_pos = tab5_sprite_mem_pos(mem_obj, pixels);
        nlr_pop();
    } else {
        free_caps(image);
        nlr_jump(nlr.ret_val);
    }
    for (size_t pixel = 0; pixel < pixels; pixel++) {
        const uint8_t *rgba = image + pixel * 4;
        sprite_ram[mem_pos + pixel] = rgba[3] == 0 ? ALPHA : px_from_rgb(rgba[0], rgba[1], rgba[2]);
    }
    free_caps(image);

    mp_obj_t result[] = {
        mp_obj_new_int_from_uint(width),
        mp_obj_new_int_from_uint(height),
        mp_obj_new_int_from_uint(pixels),
    };
    return mp_obj_new_tuple(3, result);
}
static MP_DEFINE_CONST_FUN_OBJ_2(tulip_sprite_png_obj, tulip_sprite_png);

static mp_obj_t tulip_sprite_register(size_t n_args, const mp_obj_t *args) {
    tab5_require_sprites_ready();
    if (n_args == 3) {
        mp_raise_TypeError(MP_ERROR_TEXT("sprite_register needs both width and height"));
    }
    size_t sprite = tab5_sprite_index(args[0]);
    size_t pixels = (size_t)sprite_w_px[sprite] * sprite_h_px[sprite];
    if (n_args == 4) {
        int width = mp_obj_get_int(args[2]);
        int height = mp_obj_get_int(args[3]);
        if (width <= 0 || height <= 0 || width > H_RES || height > V_RES ||
            (size_t)width > SPRITE_RAM_BYTES / (size_t)height) {
            mp_raise_ValueError(MP_ERROR_TEXT("invalid sprite dimensions"));
        }
        pixels = (size_t)width * (size_t)height;
        sprite_w_px[sprite] = width;
        sprite_h_px[sprite] = height;
    }
    size_t mem_pos = tab5_sprite_mem_pos(args[1], pixels);
    sprite_mem[sprite] = mem_pos;
    if (spriteno_activated < sprite + 1) {
        spriteno_activated = sprite + 1;
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_sprite_register_obj, 2, 4, tulip_sprite_register);

static mp_obj_t tulip_sprite_move(mp_obj_t sprite_obj, mp_obj_t x_obj, mp_obj_t y_obj) {
    tab5_require_sprites_ready();
    size_t sprite = tab5_sprite_index(sprite_obj);
    int x = mp_obj_get_int(x_obj);
    int y = mp_obj_get_int(y_obj);
    if (x < 0 || x >= H_RES || y < 0 || y >= V_RES) {
        mp_raise_ValueError(MP_ERROR_TEXT("sprite position out of range"));
    }
    sprite_x_px[sprite] = x;
    sprite_y_px[sprite] = y;
    display_mark_dirty();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(tulip_sprite_move_obj, tulip_sprite_move);

static mp_obj_t tulip_sprite_on(mp_obj_t sprite_obj) {
    tab5_require_sprites_ready();
    size_t sprite = tab5_sprite_index(sprite_obj);
    size_t pixels = (size_t)sprite_w_px[sprite] * sprite_h_px[sprite];
    if (pixels == 0 || sprite_mem[sprite] > SPRITE_RAM_BYTES ||
        pixels > SPRITE_RAM_BYTES - sprite_mem[sprite]) {
        mp_raise_ValueError(MP_ERROR_TEXT("sprite layout is not registered"));
    }
    sprite_vis[sprite] = SPRITE_IS_SPRITE;
    display_mark_dirty();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(tulip_sprite_on_obj, tulip_sprite_on);

static mp_obj_t tulip_sprite_off(mp_obj_t sprite_obj) {
    tab5_require_sprites_ready();
    sprite_vis[tab5_sprite_index(sprite_obj)] = 0;
    display_mark_dirty();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(tulip_sprite_off_obj, tulip_sprite_off);

static mp_obj_t tulip_sprite_clear(void) {
    tab5_require_sprites_ready();
    display_reset_sprites();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_sprite_clear_obj, tulip_sprite_clear);

static mp_obj_t tulip_collisions(void) {
    tab5_require_sprites_ready();
    mp_obj_t collisions = mp_obj_new_list(0, NULL);
    for (uint8_t first = 0; first < SPRITES; first++) {
        for (uint8_t second = first + 1; second < SPRITES; second++) {
            if (collide_mask_get(first, second)) {
                mp_obj_t pair[] = {mp_obj_new_int(first), mp_obj_new_int(second)};
                mp_obj_list_append(collisions, mp_obj_new_tuple(2, pair));
            }
        }
    }
    memset(collision_bitfield, 0, 62);
    return collisions;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_collisions_obj, tulip_collisions);

static mp_obj_t tulip_gpu_reset(void) {
    display_reset_bg();
    display_reset_sprites();
    display_reset_tfb();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_gpu_reset_obj, tulip_gpu_reset);

static mp_obj_t tulip_gpu(void) {
    return mp_obj_new_float(reported_gpu_usage);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_gpu_obj, tulip_gpu);

static mp_obj_t tulip_fps(void) {
    return mp_obj_new_float(reported_fps);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_fps_obj, tulip_fps);

static mp_obj_t tulip_brightness(size_t n_args, const mp_obj_t *args) {
    if (n_args == 0) return mp_obj_new_int(brightness);
    int amount = mp_obj_get_int(args[0]);
    if (amount < 1 || amount > 9) {
        mp_raise_ValueError(MP_ERROR_TEXT("brightness must be between 1 and 9"));
    }
    brightness = amount;
    tab5_display_brightness((uint8_t)amount);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_brightness_obj, 0, 1, tulip_brightness);

// The two indicator LEDs on the built-in keyboard, 0-100. Unlike the screen's
// brightness this is one number for both LEDs; their colors are the keyboard
// firmware's own (green in HID mode, and the caps indicator blinking blue).
static mp_obj_t tulip_keyboard_brightness(size_t n_args, const mp_obj_t *args) {
    if (n_args == 0) return mp_obj_new_int(tab5_keyboard_get_brightness());
    int amount = mp_obj_get_int(args[0]);
    if (amount < 0 || amount > 100) {
        mp_raise_ValueError(MP_ERROR_TEXT("keyboard_brightness must be between 0 and 100"));
    }
    tab5_keyboard_set_brightness((uint8_t)amount);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_keyboard_brightness_obj, 0, 1, tulip_keyboard_brightness);

static mp_obj_t tulip_int_screenshot(size_t n_args, const mp_obj_t *args) {
    const char *filename = mp_obj_str_get_str(args[0]);
    if (n_args == 5) {
        display_screenshot((char *)filename, mp_obj_get_int(args[1]), mp_obj_get_int(args[2]),
                           mp_obj_get_int(args[3]), mp_obj_get_int(args[4]));
    } else {
        display_screenshot((char *)filename, -1, -1, -1, -1);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_int_screenshot_obj, 1, 5, tulip_int_screenshot);

static mp_obj_t tulip_tfb_str(size_t n_args, const mp_obj_t *args) {
    if (TFB == NULL || TFBf == NULL || TFBfg == NULL || TFBbg == NULL) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("text framebuffer is not ready"));
    }
    int x = mp_obj_get_int(args[0]);
    int y = mp_obj_get_int(args[1]);
    if (x < 0 || x >= TFB_COLS || y < 0 || y >= TFB_ROWS) {
        mp_raise_ValueError(MP_ERROR_TEXT("text coordinates out of range"));
    }
    size_t offset = (size_t)y * TFB_COLS + x;
    if (n_args == 2) {
        char utf8[4];
        uint16_t cp = display_tfb_read_char(x, y, utf8);
        (void)cp;
        mp_obj_t tuple[] = {
            mp_obj_new_str(utf8, strlen(utf8)),
            mp_obj_new_int(TFBf[offset]),
            mp_obj_new_int(px_to_pal(TFBfg[offset])),
            mp_obj_new_int(px_to_pal(TFBbg[offset])),
        };
        return mp_obj_new_tuple(MP_ARRAY_SIZE(tuple), tuple);
    }

    // Cells, not bytes: a fullwidth Japanese character is three UTF-8 bytes and
    // two cells, so the byte length is the wrong count to spread attributes over.
    const char *text = mp_obj_str_get_str(args[2]);
    uint16_t cells = display_tfb_place_str(text, x, y);
    // -1 (or nothing) leaves an attribute alone; a palette index or (r, g, b)
    // tuple sets it, as for the BG drawing calls.
    bool set_f = n_args > 3 && mp_obj_get_int(args[3]) >= 0;
    bool set_fg = n_args > 4 && !(mp_obj_is_int(args[4]) && mp_obj_get_int(args[4]) < 0);
    bool set_bg = n_args > 5 && !(mp_obj_is_int(args[5]) && mp_obj_get_int(args[5]) < 0);
    uint8_t f = set_f ? (uint8_t)mp_obj_get_int(args[3]) : 0;
    tulip_px_t fg = set_fg ? tab5_color_arg(args[4]) : 0;
    tulip_px_t bgc = set_bg ? tab5_color_arg(args[5]) : 0;
    for (uint16_t i = 0; i < cells; i++) {
        if (set_f) TFBf[offset + i] = f;
        if (set_fg) TFBfg[offset + i] = fg;
        if (set_bg) TFBbg[offset + i] = bgc;
    }
    display_tfb_update(y);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_tfb_str_obj, 2, 6, tulip_tfb_str);

// 0=8x12, 1=portfolio 6x8, 2=12x16, 3=Japanese 16 dot, 4=Japanese 16 dot at 2x,
// 5=Japanese in font 2's 12x16 cells
static mp_obj_t tulip_tfb_font(size_t n_args, const mp_obj_t *args) {
    if (n_args == 0) return mp_obj_new_int(tfb_font);
    int font = mp_obj_get_int(args[0]);
    if (font < TFB_FONT_8X12 || font > TFB_FONT_MAX) {
        mp_raise_ValueError(MP_ERROR_TEXT("tfb_font must be 0 to 5"));
    }
    if (font >= TFB_FONT_JP16 && !jpfont_available()) {
        mp_raise_ValueError(MP_ERROR_TEXT("this build has no Japanese font"));
    }
    tfb_font = font;
    // Picking a font by hand turns off the console's automatic switch to the
    // Japanese font on the first character CP437 cannot hold.
    tfb_font_user_set = 1;
    // The cursor can be outside a smaller font's geometry now that the fonts
    // differ in both axes.
    uint8_t visible_cols = display_tfb_visible_cols();
    uint8_t visible_rows = display_tfb_visible_rows();
    if (visible_cols && tfb_x_col >= visible_cols) tfb_x_col = visible_cols - 1;
    if (visible_rows && tfb_y_row >= visible_rows) tfb_y_row = visible_rows - 1;
    display_tfb_update(-1);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_tfb_font_obj, 0, 1, tulip_tfb_font);

// tulip.tfb_size() -> (columns, rows) actually on screen in the current font.
static mp_obj_t tulip_tfb_size(void) {
    mp_obj_t tuple[2];
    tuple[0] = mp_obj_new_int(display_tfb_visible_cols());
    tuple[1] = mp_obj_new_int(display_tfb_visible_rows());
    return mp_obj_new_tuple(2, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_tfb_size_obj, tulip_tfb_size);

// The raw HID scan codes currently held down: the modifier byte, then the six
// rollover slots. usb_host_tab5.c copies every keyboard report into last_scan,
// so this is the same view the S3 gives. tulip.joyk() is built on it, and
// without it every joystick-from-keyboard demo (ex/joy.py, ex/parallax.py) sees
// nothing at all -- joyk() checks hasattr(tulip, "keys") and quietly returns 0.
static mp_obj_t tulip_keys(void) {
    mp_obj_t tuple[7];
    tuple[0] = mp_obj_new_int(last_scan[0]);
    for (size_t i = 0; i < 6; i++) tuple[i + 1] = mp_obj_new_int(last_scan[i + 2]);
    return mp_obj_new_tuple(MP_ARRAY_SIZE(tuple), tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_keys_obj, tulip_keys);

static mp_obj_t tulip_touch(void) {
    mp_obj_t tuple[6];
    for (size_t i = 0; i < 3; i++) {
        tuple[i * 2] = mp_obj_new_int(last_touch_x[i]);
        tuple[i * 2 + 1] = mp_obj_new_int(last_touch_y[i]);
    }
    return mp_obj_new_tuple(MP_ARRAY_SIZE(tuple), tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_touch_obj, tulip_touch);

static mp_obj_t tulip_touch_callback(size_t n_args, const mp_obj_t *args) {
    s_tab5_touch_cb = n_args == 0 ? mp_const_none : args[0];
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_touch_callback_obj, 0, 1, tulip_touch_callback);

extern int16_t touch_x_delta;
extern int16_t touch_y_delta;
extern float touch_y_scale;

static mp_obj_t tulip_touch_delta(size_t n_args, const mp_obj_t *args) {
    if (n_args == 0) {
        mp_obj_t tuple[] = {
            mp_obj_new_int(touch_x_delta),
            mp_obj_new_int(touch_y_delta),
            mp_obj_new_float(touch_y_scale),
        };
        return mp_obj_new_tuple(MP_ARRAY_SIZE(tuple), tuple);
    }
    if (n_args == 1) {
        mp_raise_TypeError(MP_ERROR_TEXT("touch_delta needs x and y"));
    }
    float scale = n_args == 3 ? mp_obj_get_float(args[2]) : 1.0f;
    if (scale <= 0.0f) {
        mp_raise_ValueError(MP_ERROR_TEXT("touch y scale must be positive"));
    }
    touch_x_delta = mp_obj_get_int(args[0]);
    touch_y_delta = mp_obj_get_int(args[1]);
    touch_y_scale = scale;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_touch_delta_obj, 0, 3, tulip_touch_delta);

static mp_obj_t tulip_frame_callback(size_t n_args, const mp_obj_t *args) {
    if (n_args == 0) {
        s_tab5_frame_cb = mp_const_none;
        s_tab5_frame_arg = mp_const_none;
    } else {
        s_tab5_frame_cb = args[0];
        s_tab5_frame_arg = n_args > 1 ? args[1] : mp_const_none;
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_frame_callback_obj, 0, 2, tulip_frame_callback);

static mp_obj_t tulip_display_ready(void) {
    return mp_obj_new_bool(bg != NULL);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_display_ready_obj, tulip_display_ready);

// A soft reset (Ctrl-D, machine.soft_reset()) rebuilds the MicroPython heap but
// leaves every C static standing, and that combination is fatal here. LVGL's
// lv_global_t is deliberately allocated from the GC heap so the collector can
// reach widgets through it (see lv_global_tab5.h and lv_mem_tab5.c), but the
// guards that would re-run lv_init() -- s_tab5_lvgl_initialized below, and
// mp_lv_roots / mp_lv_roots_initialized in the generated binding -- all live in
// BSS. gc_init() therefore frees lv_global_t out from under LVGL while every
// pointer to it survives, and the first frame after the reset has
// tulip_frame_isr() schedule lv_task_handler(), which reads a callback out of
// the now-reused struct and jumps into it. It panics with a different address
// every time, because it depends on what the heap handed out in the meantime.
//
// Re-initialising LVGL instead is not enough: the display, touch, audio and USB
// host stacks are all built once from tab5_board_startup() and hold the same
// kind of state, and setup_lvgl() would leak its PSRAM draw buffer on every
// reset. A soft reset that only restarts the interpreter is not coherent on this
// board, so make it a real reset. _boot.py calls this before anything else, and
// s_tab5_lvgl_initialized is exactly the "a previous session got as far as
// bringing LVGL up" flag we need -- if it is false there is nothing dangling.
static mp_obj_t tulip_restart_if_soft_reset(void) {
    if (s_tab5_lvgl_initialized) {
        // Keep the frame ISR from scheduling LVGL work in the window before the
        // restart actually takes effect.
        s_tab5_lvgl_running = false;
        esp_restart();
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_restart_if_soft_reset_obj, tulip_restart_if_soft_reset);

static mp_obj_t tulip_ui_init(void) {
    if (!s_tab5_lvgl_initialized && bg != NULL) {
        setup_lvgl();
        s_tab5_lvgl_initialized = true;
    }
    return mp_obj_new_bool(s_tab5_lvgl_initialized);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_ui_init_obj, tulip_ui_init);

static mp_obj_t tulip_ui_start(void) {
    s_tab5_lvgl_running = s_tab5_lvgl_initialized;
    return mp_obj_new_bool(s_tab5_lvgl_running);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_ui_start_obj, tulip_ui_start);

// tab5_diag / tab5_render_stats / audio_diag are REPL-only instruments. Nothing
// in tulip/fs or tulip/shared/py calls them; they exist so a human can read the
// display, touch and audio task counters straight off a connected board while
// tuning this port. Keep them even though a call-graph sweep will call them dead.
static mp_obj_t tulip_tab5_diag(void) {
    mp_obj_t values[] = {
        mp_obj_new_int_from_uint(tab5_display_task_entries()),
        mp_obj_new_int_from_uint(tab5_display_bridge_frames()),
        mp_obj_new_int_from_uint(tab5_touch_task_entries()),
        mp_obj_new_int_from_uint(tab5_touch_poll_count()),
        mp_obj_new_int_from_uint(tab5_touch_down_count()),
        mp_obj_new_int_from_uint(tab5_touch_read_errors()),
        mp_obj_new_int_from_uint(s_tab5_frame_callbacks),
        mp_obj_new_int_from_uint(s_tab5_lvgl_handlers),
        // Why the touch counters are what they are: a controller that never
        // came up reads the same as a screen nobody touched.
        mp_obj_new_int(tab5_touch_init_error()),
        mp_obj_new_int_from_uint(tab5_touch_init_attempts()),
        mp_obj_new_int_from_uint(tab5_board_revision_probe_ms()),
        mp_obj_new_int_from_uint(tab5_display_vsync_count()),
    };
    return mp_obj_new_tuple(MP_ARRAY_SIZE(values), values);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_tab5_diag_obj, tulip_tab5_diag);

// Panel-level instruments, same spirit as tab5_diag: a screen that stays dark
// while the DSI refreshes happily needs the panel asked directly -- reading
// RDDPM is what established that an ST7121 was awake and displaying, and that
// the problem was upstream of it. They are not free: a DCS transfer interrupts
// the video stream, and one read is enough to blank a working panel until the
// next reboot. Reach for them on a screen that is already wrong.
static mp_obj_t tulip_tab5_lcd_cmd(size_t n_args, const mp_obj_t *args) {
    const int cmd = mp_obj_get_int(args[0]);
    mp_buffer_info_t buf = {0};
    if (n_args > 1 && args[1] != mp_const_none) {
        mp_get_buffer_raise(args[1], &buf, MP_BUFFER_READ);
    }
    return mp_obj_new_int(tab5_display_panel_cmd(cmd, (const unsigned char *)buf.buf,
                                                 (unsigned int)buf.len));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_tab5_lcd_cmd_obj, 1, 2, tulip_tab5_lcd_cmd);

static mp_obj_t tulip_tab5_lcd_read(mp_obj_t cmd_in, mp_obj_t len_in) {
    const int cmd = mp_obj_get_int(cmd_in);
    const int len = mp_obj_get_int(len_in);
    if (len < 1 || len > 16) {
        mp_raise_ValueError(MP_ERROR_TEXT("read length out of range"));
    }
    unsigned char buf[16] = {0};
    const int err = tab5_display_panel_read(cmd, buf, (unsigned int)len);
    mp_obj_t values[] = {
        mp_obj_new_int(err),
        mp_obj_new_bytes(buf, len),
    };
    return mp_obj_new_tuple(2, values);
}
static MP_DEFINE_CONST_FUN_OBJ_2(tulip_tab5_lcd_read_obj, tulip_tab5_lcd_read);

static mp_obj_t tulip_tab5_lcd_errors(void) {
    int new_err = 0;
    int on_err = 0;
    tab5_display_init_errors(&new_err, &on_err);
    mp_obj_t values[] = {
        mp_obj_new_int(new_err),
        mp_obj_new_int(on_err),
    };
    return mp_obj_new_tuple(2, values);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_tab5_lcd_errors_obj, tulip_tab5_lcd_errors);

// Per-frame render phase timings, in microseconds, for the last frame drawn.
static mp_obj_t tulip_tab5_render_stats(void) {
    tab5_render_stats_t st;
    tab5_display_render_stats(&st);
    mp_obj_t values[] = {
        mp_obj_new_int_from_uint(st.composite_us),
        mp_obj_new_int_from_uint(st.convert_us),
        mp_obj_new_int_from_uint(st.rotate_us),
        mp_obj_new_int_from_uint(st.present_us),
        mp_obj_new_int_from_uint(st.wait_us),
        mp_obj_new_int_from_uint(st.frames_skipped),
        mp_obj_new_int_from_uint(st.band_rows),
        mp_obj_new_int_from_uint(st.ppa_failures),
        mp_obj_new_int_from_uint(st.dsi_fb_count),
        mp_obj_new_bool(st.ppa_active),
        mp_obj_new_bool(st.vsync_paced),
        mp_obj_new_int_from_uint(st.ppa_timeouts),
        mp_obj_new_int(st.phase),
        mp_obj_new_int(st.ppa_last_err),
        mp_obj_new_int_from_uint(st.ppa_recoveries),
        mp_obj_new_int(st.ppa_stuck_y),
        mp_obj_new_int(st.ppa_stuck_rows),
        mp_obj_new_int(st.ppa_last_y),
        mp_obj_new_int(st.ppa_last_rows),
    };
    return mp_obj_new_tuple(MP_ARRAY_SIZE(values), values);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_tab5_render_stats_obj, tulip_tab5_render_stats);

static mp_obj_t tulip_audio_diag(size_t n_args, const mp_obj_t *args) {
    if (n_args > 0 && mp_obj_is_true(args[0])) {
        tab5_audio_reset_stats();
    }
    tab5_audio_stats_t stats;
    tab5_audio_get_stats(&stats);
    mp_obj_t values[] = {
        mp_obj_new_int_from_uint(stats.blocks),
        mp_obj_new_int_from_uint(stats.interval_overruns),
        mp_obj_new_int_from_uint(stats.render_overruns),
        mp_obj_new_int_from_uint(stats.write_errors),
        mp_obj_new_int_from_uint(stats.max_interval_us),
        mp_obj_new_int_from_uint(stats.max_render_us),
        mp_obj_new_int_from_uint(stats.max_write_us),
        mp_obj_new_int_from_uint(stats.clipped_samples),
        mp_obj_new_int_from_uint(stats.peak_sample),
        mp_obj_new_int_from_uint(stats.max_deltas_us),
        mp_obj_new_int_from_uint(stats.max_dsp_us),
    };
    return mp_obj_new_tuple(MP_ARRAY_SIZE(values), values);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_audio_diag_obj, 0, 1, tulip_audio_diag);

static mp_obj_t tulip_tfb_ready(void) {
    bool ready = (TFB != NULL) && (TFBf != NULL) && (TFBfg != NULL) && (TFBbg != NULL);
    return mp_obj_new_bool(ready);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_tfb_ready_obj, tulip_tfb_ready);

static mp_obj_t tulip_tfb_start(void) {
    tfb_active = 1;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_tfb_start_obj, tulip_tfb_start);

static mp_obj_t tulip_tfb_stop(void) {
    tfb_active = 0;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_tfb_stop_obj, tulip_tfb_stop);

// tulip.term_start() / tulip.term_stop() -- drive the console as a terminal
// rather than as a printer, which is what the ssh app puts a remote shell into.
// tulip.term_flags() is what the terminal has been told about how to encode
// keys (application cursor keys, bracketed paste); tulip.term_reply() is what
// it has to say back to the far end, which only the session knows where to send.
// The optional argument is False for a session being handed the console back
// after something else had it, where the screen and the terminal's state are
// still the session's own. It defaults to a new session.
static mp_obj_t tulip_term_start(size_t n_args, const mp_obj_t *args) {
    display_term_start((n_args > 0) ? mp_obj_is_true(args[0]) : 1);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_term_start_obj, 0, 1, tulip_term_start);

static mp_obj_t tulip_term_stop(size_t n_args, const mp_obj_t *args) {
    display_term_stop((n_args > 0) ? mp_obj_is_true(args[0]) : 1);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_term_stop_obj, 0, 1, tulip_term_stop);

static mp_obj_t tulip_term_flags(void) {
    return mp_obj_new_int(display_term_flags());
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_term_flags_obj, tulip_term_flags);

static mp_obj_t tulip_term_reply(void) {
    char buf[TERM_REPLY_BUF];
    uint8_t n = display_term_take_reply(buf, sizeof(buf));
    return mp_obj_new_bytes((const byte *)buf, n);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_term_reply_obj, tulip_term_reply);

static mp_obj_t tulip_tfb_update(void) {
    display_tfb_update(-1);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_tfb_update_obj, tulip_tfb_update);

static mp_obj_t tulip_tfb_reset(void) {
    // Avoid early-boot crashes: only touch TFB buffers when they are allocated.
    if (TFB == NULL || TFBf == NULL || TFBfg == NULL || TFBbg == NULL) {
        return mp_const_none;
    }

    for (uint32_t i = 0; i < (uint32_t)TFB_ROWS * (uint32_t)TFB_COLS; i++) {
        TFB[i] = 0;
        TFBf[i] = 0;
        TFBfg[i] = tfb_fg_pal_color;
        TFBbg[i] = tfb_bg_pal_color;
    }

    if (TFB_pxlen != NULL) {
        for (uint16_t i = 0; i < V_RES; i++) {
            TFB_pxlen[i] = 0;
        }
    }

    tfb_x_col = 0;
    tfb_y_row = 0;
    ansi_active_format = -1;
    ansi_active_fg_color = tfb_fg_pal_color;
    ansi_active_bg_color = tfb_bg_pal_color;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_tfb_reset_obj, tulip_tfb_reset);

// Put the background back the way display_start() left it: the Tulip teal fill
// plus a reset scroll table. _boot.py calls this once the C boot banner is done
// with the screen, before ui.py paints the REPL over it.
static mp_obj_t tulip_bg_reset(void) {
    if (bg != NULL) {
        display_reset_bg();
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_bg_reset_obj, tulip_bg_reset);

static mp_obj_t tulip_set_screen_as_repl(mp_obj_t on_obj) {
    lvgl_is_repl = mp_obj_is_true(on_obj) ? 1 : 0;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(tulip_set_screen_as_repl_obj, tulip_set_screen_as_repl);

static mp_obj_t tulip_defer(mp_obj_t cb_obj, mp_obj_t arg_obj, mp_obj_t delay_obj) {
    (void)cb_obj;
    (void)arg_obj;
    (void)delay_obj;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(tulip_defer_obj, tulip_defer);

// Editor bindings — the C functions are compiled into the shared sources.
extern void save_tfb(void);
extern void restore_tfb(void);
extern void editor_start(const char *filename);
extern void editor_activate(void);
extern void editor_key(int c);
extern void editor_deinit(void);

static mp_obj_t tulip_tfb_save(void) { save_tfb(); return mp_const_none; }
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_tfb_save_obj, tulip_tfb_save);

static mp_obj_t tulip_tfb_restore(void) { restore_tfb(); return mp_const_none; }
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_tfb_restore_obj, tulip_tfb_restore);

static mp_obj_t tulip_run_editor(size_t n_args, const mp_obj_t *args) {
    if (n_args > 0)
        editor_start(mp_obj_str_get_str(args[0]));
    else
        editor_start(NULL);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_run_editor_obj, 0, 1, tulip_run_editor);

static mp_obj_t tulip_activate_editor(size_t n_args, const mp_obj_t *args) {
    (void)n_args; (void)args;
    editor_activate();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_activate_editor_obj, 0, 1, tulip_activate_editor);

static mp_obj_t tulip_key_editor(size_t n_args, const mp_obj_t *args) {
    editor_key(mp_obj_get_int(args[0]));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_key_editor_obj, 1, 1, tulip_key_editor);

static mp_obj_t tulip_deinit_editor(size_t n_args, const mp_obj_t *args) {
    (void)n_args; (void)args;
    editor_deinit();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_deinit_editor_obj, 0, 0, tulip_deinit_editor);

// A registered callback owns keyboard input, so Editor keys are not also sent to LVGL.
//
// It lives in MP_STATE_PORT with the other callbacks rather than in a plain
// static, because a plain static is not a GC root: the collector would free a
// callback nothing else refers to -- a bound method or a lambda, which is what
// an app naturally passes -- while this still pointed at it, and the next key
// would schedule freed memory. That shows up as "TypeError: 'bytes' object
// isn't callable" from nowhere, or a load fault.
// keyboard_send_keys_to_micropython lives in shared/keyscan.c, which TAB5 now
// builds for the USB HID scan-code decoder (see usb_host_tab5.c).

static mp_obj_t tulip_keyboard_callback(size_t n_args, const mp_obj_t *args) {
    _tab5_keyboard_cb = (n_args > 0) ? args[0] : mp_const_none;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_keyboard_callback_obj, 0, 1, tulip_keyboard_callback);

extern int mp_interrupt_char;

// allow_ime is false for keys the IME itself is forwarding on to the app. Without
// it, tulip.key_send() would hand every forwarded key straight back to the IME.
static bool tab5_deliver_key(uint16_t key, bool allow_ime) {
    // The IME comes before everything except the interrupt char. A key it is
    // going to fold into a Japanese character must not also arrive at the REPL,
    // LVGL or the editor as a Latin letter, so this returns "consumed" and the
    // caller skips the LVGL indev too. Ctrl-C still gets through, which is what
    // makes a wedged IME escapable.
    if (allow_ime && key != mp_interrupt_char) {
        if (key == ime_toggle_key && s_tab5_ime_cb != MP_OBJ_NULL &&
            s_tab5_ime_cb != mp_const_none) {
            // Arm the flag so the frame ISR starts scheduling the drain, then let
            // the IME see the key and decide whether this turned it on or off --
            // it has a preedit line to clean up on the way out. Ignored entirely
            // when no IME is loaded, so this key cannot deafen the keyboard.
            ime_active = 1;
            ime_push_key(key);
            return true;
        }
        if (ime_active) {
            ime_push_key(key);
            return true;
        }
    }
    bool callback_consumed = _tab5_keyboard_cb != MP_OBJ_NULL && _tab5_keyboard_cb != mp_const_none;
    if (callback_consumed) {
        mp_sched_schedule(_tab5_keyboard_cb, mp_obj_new_int(key));
    }

    if (key == mp_interrupt_char) {
        mp_sched_keyboard_interrupt();
    } else if (key == 4) {
        tx_char(key);
    } else if (lvgl_is_repl) {
        // One table for what a key that is not a character sends, in keyscan.c,
        // so this and the REPL on every other board cannot drift apart. An
        // extended code with no terminal meaning goes nowhere rather than to
        // tx_char(), which takes a char: a function key used to reach the REPL
        // as whatever its low byte happened to be.
        const char *seq = keycode_ansi(key);
        if (seq != NULL) {
            while (*seq) tx_char(*seq++);
        } else if (key < 256) {
            tx_char(key);
        }
    }

    return callback_consumed;
}

bool tab5_keyboard_deliver_key(uint16_t key) {
    return tab5_deliver_key(key, true);
}

// Inject a key as if the hardware keyboard had produced it -- what ui.py's soft
// keyboard types with. This is the same entry point tulip_key_send() uses on the
// other boards (send_key_to_micropython); it stops one step short of LVGL's
// keypad indev, exactly as that one does, because a soft keyboard bound to an
// LVGL text area already feeds it directly.
// tulip.key_send(key) -- goes through the IME, so ui.py's on-screen keyboard can
// type Japanese too. tulip.key_send(key, False) skips it, which is how the IME
// forwards the keys it decided not to eat; without that it would feed itself.
static mp_obj_t tulip_key_send(size_t n_args, const mp_obj_t *args) {
    bool allow_ime = (n_args > 1) ? mp_obj_is_true(args[1]) : true;
    tab5_deliver_key((uint16_t)mp_obj_get_int(args[0]), allow_ime);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_key_send_obj, 1, 2, tulip_key_send);

/*
 * Keyboard remapping, for the USB-A keyboard.
 *
 * scan_ascii() (shared/keyscan.c) decodes HID scan codes with a hard-coded US
 * layout, so on a JIS keyboard the letters and digits are right but the symbols
 * are not, and the JIS-only keys (henkan 0x8a, muhenkan 0x8b, kana 0x88, ro 0x87,
 * yen 0x89) decode to nothing at all. Before consulting that layout scan_ascii()
 * checks key_remaps[], so a handful of entries in boot.py fixes the symbols
 * without touching code shared with the other Tulip targets.
 *
 *     tulip.key_remap(0x1f, 0x02, ord('"'))   # shift-2 types " not @
 *
 * The built-in I2C keyboard runs through scan_ascii() too (it is put in HID mode,
 * see keyboard_tab5.c), so remaps apply to both keyboards. Its own layout is US,
 * so in practice only the USB keyboard needs them.
 */
static mp_obj_t tulip_key_remap(mp_obj_t scan_obj, mp_obj_t mod_obj, mp_obj_t code_obj) {
    const uint8_t scan = (uint8_t)mp_obj_get_int(scan_obj);
    const uint16_t mod = (uint16_t)mp_obj_get_int(mod_obj);
    const uint8_t code = (uint8_t)mp_obj_get_int(code_obj);

    if (scan == 0) {
        // Slot 0 means "empty", so scan code 0 (KEY_NONE) can never be remapped.
        mp_raise_ValueError(MP_ERROR_TEXT("scan code 0 cannot be remapped"));
    }

    // Replace an existing entry for the same key rather than appending a second
    // one: scan_ascii() returns the first match, so a duplicate would silently
    // shadow the new mapping -- easy to hit while working out a layout.
    for (uint8_t i = 0; i < MAX_KEY_REMAPS; i++) {
        if (key_remaps[i].scan == scan && key_remaps[i].mod == mod) {
            key_remaps[i].code = code;
            return mp_const_none;
        }
    }

    for (uint8_t i = 0; i < MAX_KEY_REMAPS; i++) {
        if (key_remaps[i].scan == 0) {
            key_remaps[i].scan = scan;
            key_remaps[i].mod = mod;
            key_remaps[i].code = code;
            return mp_const_none;
        }
    }

    mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("key remap table is full"));
}
static MP_DEFINE_CONST_FUN_OBJ_3(tulip_key_remap_obj, tulip_key_remap);

static mp_obj_t tulip_key_remaps_clear(void) {
    for (uint8_t i = 0; i < MAX_KEY_REMAPS; i++) {
        key_remaps[i].scan = 0;
        key_remaps[i].mod = 0;
        key_remaps[i].code = 0;
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_key_remaps_clear_obj, tulip_key_remaps_clear);

/*
 * The Japanese IME's C side, which is only plumbing -- the engine is
 * shared/py/ime.py. Three things have to happen in C and nothing else does:
 * keys have to be taken away from the REPL/LVGL/editor synchronously, they have
 * to be queued so a full scheduler queue cannot lose one, and committed text has
 * to reach whatever has focus.
 */

extern uint8_t editor_on_screen;
extern void editor_refresh_cursor(void);

// tulip.ime(True/False) -> take over the keyboard / hand it back
// tulip.ime() -> is the IME holding the keyboard?
static mp_obj_t tulip_ime(size_t n_args, const mp_obj_t *args) {
    if (n_args == 0) return mp_obj_new_bool(ime_active);
    uint8_t want = mp_obj_is_true(args[0]) ? 1 : 0;
    if (!want) {
        // Anything still queued was typed at the IME, so it is not text the app
        // underneath asked for. Drop it rather than replaying it as Latin.
        ime_flush_keys();
    }
    ime_active = want;
    // The cursor says which language the keyboard is in, so it has to change at
    // the moment the IME does, not at the next thing that prints. Whichever of
    // the two owns the console's cells right now is the one to repaint.
    if (editor_on_screen) {
        editor_refresh_cursor();
    } else {
        display_tfb_refresh_cursor();
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_ime_obj, 0, 1, tulip_ime);

// tulip.ime_callback(fn) -- scheduled once per frame while the IME is active, to
// drain the key queue. Its own slot, so it does not take tulip.frame_callback().
static mp_obj_t tulip_ime_callback(size_t n_args, const mp_obj_t *args) {
    s_tab5_ime_cb = (n_args > 0) ? args[0] : mp_const_none;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_ime_callback_obj, 0, 1, tulip_ime_callback);

// tulip.ime_toggle() -> the key code that switches the IME on and off
// tulip.ime_toggle(code) -> use this key instead
//
// No default survives every keyboard -- the Tab5's own puts backslash behind a Sym
// layer, so Ctrl-\\ cannot be pressed there -- so whatever ime.keytest() shows for a
// key can be bound here.
static mp_obj_t tulip_ime_toggle(size_t n_args, const mp_obj_t *args) {
    if (n_args == 0) return mp_obj_new_int(ime_toggle_key);
    ime_toggle_key = (uint16_t)mp_obj_get_int(args[0]);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_ime_toggle_obj, 0, 1, tulip_ime_toggle);

// tulip.ime_key() -> the next queued key code, or None when the queue is empty.
static mp_obj_t tulip_ime_key(void) {
    int32_t key = ime_take_key();
    if (key < 0) return mp_const_none;
    return mp_obj_new_int(key);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_ime_key_obj, tulip_ime_key);

extern void editor_insert_string(const char *text);

// tulip.editor_insert("日本語") -- commit text into the editor at its cursor.
// The per-key path inserts one byte, which cannot carry a Japanese character.
static mp_obj_t tulip_editor_insert(mp_obj_t text_obj) {
    editor_insert_string(mp_obj_str_get_str(text_obj));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(tulip_editor_insert_obj, tulip_editor_insert);

// tulip.key_send_str("日本語") -- push text into the REPL's input as if typed.
// One tx_char() per byte: stdin_ringbuf is a byte ring, so UTF-8 goes in fine.
// Whether the REPL then accepts it is readline's business, not ours.
static mp_obj_t tulip_key_send_str(mp_obj_t text_obj) {
    size_t len = 0;
    const char *text = mp_obj_str_get_data(text_obj, &len);
    for (size_t i = 0; i < len; i++) {
        tx_char((unsigned char)text[i]);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(tulip_key_send_str_obj, tulip_key_send_str);

// Block for one key and report what produced it: (character, scan code,
// modifier). tulip.remap() uses this to learn a key before remapping it.
// last_held_code/last_held_modifier are set by scan_ascii(), so they carry the
// USB keyboard's raw HID report; a key from the built-in keyboard leaves them at
// whatever the USB keyboard last sent (0 if none).
static mp_obj_t tulip_key_wait(void) {
    mp_obj_t tuple[3];
    tuple[0] = mp_obj_new_int(mp_hal_stdin_rx_chr());
    tuple[1] = mp_obj_new_int(last_held_code);
    tuple[2] = mp_obj_new_int(last_held_modifier);
    return mp_obj_new_tuple(3, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_key_wait_obj, tulip_key_wait);

// What the USB-A port and the built-in keyboard are actually doing. The counts
// separate "nothing ever attached" from "it attached but carried no interface we
// handle" -- the two look identical from the outside, and the built-in keyboard
// types into the same REPL as a USB one, so which keyboard produced a character
// is otherwise a guess.
static mp_obj_t tulip_usb_status(void) {
    mp_obj_t dict = mp_obj_new_dict(17);
    #define TAB5_STATUS(name, value) \
        mp_obj_dict_store(dict, mp_obj_new_str(name, strlen(name)), value)
    TAB5_STATUS("attached", mp_obj_new_int(tab5_usb_attach_count()));
    TAB5_STATUS("detached", mp_obj_new_int(tab5_usb_detach_count()));
    TAB5_STATUS("unclaimed", mp_obj_new_int(tab5_usb_unclaimed_count()));
    TAB5_STATUS("release_errors", mp_obj_new_int(tab5_usb_release_errors()));
    TAB5_STATUS("close_errors", mp_obj_new_int(tab5_usb_close_errors()));
    TAB5_STATUS("free_errors", mp_obj_new_int(tab5_usb_free_errors()));
    TAB5_STATUS("enum_retries", mp_obj_new_int(tab5_usb_enum_retries()));
    TAB5_STATUS("midi", mp_obj_new_bool(tab5_usb_midi_connected()));
    TAB5_STATUS("keyboard", mp_obj_new_bool(tab5_usb_keyboard_connected()));
    TAB5_STATUS("mouse", mp_obj_new_bool(tab5_usb_mouse_connected()));
    TAB5_STATUS("builtin_keyboard", mp_obj_new_bool(tab5_keyboard_connected()));
    // Keys queued from either keyboard: a USB one joins the built-in keyboard's
    // ring buffer (tab5_keyboard_push_key), which is the whole point of that
    // design, so this counter cannot attribute a key to one or the other. Use
    // the "keyboard" flag above to tell whether a USB keyboard is attached.
    TAB5_STATUS("keys_queued", mp_obj_new_int(tab5_keyboard_event_count()));
    // Root-port hardware state. "attached" above counts devices that finished
    // enumerating, so it stays 0 both for an empty connector and for a device
    // that is plugged in and failing -- which are opposite problems. "port"
    // here is the D+ pull-up the device asserts as soon as it sees VBUS, so
    // port=True with attached=0 means the device is there and enumeration is
    // what is broken. "overcurrent" means the connector's load switch tripped.
    tab5_usb_port_state_t port;
    tab5_usb_port_state(&port);
    TAB5_STATUS("port", mp_obj_new_bool(port.connected));
    TAB5_STATUS("port_enabled", mp_obj_new_bool(port.enabled));
    TAB5_STATUS("overcurrent", mp_obj_new_bool(port.overcurrent));
    TAB5_STATUS("vbus", mp_obj_new_bool(port.powered));
    TAB5_STATUS("port_speed", mp_obj_new_int(port.speed));
    #undef TAB5_STATUS
    return dict;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_usb_status_obj, tulip_usb_status);

// Drive the USB-A connector's 5V load switch by hand, and read it back with no
// argument. A device that will not start is either not being given power or is
// pulling the rail down; toggling the switch while watching the device tells
// which, and the read-back says whether the expander actually took the write.
static mp_obj_t tulip_usb_host_power(size_t n_args, const mp_obj_t *args) {
    if (n_args > 0) {
        if (!tab5_power_set_usb_host(mp_obj_is_true(args[0]))) {
            mp_raise_msg(&mp_type_RuntimeError,
                         MP_ERROR_TEXT("could not reach the USB power switch"));
        }
        return mp_const_none;
    }
    bool on = false;
    if (!tab5_power_get_usb_host(&on)) {
        mp_raise_msg(&mp_type_RuntimeError,
                     MP_ERROR_TEXT("could not read the USB power switch"));
    }
    return mp_obj_new_bool(on);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_usb_host_power_obj, 0, 1, tulip_usb_host_power);

// Wi-Fi regulatory domain, set with no wifi country code and read back with
// none. MicroPython's network.country() is not this: it only stores two bytes
// that extmod/network_cyw43.c reads, and nothing in the ESP-IDF path ever looks
// at them, so on this board it is silently a no-op. The stack takes its country
// through esp_wifi_set_country_code(), and the default "01" (world safe mode)
// is what keeps channels 12-14 closed -- a "JP" AP parked up there is invisible
// to scan and connect until this is called.
//
// esp_wifi_init() has to have run first (network.WLAN().active(True)), and the
// setting has to land before connect(). With ieee80211d left on, this is the
// country used for scanning and the AP's own country IE takes over once
// associated; pass False to pin it regardless of what the AP advertises.
static mp_obj_t tulip_wifi_country(size_t n_args, const mp_obj_t *args) {
    if (n_args == 0) {
        // Two ISO letters plus the third octet, plus room for the terminator.
        char code[4] = {0};
        esp_err_t err = esp_wifi_get_country_code(code);
        if (err != ESP_OK) {
            mp_raise_msg(&mp_type_RuntimeError,
                         MP_ERROR_TEXT("could not read the wifi country (is wifi started?)"));
        }
        return mp_obj_new_str(code, strlen(code));
    }
    const char *code = mp_obj_str_get_str(args[0]);
    bool ieee80211d = (n_args > 1) ? mp_obj_is_true(args[1]) : true;
    esp_err_t err = esp_wifi_set_country_code(code, ieee80211d);
    if (err != ESP_OK) {
        mp_raise_msg(&mp_type_RuntimeError,
                     MP_ERROR_TEXT("could not set the wifi country (started? code supported?)"));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_wifi_country_obj, 0, 2, tulip_wifi_country);

// Last-resort on-screen error report for when the Python UI stack fails to come
// up. Drawn through the text framebuffer, which display_tab5.c has running long
// before MicroPython starts, so it still works when nothing else on screen does.
// (This used to carry its own 5x7 glyph renderer, from before TFB was wired up
// on this board.)
static mp_obj_t tulip_boot_status(size_t n_args, const mp_obj_t *args) {
    if (TFB == NULL || TFBf == NULL || TFBfg == NULL || TFBbg == NULL) {
        return mp_const_none;
    }

    lvgl_is_repl = 1;
    (void)tulip_tfb_reset();
    tfb_active = 1;

    static const char header[] =
        "TAB5 SAFE MODE\n"
        "The UI did not start. Connect USB serial for the log.\n";
    display_tfb_str((unsigned char *)header, (uint16_t)(sizeof(header) - 1), 0, PX(255), PX(9));

    if (n_args == 1) {
        size_t len = 0;
        const char *detail = mp_obj_str_get_data(args[0], &len);
        display_tfb_str((unsigned char *)detail, (uint16_t)len, 0, PX(251), PX(9));
        display_tfb_str((unsigned char *)"\n", 1, 0, PX(251), PX(9));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_boot_status_obj, 0, 1, tulip_boot_status);

// tulip.cpu(). The ESP32-S3 version (compute_cpu_usage() in tulip/esp32s3/main.c)
// walks a hardcoded table of task names taken from tasks.h, and that table does not
// describe this board: Tab5 runs tab5_audio, touch_task and keyboard_task, and has
// no amy_r_task/amy_fb_task/seq_task at all, because AMY renders inside the audio
// task and the sequencer is an AMY callback (see tsequencer_tab5.c). Rather than
// keep a second list in sync, key off the task handles and report whatever is
// actually scheduled.
//
// ulRunTimeCounter is a free-running total, so a reading is only meaningful as a
// difference against the previous one -- the first call after boot therefore has
// nothing to report and returns 0.
#define TAB5_CPU_MAX_TASKS 48

typedef struct {
    TaskHandle_t handle;
    uint32_t counter;
} tab5_task_time_t;

static tab5_task_time_t s_tab5_task_times[TAB5_CPU_MAX_TASKS];
static size_t s_tab5_task_time_count = 0;

// Run time this task accumulated since the previous tulip.cpu(), remembering the
// new total. A task first seen now contributes 0: we have no interval for it yet.
// Tasks that exit leave their slot behind, which only matters if something churns
// through more than TAB5_CPU_MAX_TASKS of them -- then new tasks read as idle.
static uint32_t tab5_task_run_time_delta(TaskHandle_t handle, uint32_t counter) {
    for (size_t i = 0; i < s_tab5_task_time_count; i++) {
        if (s_tab5_task_times[i].handle == handle) {
            uint32_t delta = counter - s_tab5_task_times[i].counter;
            s_tab5_task_times[i].counter = counter;
            return delta;
        }
    }
    if (s_tab5_task_time_count < TAB5_CPU_MAX_TASKS) {
        s_tab5_task_times[s_tab5_task_time_count].handle = handle;
        s_tab5_task_times[s_tab5_task_time_count].counter = counter;
        s_tab5_task_time_count++;
    }
    return 0;
}

static mp_obj_t tulip_cpu(size_t n_args, const mp_obj_t *args) {
    bool debug = (n_args > 0) && mp_obj_is_true(args[0]);

    UBaseType_t capacity = uxTaskGetNumberOfTasks();
    TaskStatus_t *status = m_new(TaskStatus_t, capacity);
    uint32_t *deltas = m_new(uint32_t, capacity);
    UBaseType_t count = uxTaskGetSystemState(status, capacity, NULL);

    // Both idle tasks are named IDLEn. Everything else is work, including the
    // ESP-Hosted and lwIP tasks that only exist once wifi is up.
    uint32_t busy = 0;
    uint32_t idle = 0;
    for (UBaseType_t i = 0; i < count; i++) {
        deltas[i] = tab5_task_run_time_delta(status[i].xHandle, status[i].ulRunTimeCounter);
        if (strncmp(status[i].pcTaskName, "IDLE", 4) == 0) {
            idle += deltas[i];
        } else {
            busy += deltas[i];
        }
    }

    uint32_t total = busy + idle;
    // Two cores, so "100%" here means both were busy for the whole interval.
    float usage = total ? ((float)busy / (float)total) * 100.0f : 0.0f;

    if (debug) {
        // Same detail as the ESP32-S3's tulip.cpu(1), but through mp_printf rather
        // than printf. The board's plain stdout is buffered and gets picked up by
        // Tulip's on-screen REPL, so a printf here reaches the panel and never the
        // UART; mp_plat_print is where Python's own print() goes, which means this
        // lands wherever the caller's REPL actually is.
        mp_printf(&mp_plat_print, "------ CPU usage since the last tulip.cpu() call (%u tasks)\n",
                  (unsigned)count);
        for (UBaseType_t i = 0; i < count; i++) {
            mp_printf(&mp_plat_print, "%-16s %10u  %6.2f%%  stack free %u\n",
                      status[i].pcTaskName,
                      (unsigned)deltas[i],
                      total ? ((double)deltas[i] / (double)total) * 100.0 : 0.0,
                      (unsigned)status[i].usStackHighWaterMark);
        }
        mp_printf(&mp_plat_print, "------ busy %6.2f%%   idle %6.2f%%\n",
                  (double)usage, 100.0 - (double)usage);
        mp_printf(&mp_plat_print, "SPIRAM free %u (largest block %u)\n",
                  (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
                  (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM));
        mp_printf(&mp_plat_print, "internal free %u (largest block %u)\n",
                  (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                  (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
    }

    m_del(uint32_t, deltas, capacity);
    m_del(TaskStatus_t, status, capacity);
    return mp_obj_new_float_from_f(usage);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_cpu_obj, 0, 1, tulip_cpu);

// Camera. The Tab5's built-in SC2356 on MIPI-CSI, see camera_tab5.c. Frames are
// 1280x720 RGB565 -- the screen's size and LVGL's colour depth on this board --
// so camera_frame() output drops straight into an lv.image_dsc_t, and
// camera_bg() is the cheap preview: the frame converted onto the BG plane.

static void tab5_camera_raise(esp_err_t err, const char *what) {
    if (err == ESP_ERR_INVALID_STATE) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("camera is not running"));
    }
    if (err == ESP_ERR_TIMEOUT) {
        mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("camera produced no frame"));
    }
    if (err == ESP_ERR_NO_MEM) {
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("camera: out of memory"));
    }
    mp_raise_msg_varg(&mp_type_RuntimeError, MP_ERROR_TEXT("camera %s: %s"), what, esp_err_to_name(err));
}

static int tab5_camera_flip_arg(mp_obj_t obj) {
    if (obj == mp_const_none) {
        return -1;
    }
    return mp_obj_is_true(obj) ? 1 : 0;
}

static mp_obj_t tulip_camera_start(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_hflip, ARG_vflip };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_hflip, MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_vflip, MP_ARG_OBJ, {.u_obj = mp_const_none} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed), allowed, args);
    // Set before starting so the first frame already comes out the right way round.
    tab5_camera_set_flip(tab5_camera_flip_arg(args[ARG_hflip].u_obj),
                         tab5_camera_flip_arg(args[ARG_vflip].u_obj));
    esp_err_t err = tab5_camera_start();
    if (err != ESP_OK) {
        tab5_camera_raise(err, "start");
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(tulip_camera_start_obj, 0, tulip_camera_start);

static mp_obj_t tulip_camera_stop(void) {
    esp_err_t err = tab5_camera_stop();
    if (err != ESP_OK) {
        tab5_camera_raise(err, "stop");
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_camera_stop_obj, tulip_camera_stop);

static mp_obj_t tulip_camera_running(void) {
    return mp_obj_new_bool(tab5_camera_running());
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_camera_running_obj, tulip_camera_running);

static mp_obj_t tulip_camera_info(void) {
    tab5_camera_info_t info;
    tab5_camera_info(&info);
    mp_obj_t dict = mp_obj_new_dict(16);
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_sensor), mp_obj_new_str(info.sensor, strlen(info.sensor)));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_chip_id), mp_obj_new_int(info.chip_id));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_width), mp_obj_new_int(info.width));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_height), mp_obj_new_int(info.height));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_format), MP_ROM_QSTR(MP_QSTR_RGB565));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_initialized), mp_obj_new_bool(info.initialized));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_running), mp_obj_new_bool(info.running));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_frames), mp_obj_new_int_from_uint(info.frames));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_errors), mp_obj_new_int_from_uint(info.errors));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_dropped), mp_obj_new_int_from_uint(info.dropped));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_fps), mp_obj_new_int_from_uint(info.fps));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_hflip), mp_obj_new_bool(info.hflip));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_vflip), mp_obj_new_bool(info.vflip));
    // AE/AWB state from the ISP pipeline controller; -1 when the camera is stopped.
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_gain), mp_obj_new_int(info.gain));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_exposure), mp_obj_new_int(info.exposure));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_red_balance), mp_obj_new_int(info.red_balance));
    mp_obj_dict_store(dict, MP_ROM_QSTR(MP_QSTR_blue_balance), mp_obj_new_int(info.blue_balance));
    return dict;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_camera_info_obj, tulip_camera_info);

// camera_wait(seq=None, timeout_ms=1000): block until a frame newer than seq
// (default: whatever is newest now) arrives. Returns the new sequence number,
// or None on timeout.
static mp_obj_t tulip_camera_wait(size_t n_args, const mp_obj_t *args) {
    uint32_t seq = (n_args > 0 && args[0] != mp_const_none) ? (uint32_t)mp_obj_get_int(args[0]) : tab5_camera_seq();
    uint32_t timeout_ms = n_args > 1 ? (uint32_t)mp_obj_get_int(args[1]) : 1000;
    if (!tab5_camera_running()) {
        tab5_camera_raise(ESP_ERR_INVALID_STATE, "wait");
    }
    uint32_t got = tab5_camera_wait(seq, timeout_ms);
    if (got == 0) {
        return mp_const_none;
    }
    return mp_obj_new_int_from_uint(got);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_camera_wait_obj, 0, 2, tulip_camera_wait);

// camera_frame(w=1280, h=720, buf=None): the newest frame as RGB565, w*h*2
// bytes, scaled to w x h. Fills and returns buf when given, else a new bytearray.
static mp_obj_t tulip_camera_frame(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_w, ARG_h, ARG_buf };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_w, MP_ARG_INT, {.u_int = TAB5_CAMERA_WIDTH} },
        { MP_QSTR_h, MP_ARG_INT, {.u_int = TAB5_CAMERA_HEIGHT} },
        { MP_QSTR_buf, MP_ARG_OBJ, {.u_obj = mp_const_none} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed), allowed, args);
    int w = args[ARG_w].u_int;
    int h = args[ARG_h].u_int;
    if (w <= 0 || h <= 0 || w > TAB5_CAMERA_WIDTH || h > TAB5_CAMERA_HEIGHT) {
        mp_raise_ValueError(MP_ERROR_TEXT("frame size must be 1..1280 x 1..720"));
    }
    size_t need = (size_t)w * (size_t)h * 2;
    mp_obj_t result = args[ARG_buf].u_obj;
    uint16_t *dst;
    if (result == mp_const_none) {
        dst = (uint16_t *)m_new(byte, need);
        result = mp_obj_new_bytearray_by_ref(need, dst);
    } else {
        mp_buffer_info_t info;
        mp_get_buffer_raise(result, &info, MP_BUFFER_WRITE);
        if (info.len != need) {
            mp_raise_ValueError(MP_ERROR_TEXT("buf length does not match w*h*2"));
        }
        dst = (uint16_t *)info.buf;
    }
    esp_err_t err = tab5_camera_read_rgb565(dst, w, h);
    if (err != ESP_OK) {
        tab5_camera_raise(err, "frame");
    }
    return result;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(tulip_camera_frame_obj, 0, tulip_camera_frame);

// camera_bg(x=0, y=0, w=1280, h=720): draw the newest frame onto the BG plane.
static mp_obj_t tulip_camera_bg(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_x, ARG_y, ARG_w, ARG_h };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_x, MP_ARG_INT, {.u_int = 0} },
        { MP_QSTR_y, MP_ARG_INT, {.u_int = 0} },
        { MP_QSTR_w, MP_ARG_INT, {.u_int = H_RES} },
        { MP_QSTR_h, MP_ARG_INT, {.u_int = V_RES} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed), allowed, args);
    tab5_require_bg();
    int x = args[ARG_x].u_int, y = args[ARG_y].u_int;
    int w = args[ARG_w].u_int, h = args[ARG_h].u_int;
    if (w <= 0 || h <= 0 || x < 0 || y < 0 || x + w > H_RES + OFFSCREEN_X_PX ||
        y + h > V_RES + OFFSCREEN_Y_PX) {
        mp_raise_ValueError(MP_ERROR_TEXT("bitmap rectangle out of range"));
    }
    esp_err_t err = tab5_camera_draw_bg(x, y, w, h);
    if (err != ESP_OK) {
        tab5_camera_raise(err, "bg");
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(tulip_camera_bg_obj, 0, tulip_camera_bg);

static bool tab5_str_ends_with(const char *s, const char *suffix) {
    size_t n = strlen(s), m = strlen(suffix);
    if (m > n) {
        return false;
    }
    for (size_t i = 0; i < m; i++) {
        char a = s[n - m + i], b = suffix[i];
        if (a >= 'A' && a <= 'Z') a += 'a' - 'A';
        if (a != b) return false;
    }
    return true;
}

// camera_capture(filename=None, quality=80): the newest frame as a still.
// With a filename ending .jpg/.jpeg or .png, writes it and returns the byte
// count; with none, returns the JPEG as bytes.
static mp_obj_t tulip_camera_capture(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_filename, ARG_quality };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_filename, MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_quality, MP_ARG_INT, {.u_int = 80} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed), allowed, args);
    int quality = args[ARG_quality].u_int;
    if (quality < 1 || quality > 100) {
        mp_raise_ValueError(MP_ERROR_TEXT("quality must be 1..100"));
    }
    const char *filename = NULL;
    bool png = false;
    if (args[ARG_filename].u_obj != mp_const_none) {
        filename = mp_obj_str_get_str(args[ARG_filename].u_obj);
        if (tab5_str_ends_with(filename, ".png")) {
            png = true;
        } else if (!tab5_str_ends_with(filename, ".jpg") && !tab5_str_ends_with(filename, ".jpeg")) {
            mp_raise_ValueError(MP_ERROR_TEXT("filename must end in .jpg or .png"));
        }
    }
    uint8_t *data = NULL;
    size_t len = 0;
    esp_err_t err = png ? tab5_camera_encode_png(&data, &len) : tab5_camera_encode_jpeg(quality, &data, &len);
    if (err != ESP_OK) {
        tab5_camera_raise(err, png ? "png" : "jpeg");
    }
    if (filename == NULL) {
        mp_obj_t result = mp_obj_new_bytes(data, len);
        free_caps(data);
        return result;
    }
    uint32_t written = write_file(filename, data, (uint32_t)len, 1);
    free_caps(data);
    if (written != len) {
        mp_raise_OSError(MP_EIO);
    }
    return mp_obj_new_int_from_uint(written);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(tulip_camera_capture_obj, 0, tulip_camera_capture);

// camera_flip(hflip=None, vflip=None): mirror on the sensor; returns (hflip, vflip).
static mp_obj_t tulip_camera_flip(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_hflip, ARG_vflip };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_hflip, MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_vflip, MP_ARG_OBJ, {.u_obj = mp_const_none} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed), allowed, args);
    esp_err_t err = tab5_camera_set_flip(tab5_camera_flip_arg(args[ARG_hflip].u_obj),
                                         tab5_camera_flip_arg(args[ARG_vflip].u_obj));
    if (err != ESP_OK) {
        tab5_camera_raise(err, "flip");
    }
    tab5_camera_info_t info;
    tab5_camera_info(&info);
    mp_obj_t tuple[] = { mp_obj_new_bool(info.hflip), mp_obj_new_bool(info.vflip) };
    return mp_obj_new_tuple(2, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(tulip_camera_flip_obj, 0, tulip_camera_flip);

// camera_test_pattern(on=True): the sensor's colour bars instead of the scene.
static mp_obj_t tulip_camera_test_pattern(size_t n_args, const mp_obj_t *args) {
    bool on = n_args == 0 ? true : mp_obj_is_true(args[0]);
    esp_err_t err = tab5_camera_set_test_pattern(on);
    if (err != ESP_OK) {
        tab5_camera_raise(err, "test pattern");
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_camera_test_pattern_obj, 0, 1, tulip_camera_test_pattern);

// Microphone. The Tab5's two built-in mics through the ES7210 ADC, see
// mic_tab5.c. The rate and format are fixed (44.1 kHz, 16-bit stereo) because
// the ADC shares the speaker's full-duplex I2S; the two mics are the two
// channels of every read.
static void tab5_mic_raise(esp_err_t err, const char *what) {
    if (err == ESP_ERR_NO_MEM) {
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("microphone: out of memory"));
    }
    mp_raise_msg_varg(&mp_type_RuntimeError, MP_ERROR_TEXT("microphone %s: %s"), what, esp_err_to_name(err));
}

// mic_start(gain=30): program the ADC (once), open it and start capturing into
// the ring. gain is the ADC input gain in dB, 0..37.
static mp_obj_t tulip_mic_start(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_gain };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_gain, MP_ARG_INT, {.u_int = 30} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed), allowed, args);
    esp_err_t err = tab5_mic_start(args[ARG_gain].u_int);
    if (err != ESP_OK) {
        tab5_mic_raise(err, "start");
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(tulip_mic_start_obj, 0, tulip_mic_start);

static mp_obj_t tulip_mic_stop(void) {
    esp_err_t err = tab5_mic_stop();
    if (err != ESP_OK) {
        tab5_mic_raise(err, "stop");
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_mic_stop_obj, tulip_mic_stop);

static mp_obj_t tulip_mic_running(void) {
    return mp_obj_new_bool(tab5_mic_running());
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_mic_running_obj, tulip_mic_running);

static mp_obj_t tulip_mic_info(void) {
    tab5_mic_info_t info;
    tab5_mic_info(&info);
    mp_obj_t d = mp_obj_new_dict(0);
    mp_obj_dict_store(d, MP_ROM_QSTR(MP_QSTR_running), mp_obj_new_bool(info.running));
    mp_obj_dict_store(d, MP_ROM_QSTR(MP_QSTR_initialized), mp_obj_new_bool(info.initialized));
    mp_obj_dict_store(d, MP_ROM_QSTR(MP_QSTR_sample_rate), mp_obj_new_int(info.sample_rate));
    mp_obj_dict_store(d, MP_ROM_QSTR(MP_QSTR_channels), mp_obj_new_int(info.channels));
    mp_obj_dict_store(d, MP_ROM_QSTR(MP_QSTR_bits), mp_obj_new_int(info.bits));
    mp_obj_dict_store(d, MP_ROM_QSTR(MP_QSTR_gain), mp_obj_new_int(info.gain));
    mp_obj_dict_store(d, MP_ROM_QSTR(MP_QSTR_blocks), mp_obj_new_int_from_uint(info.blocks));
    mp_obj_dict_store(d, MP_ROM_QSTR(MP_QSTR_read_errors), mp_obj_new_int_from_uint(info.read_errors));
    mp_obj_dict_store(d, MP_ROM_QSTR(MP_QSTR_overruns), mp_obj_new_int_from_uint(info.overruns));
    mp_obj_dict_store(d, MP_ROM_QSTR(MP_QSTR_available), mp_obj_new_int_from_uint(info.available));
    mp_obj_dict_store(d, MP_ROM_QSTR(MP_QSTR_capacity), mp_obj_new_int_from_uint(info.capacity));
    mp_obj_dict_store(d, MP_ROM_QSTR(MP_QSTR_peak_left), mp_obj_new_int_from_uint(info.peak_left));
    mp_obj_dict_store(d, MP_ROM_QSTR(MP_QSTR_peak_right), mp_obj_new_int_from_uint(info.peak_right));
    return d;
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_mic_info_obj, tulip_mic_info);

// mic_read(frames=None, timeout_ms=1000): pull interleaved L,R int16 frames out
// of the ring as bytes (4 bytes per frame). With frames=None, returns whatever
// is buffered, waiting up to timeout_ms for the first block if the ring is
// empty. Returns None if nothing arrived in time.
#define TAB5_MIC_READ_DEFAULT_FRAMES 4096
static mp_obj_t tulip_mic_read(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_frames, ARG_timeout_ms };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_frames, MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_timeout_ms, MP_ARG_INT, {.u_int = 1000} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed), allowed, args);
    if (!tab5_mic_running()) {
        tab5_mic_raise(ESP_ERR_INVALID_STATE, "read");
    }
    size_t req = TAB5_MIC_READ_DEFAULT_FRAMES;
    if (args[ARG_frames].u_obj != mp_const_none) {
        mp_int_t f = mp_obj_get_int(args[ARG_frames].u_obj);
        if (f <= 0) {
            mp_raise_ValueError(MP_ERROR_TEXT("frames must be positive"));
        }
        req = (size_t)f;
    }
    uint32_t timeout_ms = args[ARG_timeout_ms].u_int < 0 ? 0 : (uint32_t)args[ARG_timeout_ms].u_int;
    int16_t *buf = malloc_caps(req * TAB5_MIC_CHANNELS * sizeof(int16_t),
                               MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (buf == NULL) {
        tab5_mic_raise(ESP_ERR_NO_MEM, "read");
    }
    size_t got = tab5_mic_read(buf, req, timeout_ms);
    if (got == 0) {
        free_caps(buf);
        return mp_const_none;
    }
    mp_obj_t result = mp_obj_new_bytes((const uint8_t *)buf, got * TAB5_MIC_CHANNELS * sizeof(int16_t));
    free_caps(buf);
    return result;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(tulip_mic_read_obj, 0, tulip_mic_read);

// mic_level(): the last block's per-mic peak as (left, right), each 0.0..1.0.
static mp_obj_t tulip_mic_level(void) {
    float l = 0.0f, r = 0.0f;
    tab5_mic_levels(&l, &r);
    mp_obj_t tuple[] = { mp_obj_new_float(l), mp_obj_new_float(r) };
    return mp_obj_new_tuple(2, tuple);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_mic_level_obj, tulip_mic_level);

// mic_gain(db=None): with no argument, returns the current ADC gain; with one,
// sets it (0..37 dB) and returns the clamped value.
static mp_obj_t tulip_mic_gain(size_t n_args, const mp_obj_t *args) {
    if (n_args > 0) {
        esp_err_t err = tab5_mic_set_gain(mp_obj_get_int(args[0]));
        if (err != ESP_OK) {
            tab5_mic_raise(err, "gain");
        }
    }
    tab5_mic_info_t info;
    tab5_mic_info(&info);
    return mp_obj_new_int(info.gain);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_mic_gain_obj, 0, 1, tulip_mic_gain);

// mic_record(seconds, filename=None, mono=False): block for `seconds` and
// collect the audio. With a filename ending .wav, writes a WAV and returns the
// byte count; otherwise returns the raw PCM as bytes. mono averages the two mics
// into one channel. Capped at 30 s so a stray call cannot exhaust PSRAM.
#define TAB5_MIC_RECORD_MAX_SECONDS 30
static mp_obj_t tulip_mic_record(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_seconds, ARG_filename, ARG_mono };
    static const mp_arg_t allowed[] = {
        { MP_QSTR_seconds, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_filename, MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_mono, MP_ARG_BOOL, {.u_bool = false} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed), allowed, args);
    if (!tab5_mic_running()) {
        tab5_mic_raise(ESP_ERR_INVALID_STATE, "record");
    }
    float seconds = mp_obj_get_float(args[ARG_seconds].u_obj);
    if (seconds <= 0.0f || seconds > (float)TAB5_MIC_RECORD_MAX_SECONDS) {
        mp_raise_ValueError(MP_ERROR_TEXT("seconds must be > 0 and <= 30"));
    }
    bool mono = args[ARG_mono].u_bool;

    tab5_mic_info_t info;
    tab5_mic_info(&info);
    uint32_t total_frames = (uint32_t)(seconds * (float)info.sample_rate);
    // Capture is always native stereo; a mono file is downmixed on the way out.
    int16_t *pcm = malloc_caps(total_frames * TAB5_MIC_CHANNELS * sizeof(int16_t),
                               MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (pcm == NULL) {
        tab5_mic_raise(ESP_ERR_NO_MEM, "record");
    }

    tab5_mic_flush();
    uint32_t got = 0;
    while (got < total_frames) {
        size_t n = tab5_mic_read(pcm + got * TAB5_MIC_CHANNELS, total_frames - got, 1000);
        if (n == 0) {
            break;  // capture stopped or timed out
        }
        got += n;
    }

    uint32_t out_channels = mono ? 1 : TAB5_MIC_CHANNELS;
    if (mono) {
        // Average L,R in place into a contiguous mono run at the front.
        for (uint32_t f = 0; f < got; f++) {
            int32_t l = pcm[f * TAB5_MIC_CHANNELS + 0];
            int32_t r = pcm[f * TAB5_MIC_CHANNELS + 1];
            pcm[f] = (int16_t)((l + r) / 2);
        }
    }
    uint32_t pcm_bytes = got * out_channels * sizeof(int16_t);

    const char *filename = NULL;
    if (args[ARG_filename].u_obj != mp_const_none) {
        filename = mp_obj_str_get_str(args[ARG_filename].u_obj);
        if (!tab5_str_ends_with(filename, ".wav")) {
            free_caps(pcm);
            mp_raise_ValueError(MP_ERROR_TEXT("filename must end in .wav"));
        }
    }

    if (filename == NULL) {
        mp_obj_t result = mp_obj_new_bytes((const uint8_t *)pcm, pcm_bytes);
        free_caps(pcm);
        return result;
    }

    // Prepend a 44-byte canonical PCM WAV header, then write header + samples.
    uint32_t data_bytes = pcm_bytes;
    uint32_t byte_rate = info.sample_rate * out_channels * sizeof(int16_t);
    uint16_t block_align = (uint16_t)(out_channels * sizeof(int16_t));
    uint32_t riff_size = 36 + data_bytes;
    uint8_t *file = malloc_caps(44 + data_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (file == NULL) {
        free_caps(pcm);
        tab5_mic_raise(ESP_ERR_NO_MEM, "record");
    }
    uint8_t *h = file;
    memcpy(h, "RIFF", 4);                                        h += 4;
    h[0] = riff_size; h[1] = riff_size >> 8; h[2] = riff_size >> 16; h[3] = riff_size >> 24; h += 4;
    memcpy(h, "WAVEfmt ", 8);                                    h += 8;
    h[0] = 16; h[1] = 0; h[2] = 0; h[3] = 0;                     h += 4;  // fmt chunk size
    h[0] = 1; h[1] = 0;                                          h += 2;  // PCM
    h[0] = out_channels; h[1] = out_channels >> 8;               h += 2;
    h[0] = info.sample_rate; h[1] = info.sample_rate >> 8;
    h[2] = info.sample_rate >> 16; h[3] = info.sample_rate >> 24; h += 4;
    h[0] = byte_rate; h[1] = byte_rate >> 8; h[2] = byte_rate >> 16; h[3] = byte_rate >> 24; h += 4;
    h[0] = block_align; h[1] = block_align >> 8;                 h += 2;
    h[0] = TAB5_MIC_BITS; h[1] = 0;                              h += 2;  // bits per sample
    memcpy(h, "data", 4);                                        h += 4;
    h[0] = data_bytes; h[1] = data_bytes >> 8; h[2] = data_bytes >> 16; h[3] = data_bytes >> 24; h += 4;
    memcpy(h, pcm, data_bytes);
    free_caps(pcm);

    uint32_t written = write_file(filename, file, 44 + data_bytes, 1);
    free_caps(file);
    if (written != 44 + data_bytes) {
        mp_raise_OSError(MP_EIO);
    }
    return mp_obj_new_int_from_uint(written);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(tulip_mic_record_obj, 1, tulip_mic_record);

// imu(): read the Tab5's built-in BMI270 6-axis motion sensor and return
// (ax, ay, az, gx, gy, gz) -- accelerometer in g, gyroscope in degrees/second.
// The sensor is brought up on the first call; raises RuntimeError if it can't
// be reached (e.g. on a board without the IMU populated).
static mp_obj_t tulip_imu(void) {
    float ax, ay, az, gx, gy, gz;
    esp_err_t err = tab5_imu_read(&ax, &ay, &az, &gx, &gy, &gz);
    if (err != ESP_OK) {
        mp_raise_msg_varg(&mp_type_RuntimeError, MP_ERROR_TEXT("imu: %s"), esp_err_to_name(err));
    }
    mp_obj_t t[6] = {
        mp_obj_new_float(ax), mp_obj_new_float(ay), mp_obj_new_float(az),
        mp_obj_new_float(gx), mp_obj_new_float(gy), mp_obj_new_float(gz),
    };
    return mp_obj_new_tuple(6, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(tulip_imu_obj, tulip_imu);

/*
 * amy_overload_callback(fn) / the hook AMY calls when its CPU overload failsafe
 * trips. By the time the hook runs AMY has already reset the synth and played
 * its bleep; this is only how Python finds out, so a sketch can back off or say
 * something on screen instead of the audio just going quiet.
 *
 * The hook runs on the render task. It may therefore only schedule -- and it
 * passes the load as a percent in a small int, which needs no allocation to
 * build, so nothing here touches the MP heap off-thread.
 */
static mp_obj_t tulip_amy_overload_callback(size_t n_args, const mp_obj_t *args) {
    s_tab5_amy_overload_cb = (n_args == 0 || args[0] == mp_const_none) ? MP_OBJ_NULL : args[0];
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_amy_overload_callback_obj, 0, 1, tulip_amy_overload_callback);

void tulip_amy_overload_hook(float load) {
    if (s_tab5_amy_overload_cb != MP_OBJ_NULL && s_tab5_amy_overload_cb != mp_const_none) {
        mp_sched_schedule(s_tab5_amy_overload_cb, MP_OBJ_NEW_SMALL_INT((mp_int_t)(load * 100.0f)));
    }
}

// AMY's file-I/O hooks over MicroPython's VFS, shared with the other targets
// (they get them from amy_connector.c). audio_tab5.c installs them into
// amy_config; without them AMY's zL sample load and zT file transfer bail with
// "fopen hook not enabled on platform". Safe here only because this board's
// sysex dispatch is deferred to the MP thread -- see tulip_amy_send_sysex().
#include "../../../shared/amy_file_hooks.inc"

// pcm_load_file(): finish a `zL` (load PCM preset from a file) that a wire
// message set up, and return AMY's result code. Same shape as the other
// targets'. AMY reaches pcm_load_file() itself from parse.c; this exposes it to
// Python so a sketch can drive the same load without going through the wire.
static mp_obj_t tulip_pcm_load_file(size_t n_args, const mp_obj_t *args) {
    (void)n_args; (void)args;
    return mp_obj_new_int(pcm_load_file());
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_pcm_load_file_obj, 0, 1, tulip_pcm_load_file);

// tulip.amy_message(): the C wire-string builder that _boot.py installs over
// amy.message(). Shared verbatim with the other targets, which get it from
// amy_connector.c -- a file this board does not build. It is pure string
// building against MicroPython's own formatters, touching neither AMY nor the
// board, so it needs nothing from here beyond the MP headers already included.
#include "../../../shared/amy_message.inc"

static const mp_rom_map_elem_t tulip_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR__tulip) },
    { MP_ROM_QSTR(MP_QSTR_board), MP_ROM_PTR(&tulip_board_obj) },
    { MP_ROM_QSTR(MP_QSTR_ticks_ms), MP_ROM_PTR(&tulip_ticks_ms_obj) },
    { MP_ROM_QSTR(MP_QSTR_cpu), MP_ROM_PTR(&tulip_cpu_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_ticks_ms), MP_ROM_PTR(&tulip_amy_ticks_ms_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_sequencer_ticks), MP_ROM_PTR(&tulip_amy_sequencer_ticks_obj) },
    { MP_ROM_QSTR(MP_QSTR_seq_ticks), MP_ROM_PTR(&tulip_seq_ticks_obj) },
    { MP_ROM_QSTR(MP_QSTR_seq_add_callback), MP_ROM_PTR(&tulip_seq_add_callback_obj) },
    { MP_ROM_QSTR(MP_QSTR_seq_remove_callback), MP_ROM_PTR(&tulip_seq_remove_callback_obj) },
    { MP_ROM_QSTR(MP_QSTR_seq_remove_callbacks), MP_ROM_PTR(&tulip_seq_remove_callbacks_obj) },
    { MP_ROM_QSTR(MP_QSTR_sequencer_start), MP_ROM_PTR(&tulip_sequencer_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_get_synth_commands), MP_ROM_PTR(&tulip_amy_get_synth_commands_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_render_load), MP_ROM_PTR(&tulip_amy_render_load_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_set_render_load_threshold), MP_ROM_PTR(&tulip_amy_set_render_load_threshold_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_send), MP_ROM_PTR(&tulip_amy_send_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_message), MP_ROM_PTR(&tulip_amy_message_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_send_wire_from_sysex), MP_ROM_PTR(&tulip_amy_send_wire_from_sysex_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_send_sysex), MP_ROM_PTR(&tulip_amy_send_sysex_obj) },
    { MP_ROM_QSTR(MP_QSTR_pcm_load_file), MP_ROM_PTR(&tulip_pcm_load_file_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_overload_callback), MP_ROM_PTR(&tulip_amy_overload_callback_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_bleep), MP_ROM_PTR(&tulip_amy_bleep_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_process_single_midi_byte), MP_ROM_PTR(&tulip_amy_process_single_midi_byte_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_set_cv_from_osc), MP_ROM_PTR(&tulip_amy_set_cv_from_osc_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_dump_state), MP_ROM_PTR(&tulip_amy_dump_state_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_get_output_buffer), MP_ROM_PTR(&tulip_amy_get_output_buffer_obj) },
    { MP_ROM_QSTR(MP_QSTR_amy_get_input_buffer), MP_ROM_PTR(&tulip_amy_get_input_buffer_obj) },
    { MP_ROM_QSTR(MP_QSTR_midi_callback), MP_ROM_PTR(&tulip_midi_callback_obj) },
    { MP_ROM_QSTR(MP_QSTR_midi_in), MP_ROM_PTR(&tulip_midi_in_obj) },
    { MP_ROM_QSTR(MP_QSTR_midi_out), MP_ROM_PTR(&tulip_midi_out_obj) },
    { MP_ROM_QSTR(MP_QSTR_midi_local), MP_ROM_PTR(&tulip_midi_local_obj) },
    { MP_ROM_QSTR(MP_QSTR_sysex_in), MP_ROM_PTR(&tulip_sysex_in_obj) },
    { MP_ROM_QSTR(MP_QSTR_build_strings), MP_ROM_PTR(&tulip_build_strings_obj) },
    { MP_ROM_QSTR(MP_QSTR_screen_size), MP_ROM_PTR(&tulip_screen_size_obj) },
    { MP_ROM_QSTR(MP_QSTR_gpu), MP_ROM_PTR(&tulip_gpu_obj) },
    { MP_ROM_QSTR(MP_QSTR_fps), MP_ROM_PTR(&tulip_fps_obj) },
    { MP_ROM_QSTR(MP_QSTR_gpu_reset), MP_ROM_PTR(&tulip_gpu_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_brightness), MP_ROM_PTR(&tulip_brightness_obj) },
    { MP_ROM_QSTR(MP_QSTR_int_screenshot), MP_ROM_PTR(&tulip_int_screenshot_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_pixel), MP_ROM_PTR(&tulip_bg_pixel_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_pixel_rgb), MP_ROM_PTR(&tulip_bg_pixel_rgb_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_clear), MP_ROM_PTR(&tulip_bg_clear_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_bitmap), MP_ROM_PTR(&tulip_bg_bitmap_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_blit), MP_ROM_PTR(&tulip_bg_blit_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_png), MP_ROM_PTR(&tulip_bg_png_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_bezier), MP_ROM_PTR(&tulip_bg_bezier_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_line), MP_ROM_PTR(&tulip_bg_line_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_roundrect), MP_ROM_PTR(&tulip_bg_roundrect_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_rect), MP_ROM_PTR(&tulip_bg_rect_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_circle), MP_ROM_PTR(&tulip_bg_circle_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_triangle), MP_ROM_PTR(&tulip_bg_triangle_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_fill), MP_ROM_PTR(&tulip_bg_fill_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_str), MP_ROM_PTR(&tulip_bg_str_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_scroll), MP_ROM_PTR(&tulip_bg_scroll_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_scroll_x_speed), MP_ROM_PTR(&tulip_bg_scroll_x_speed_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_scroll_y_speed), MP_ROM_PTR(&tulip_bg_scroll_y_speed_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_scroll_x_offset), MP_ROM_PTR(&tulip_bg_scroll_x_offset_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_scroll_y_offset), MP_ROM_PTR(&tulip_bg_scroll_y_offset_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_swap), MP_ROM_PTR(&tulip_bg_swap_obj) },
    { MP_ROM_QSTR(MP_QSTR_bg_reset), MP_ROM_PTR(&tulip_bg_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_sprite_bitmap), MP_ROM_PTR(&tulip_sprite_bitmap_obj) },
    { MP_ROM_QSTR(MP_QSTR_sprite_png), MP_ROM_PTR(&tulip_sprite_png_obj) },
    { MP_ROM_QSTR(MP_QSTR_sprite_register), MP_ROM_PTR(&tulip_sprite_register_obj) },
    { MP_ROM_QSTR(MP_QSTR_sprite_move), MP_ROM_PTR(&tulip_sprite_move_obj) },
    { MP_ROM_QSTR(MP_QSTR_sprite_on), MP_ROM_PTR(&tulip_sprite_on_obj) },
    { MP_ROM_QSTR(MP_QSTR_sprite_off), MP_ROM_PTR(&tulip_sprite_off_obj) },
    { MP_ROM_QSTR(MP_QSTR_sprite_clear), MP_ROM_PTR(&tulip_sprite_clear_obj) },
    { MP_ROM_QSTR(MP_QSTR_collisions), MP_ROM_PTR(&tulip_collisions_obj) },
    { MP_ROM_QSTR(MP_QSTR_tfb_str), MP_ROM_PTR(&tulip_tfb_str_obj) },
    { MP_ROM_QSTR(MP_QSTR_tfb_font), MP_ROM_PTR(&tulip_tfb_font_obj) },
    { MP_ROM_QSTR(MP_QSTR_tfb_size), MP_ROM_PTR(&tulip_tfb_size_obj) },
    { MP_ROM_QSTR(MP_QSTR_keys), MP_ROM_PTR(&tulip_keys_obj) },
    { MP_ROM_QSTR(MP_QSTR_touch), MP_ROM_PTR(&tulip_touch_obj) },
    { MP_ROM_QSTR(MP_QSTR_touch_delta), MP_ROM_PTR(&tulip_touch_delta_obj) },
    { MP_ROM_QSTR(MP_QSTR_touch_callback), MP_ROM_PTR(&tulip_touch_callback_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame_callback), MP_ROM_PTR(&tulip_frame_callback_obj) },
    { MP_ROM_QSTR(MP_QSTR_display_ready), MP_ROM_PTR(&tulip_display_ready_obj) },
    { MP_ROM_QSTR(MP_QSTR_restart_if_soft_reset), MP_ROM_PTR(&tulip_restart_if_soft_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_ui_init), MP_ROM_PTR(&tulip_ui_init_obj) },
    { MP_ROM_QSTR(MP_QSTR_ui_start), MP_ROM_PTR(&tulip_ui_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_tab5_diag), MP_ROM_PTR(&tulip_tab5_diag_obj) },
    { MP_ROM_QSTR(MP_QSTR_tab5_render_stats), MP_ROM_PTR(&tulip_tab5_render_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_tab5_lcd_cmd), MP_ROM_PTR(&tulip_tab5_lcd_cmd_obj) },
    { MP_ROM_QSTR(MP_QSTR_tab5_lcd_read), MP_ROM_PTR(&tulip_tab5_lcd_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_tab5_lcd_errors), MP_ROM_PTR(&tulip_tab5_lcd_errors_obj) },
    { MP_ROM_QSTR(MP_QSTR_audio_diag), MP_ROM_PTR(&tulip_audio_diag_obj) },
    { MP_ROM_QSTR(MP_QSTR_tfb_ready), MP_ROM_PTR(&tulip_tfb_ready_obj) },
    { MP_ROM_QSTR(MP_QSTR_tfb_start), MP_ROM_PTR(&tulip_tfb_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_term_start), MP_ROM_PTR(&tulip_term_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_term_stop), MP_ROM_PTR(&tulip_term_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_term_flags), MP_ROM_PTR(&tulip_term_flags_obj) },
    { MP_ROM_QSTR(MP_QSTR_term_reply), MP_ROM_PTR(&tulip_term_reply_obj) },
    { MP_ROM_QSTR(MP_QSTR_tfb_stop), MP_ROM_PTR(&tulip_tfb_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_tfb_update), MP_ROM_PTR(&tulip_tfb_update_obj) },
    { MP_ROM_QSTR(MP_QSTR_tfb_reset), MP_ROM_PTR(&tulip_tfb_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_screen_as_repl), MP_ROM_PTR(&tulip_set_screen_as_repl_obj) },
    { MP_ROM_QSTR(MP_QSTR_defer), MP_ROM_PTR(&tulip_defer_obj) },
    { MP_ROM_QSTR(MP_QSTR_boot_status), MP_ROM_PTR(&tulip_boot_status_obj) },
    // Editor
    { MP_ROM_QSTR(MP_QSTR_tfb_save), MP_ROM_PTR(&tulip_tfb_save_obj) },
    { MP_ROM_QSTR(MP_QSTR_tfb_restore), MP_ROM_PTR(&tulip_tfb_restore_obj) },
    { MP_ROM_QSTR(MP_QSTR_run_editor), MP_ROM_PTR(&tulip_run_editor_obj) },
    { MP_ROM_QSTR(MP_QSTR_activate_editor), MP_ROM_PTR(&tulip_activate_editor_obj) },
    { MP_ROM_QSTR(MP_QSTR_key_editor), MP_ROM_PTR(&tulip_key_editor_obj) },
    { MP_ROM_QSTR(MP_QSTR_deinit_editor), MP_ROM_PTR(&tulip_deinit_editor_obj) },
    { MP_ROM_QSTR(MP_QSTR_keyboard_callback), MP_ROM_PTR(&tulip_keyboard_callback_obj) },
    { MP_ROM_QSTR(MP_QSTR_keyboard_brightness), MP_ROM_PTR(&tulip_keyboard_brightness_obj) },
    { MP_ROM_QSTR(MP_QSTR_key_send), MP_ROM_PTR(&tulip_key_send_obj) },
    { MP_ROM_QSTR(MP_QSTR_key_remap), MP_ROM_PTR(&tulip_key_remap_obj) },
    { MP_ROM_QSTR(MP_QSTR_key_remaps_clear), MP_ROM_PTR(&tulip_key_remaps_clear_obj) },
    { MP_ROM_QSTR(MP_QSTR_ime), MP_ROM_PTR(&tulip_ime_obj) },
    { MP_ROM_QSTR(MP_QSTR_ime_callback), MP_ROM_PTR(&tulip_ime_callback_obj) },
    { MP_ROM_QSTR(MP_QSTR_ime_toggle), MP_ROM_PTR(&tulip_ime_toggle_obj) },
    { MP_ROM_QSTR(MP_QSTR_ime_key), MP_ROM_PTR(&tulip_ime_key_obj) },
    { MP_ROM_QSTR(MP_QSTR_editor_insert), MP_ROM_PTR(&tulip_editor_insert_obj) },
    { MP_ROM_QSTR(MP_QSTR_key_send_str), MP_ROM_PTR(&tulip_key_send_str_obj) },
    { MP_ROM_QSTR(MP_QSTR_key_wait), MP_ROM_PTR(&tulip_key_wait_obj) },
    { MP_ROM_QSTR(MP_QSTR_usb_status), MP_ROM_PTR(&tulip_usb_status_obj) },
    { MP_ROM_QSTR(MP_QSTR_usb_host_power), MP_ROM_PTR(&tulip_usb_host_power_obj) },
    { MP_ROM_QSTR(MP_QSTR_wifi_country), MP_ROM_PTR(&tulip_wifi_country_obj) },
    // Camera
    { MP_ROM_QSTR(MP_QSTR_camera_start), MP_ROM_PTR(&tulip_camera_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_camera_stop), MP_ROM_PTR(&tulip_camera_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_camera_running), MP_ROM_PTR(&tulip_camera_running_obj) },
    { MP_ROM_QSTR(MP_QSTR_camera_info), MP_ROM_PTR(&tulip_camera_info_obj) },
    { MP_ROM_QSTR(MP_QSTR_camera_wait), MP_ROM_PTR(&tulip_camera_wait_obj) },
    { MP_ROM_QSTR(MP_QSTR_camera_frame), MP_ROM_PTR(&tulip_camera_frame_obj) },
    { MP_ROM_QSTR(MP_QSTR_camera_bg), MP_ROM_PTR(&tulip_camera_bg_obj) },
    { MP_ROM_QSTR(MP_QSTR_camera_capture), MP_ROM_PTR(&tulip_camera_capture_obj) },
    { MP_ROM_QSTR(MP_QSTR_camera_flip), MP_ROM_PTR(&tulip_camera_flip_obj) },
    { MP_ROM_QSTR(MP_QSTR_camera_test_pattern), MP_ROM_PTR(&tulip_camera_test_pattern_obj) },
    { MP_ROM_QSTR(MP_QSTR_mic_start), MP_ROM_PTR(&tulip_mic_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_mic_stop), MP_ROM_PTR(&tulip_mic_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_mic_running), MP_ROM_PTR(&tulip_mic_running_obj) },
    { MP_ROM_QSTR(MP_QSTR_mic_info), MP_ROM_PTR(&tulip_mic_info_obj) },
    { MP_ROM_QSTR(MP_QSTR_mic_read), MP_ROM_PTR(&tulip_mic_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_mic_level), MP_ROM_PTR(&tulip_mic_level_obj) },
    { MP_ROM_QSTR(MP_QSTR_mic_gain), MP_ROM_PTR(&tulip_mic_gain_obj) },
    { MP_ROM_QSTR(MP_QSTR_mic_record), MP_ROM_PTR(&tulip_mic_record_obj) },
    { MP_ROM_QSTR(MP_QSTR_imu), MP_ROM_PTR(&tulip_imu_obj) },
};

static MP_DEFINE_CONST_DICT(tulip_module_globals, tulip_module_globals_table);

const mp_obj_module_t tulip_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&tulip_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR__tulip, tulip_user_cmodule);
