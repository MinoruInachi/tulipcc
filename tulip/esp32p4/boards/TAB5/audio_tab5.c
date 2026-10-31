#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_task.h"
#include "esp_codec_dev.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "bsp/m5stack_tab5.h"
#include "../../../../amy/src/amy.h"

#include "audio_tab5.h"
// tulip_amy_midi_hook() -- queues MIDI in for Python.
#include "modtulip_tab5.h"
// tulip_amy_sequencer_hook() -- fans a sequencer tick out to Python callbacks.
#include "tsequencer_tab5.h"
#include "usb_host_tab5.h"  // send_usb_midi_out(), AMY's MIDI output hook
#include "mic_tab5.h"       // the mic block AMY renders as wave=AUDIO_EXT0/1
#ifdef TULIP_USER_C_DSP
#include "../../../shared/user_c_dsp.h"  // the user C DSP render/bus hooks
#endif

static const char *TAG = "TAB5-AUDIO";
static esp_codec_dev_handle_t s_speaker;
static bool s_audio_ready;
static volatile tab5_audio_stats_t s_audio_stats;

#define TAB5_AUDIO_TASK_STACK_WORDS (16 * 1024 / sizeof(StackType_t))
/* This task owns a block: deltas, the render (one half of it when dual-core is
 * on -- see "Two-core rendering" below), the mix and the codec write. At
 * tskIDLE_PRIORITY + 2 it sat below nearly every IDF service task on its core
 * (esp_timer, esp-hosted, USB), so a block's render time was really "render
 * time plus whatever preempted us" -- which is where the multi-millisecond
 * spikes and the audible dropouts came from. AMY runs its equivalent loop at
 * the same priority used here; it is safe because the loop always blocks in the
 * codec write. */
#define TAB5_AUDIO_TASK_PRIORITY (ESP_TASK_PRIO_MAX - 1)
// Core 0. Core 1 runs MicroPython, and this task sits at the top of the priority
// range: leaving it there starved mp_task (and the idle task, which trips the
// task watchdog) whenever AMY ran long. Core 0 only carries the display and touch
// tasks, both of which are far lower priority and tolerate preemption.
#define TAB5_AUDIO_TASK_CORE 0
// Where the codec's output gain starts. Full gain plus a patch that reaches full
// scale is what the class-D amp needs to sag the 3.3 V rail far enough to trip
// the brownout detector (2.6 V) when the board is running off a current-limited
// USB port -- measured 2026-10-07 with the dpwe piano, which clips at AMY's
// default volume. Lowering this would cost every app its loudness, so the
// default stays where it was and tulip.speaker_volume() lets a user who hits it
// turn the amp down instead.
#define TAB5_SPEAKER_VOLUME 100

// The two mics are the two channels of AMY's external input block, so the
// interleaved frames the ring holds are exactly the layout AMY wants.
_Static_assert(AMY_NCHANS == TAB5_MIC_CHANNELS,
               "the mic feed assumes AMY renders as many channels as the mic captures");

// Hand AMY the newest block of microphone audio, once per render block, so an
// oscillator with wave=AUDIO_EXT0 (left mic) or AUDIO_EXT1 (right) plays what
// the mics hear -- the Tab5's answer to the audio-in the S3 boards get from
// AMY's own I2S capture, which this board cannot use because it renders with
// AMY_AUDIO_IS_NONE and drives the codec itself.
//
// It follows tab5_mic_start()/stop() rather than a switch of its own: nothing
// is audible until a sketch asks for an AUDIO_EXT oscillator, and peeking does
// not consume the ring, so tulip.mic_read() still sees every frame.
static void tab5_feed_mic_to_amy(void)
{
    static int16_t block[AMY_BLOCK_SIZE * AMY_NCHANS];
    static bool amy_holds_audio;  // AMY's external block starts out zeroed

    if (!tab5_mic_running()) {
        // Stopping the mic must not leave the last block looping in AMY.
        if (amy_holds_audio) {
            memset(block, 0, sizeof(block));
            amy_set_external_input_buffer(block);
            amy_holds_audio = false;
        }
        return;
    }
    if (tab5_mic_peek_newest(block, AMY_BLOCK_SIZE) == AMY_BLOCK_SIZE) {
        amy_set_external_input_buffer(block);
        amy_holds_audio = true;
    }
}

// ---------------------------------------------------------------------------
// Two-core rendering
//
// AMY splits a block's oscillators across both cores on ESP
// (esp_render_on_cores() in amy/src/i2s.c), and Tulip CC on the S3 ships that
// way. Setting amy_config.platform.multicore here did not work, for two
// reasons that are both about this board rather than about dual-core
// rendering:
//
//  1. AMY pins its helper task to AMY_RENDER_TASK_COREID, which is core 0 --
//     the core this audio task also runs on, because core 1 belongs to
//     MicroPython and this task sits at the top of the priority range. So the
//     two "halves" never ran in parallel: they time-sliced one core at equal
//     priority and paid a task-notify round trip per block on top of that.
//  2. AMY's split is the fixed range 0..max_oscs/2 against the rest. Tulip
//     workloads pack their audible oscs into the low indices, so one half got
//     all of the work and the other got none.
//
// So the split happens here instead: the helper sits on core 1, and the
// boundary is chosen per block from where the audible oscs actually are. The
// renderer is still AMY's -- amy_render(start, end, core) exists for exactly
// this, fbl[]/per_osc_fb[]/core_max[] are allocated for both cores whatever
// config.platform.multicore says (amy.c's oscs_init loops to AMY_CORES, which
// is 2 on any ESP build), and amy_fill_buffer() sums the two. AMY's own
// multicore stays off so it creates no task of its own.
//
// Measured on this board with sustained oscs packed from index 0, split against
// not: 64 sine 3063 -> 2400 us worst block, 64 pulse 5735 -> 4032, 128 pulse
// 13869 -> 8162, and the smoothed render load 0.43 -> 0.27, 0.91 -> 0.54,
// 2.28 -> 1.28 -- about 1.75x the throughput, with the two halves finishing
// within a few hundred microseconds of each other. xanadu.py's worst block went
// 4647 -> 3013 us. A split block is bit-identical to a single-core one: AMY
// mixes in fixed point, and one osc panned hard left on the helper with another
// panned hard right on this task read 961 and 480 either way.
//
// The helper does not take the render lock: this task holds it across the whole
// block and only releases it after joining, so the teardown that lock exists to
// keep out (RESET_AMY freeing fbl and every osc) still cannot start underneath
// either half. AMY's esp_render_task does the same.
#define TAB5_RENDER_HELPER_CORE 1
// Same priority as the audio task. It is only runnable between the notify and
// the join -- well under a block -- so core 1's idle task still gets the rest
// of every block and the task watchdog stays happy. Lower than this and an IDF
// service task on core 1 would decide when our half of the audio finishes.
#define TAB5_RENDER_HELPER_PRIORITY (ESP_TASK_PRIO_MAX - 1)
#define TAB5_RENDER_HELPER_STACK_WORDS (12 * 1024 / sizeof(StackType_t))

// What tulip.amy_dual_core() selects between. Auto is the default, because the
// second core is not free: it is MicroPython's, and the helper takes it for as
// long as its half of the render runs. Measured here against the same loop with
// the split off, a counting loop in Python kept 47% of its throughput under 64
// sustained pulse oscs and 10% under 128 -- at 128 the helper is busy longer
// than a whole block. A Tab5 app usually has far more Python to run than audio
// to render, so the split should only happen when it buys something: at 32
// sine oscs, which one core renders in a quarter of a block, auto leaves core 1
// alone and Python keeps all of it.
#define TAB5_DUAL_CORE_OFF  0
#define TAB5_DUAL_CORE_ON   1
#define TAB5_DUAL_CORE_AUTO 2
// Percentages of one block's time, for auto. Engage once a single core is half
// spent: a chord can double the cost of a block, and the estimate below leads it
// by a block or two at best. Release well below that, so a load sitting near the
// line does not hand core 1 back and forth every other block.
#define TAB5_DUAL_CORE_ON_PCT  50
#define TAB5_DUAL_CORE_OFF_PCT 30
// A flash operation freezes core 1. MicroPython reads and writes its file
// system through esp_flash, which takes the flash lock and spins its own core
// with interrupts off, so the helper -- the highest-priority task on that core
// -- simply cannot be scheduled until the operation finishes. Measured on this
// board with the split forced on and 64 pulse oscs sounding: a Python loop
// reading a file held the helper off for 34 ms and one writing a file for
// 62 ms, against 27-55 us while Python was only doing arithmetic, allocating,
// or collecting garbage. Core 0 keeps running through all of it, which is why
// the single-core path never saw this.
//
// One stall is as many late blocks as it is long, if we keep asking. So the
// first one stands the split down and the audio goes back to being core 0's
// alone for about a second -- where file I/O cannot reach it. Auto only: mode 1
// means what it says, and is the mode that matches the S3's behaviour.
//
// The trigger is how long the helper took to START, not how long the join took.
// A join is also long whenever the helper's half is simply the slower of the
// two, which is ordinary imbalance and no reason to stand anything down; a
// notify that takes more than a millisecond to be picked up by the
// highest-priority task on its core is nothing but a stall.
#define TAB5_DUAL_CORE_STALL_US 1000
#define TAB5_DUAL_CORE_STALL_BLOCKS 175   /* about a second of blocks */

static TaskHandle_t s_render_helper;
static SemaphoreHandle_t s_render_helper_done;
static volatile uint16_t s_helper_start;
static volatile uint16_t s_helper_end;
static volatile uint32_t s_helper_us;   // the last half the helper rendered
static volatile int64_t s_helper_notified_at;  // when we asked for that half
static volatile uint32_t s_helper_notify_us;  // how long it took to be picked up
static volatile uint8_t s_dual_core_mode = TAB5_DUAL_CORE_AUTO;
// Whether the last block really split. Also the hysteresis state for auto, and
// what tells us fbl[1] needs clearing.
static bool s_dual_core_active;
// Smoothed cost of a block's rendering as a single core would have paid it: the
// two halves added together when we split, the whole block when we did not.
// Measuring it that way keeps the auto decision from chasing its own effect --
// the measured block time nearly halves the moment the helper engages, which on
// its own would release it again the next block. Rises most of the way at once
// and falls by a thirty-second of the gap per block, so a chord gets the second
// core almost immediately and a short gap does not give it up.
static uint32_t s_render_cost_us;
// Blocks left to render single-core after a stall (auto only).
static uint32_t s_stall_cooldown;
// Indices of this block's audible oscs, so the boundary costs one pass over
// synth[] instead of two. Internal RAM: synth[] itself is in PSRAM and this is
// walked once per block.
static uint16_t *s_audible;
static uint16_t s_audible_max;
// s_cut_ok[i]: a boundary at i does not cut a group of oscs that has to render
// on one core. See tab5_pick_split().
static uint8_t *s_cut_ok;

static void tab5_render_helper_task(void *ignored)
{
    (void)ignored;
    while (true) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);  // from tab5_render_block()
        int64_t t0 = esp_timer_get_time();
        uint32_t notify_us = (uint32_t)(t0 - s_helper_notified_at);
        s_helper_notify_us = notify_us;
        if (notify_us > s_audio_stats.max_notify_us) {
            s_audio_stats.max_notify_us = notify_us;
        }
        amy_render(s_helper_start, s_helper_end, 1);
        uint32_t helper_us = (uint32_t)(esp_timer_get_time() - t0);
        s_helper_us = helper_us;
        if (helper_us > s_audio_stats.max_helper_us) {
            s_audio_stats.max_helper_us = helper_us;
        }
        xSemaphoreGive(s_render_helper_done);  // to tab5_render_block()
    }
}

// Two oscs that have to render on the same core: forbid every boundary between
// them. A boundary b sends [0, b) to the helper, so it separates a and b's osc
// exactly when it falls in (lo, hi].
static void tab5_bind_oscs(uint16_t a, uint16_t b, uint16_t oscs)
{
    if (AMY_IS_UNSET(b) || b >= oscs) return;
    const uint16_t lo = a < b ? a : b;
    const uint16_t hi = a < b ? b : a;
    for (uint16_t i = lo + 1; i <= hi; i++) s_cut_ok[i] = 0;
}

// Where to cut the osc range so both cores get half of this block's audible
// oscs. Count, not cost: a PCM osc and a sine are not the same work, but the
// count is what we can read without rendering, and it is already far better
// than AMY's fixed midpoint. Returns 0 when there is no boundary worth or safe
// to use, which the caller reads as "render it all here".
//
// Oscillators are not independent, so the boundary cannot fall just anywhere.
// A Juno patch's voice is one chain -- osc 0 -> 2 -> 3 -> 4 -> 5, with osc 1 as
// its LFO -- and render_osc_wave() renders a whole chain into the buffer of
// whichever osc it reached first, guarding with synth[osc]->render_clock so the
// rest is not rendered twice. amy_render() then mixes that buffer with THAT
// osc's pan and level. Cut a chain in two and the guard becomes a race: the
// core that reaches the tail first renders it into its own buffer under its own
// pan, and the other core mixes silence instead, so the mix changes from block
// to block. Measured by hand -- osc 0 panned hard left chained to osc 3 panned
// hard right -- the right channel went from silent to a peak of 800 and the
// left peak stopped being repeatable. That is what made 01_Simple_Piano slot 2
// in kanplay (two Juno parts, patches 47 and 73) sound wrong.
//
// DX7 patches have the same problem through algo_source and anything with an
// LFO through mod_source, so all three links are honoured, and so is voice
// membership: AMY allocates a voice's oscs contiguously and osc_to_voice names
// them, which covers patches whose oscs are related in ways not visible here.
static uint16_t tab5_pick_split(void)
{
    const uint16_t oscs = AMY_OSCS < s_audible_max ? AMY_OSCS : s_audible_max;
    uint16_t count = 0;
    memset(s_cut_ok, 1, oscs);
    for (uint16_t osc = 0; osc < oscs; osc++) {
        const struct synthinfo *s = synth[osc];
        if (s == NULL) continue;
        if (s->status == SYNTH_AUDIBLE) s_audible[count++] = osc;
        if (osc > 0 && osc_to_voice != NULL &&
            AMY_IS_SET(osc_to_voice[osc]) && AMY_IS_SET(osc_to_voice[osc - 1]) &&
            osc_to_voice[osc] == osc_to_voice[osc - 1]) {
            s_cut_ok[osc] = 0;
        }
        tab5_bind_oscs(osc, s->chained_osc, oscs);
        for (uint8_t i = 0; i < NUM_MOD_SOURCES; i++) {
            tab5_bind_oscs(osc, s->mod_source[i], oscs);
        }
        for (uint8_t i = 0; i < MAX_ALGO_OPS; i++) {
            // algo_source is int16 because -1 means "no osc", and its unset
            // value (SHRT_MAX) is positive, so both have to be excluded.
            const int16_t a = s->algo_source[i];
            if (AMY_IS_SET(a) && a >= 0) tab5_bind_oscs(osc, (uint16_t)a, oscs);
        }
    }
    if (count < 2) return 0;
    // Nearest legal boundary to the median, in either direction, that still
    // leaves both cores something to render. A patch whose voice spans every
    // audible osc has none, and renders on one core.
    const uint16_t median = s_audible[count / 2];
    uint16_t up = median;
    while (up < oscs && !s_cut_ok[up]) up++;
    if (up >= oscs || up > s_audible[count - 1]) up = 0;
    uint16_t down = median;
    while (down > 0 && !s_cut_ok[down]) down--;
    if (down <= s_audible[0]) down = 0;
    if (up == 0) return down;
    if (down == 0) return up;
    return (median - down <= up - median) ? down : up;
}

static bool tab5_want_split(void)
{
    switch (s_dual_core_mode) {
        case TAB5_DUAL_CORE_OFF: return false;
        case TAB5_DUAL_CORE_ON:  return true;
        default: {
            if (s_stall_cooldown > 0) {
                s_stall_cooldown--;
                return false;
            }
            const uint32_t pct = s_dual_core_active ? TAB5_DUAL_CORE_OFF_PCT
                                                    : TAB5_DUAL_CORE_ON_PCT;
            return s_render_cost_us > (AMY_BLOCK_US * pct) / 100;
        }
    }
}

// What amy_render_audio()'s single-threaded branch does (amy/src/i2s.c), with
// our own split in place of esp_render_on_cores(). Called with the render lock
// already held.
static int16_t *tab5_render_block(void)
{
    const int64_t t0 = esp_timer_get_time();
    uint32_t cost_us;
    uint16_t split = 0;
    if (s_render_helper != NULL && tab5_want_split()) {
        split = tab5_pick_split();
    }
    if (split > 0) {
        s_helper_start = 0;
        s_helper_end = split;
        s_helper_us = 0;
        s_helper_notify_us = 0;
        const int64_t r0 = esp_timer_get_time();
        s_helper_notified_at = r0;
        xTaskNotifyGive(s_render_helper);        // to tab5_render_helper_task
        amy_render(split, AMY_OSCS, 0);
        const int64_t mine_done = esp_timer_get_time();
        cost_us = (uint32_t)(mine_done - r0);
        xSemaphoreTake(s_render_helper_done, portMAX_DELAY);
        const uint32_t join_us = (uint32_t)(esp_timer_get_time() - mine_done);
        if (join_us > s_audio_stats.max_join_us) s_audio_stats.max_join_us = join_us;
        if (s_helper_notify_us > TAB5_DUAL_CORE_STALL_US) {
            s_stall_cooldown = TAB5_DUAL_CORE_STALL_BLOCKS;
            s_audio_stats.helper_stalls++;
        }
        cost_us += s_helper_us;   // what one core would have spent on the block
        s_audio_stats.helper_blocks++;
        s_dual_core_active = true;
    } else {
        const int64_t r0 = esp_timer_get_time();
        amy_render(0, AMY_OSCS, 0);
        cost_us = (uint32_t)(esp_timer_get_time() - r0);
        // fbl[1] still holds whatever the helper rendered last, and
        // amy_fill_buffer() sums it into fbl[0] unconditionally on a dual-core
        // build -- so a block that skips the helper has to clear it or that
        // half-block drones on under everything. An empty range is exactly the
        // bzero, with no oscs to walk.
        if (s_dual_core_active) {
            amy_render(0, 0, 1);
            s_dual_core_active = false;
        }
    }
    if (cost_us > s_render_cost_us) s_render_cost_us += (cost_us - s_render_cost_us) >> 1;
    else                            s_render_cost_us -= (s_render_cost_us - cost_us) >> 5;
    int16_t *buf = amy_fill_buffer();
    // AMY's CPU overload failsafe, which amy_render_audio() would have called.
    amy_overload_check((uint32_t)(esp_timer_get_time() - t0));
    return buf;
}

void tab5_audio_set_dual_core(int mode)
{
    if (mode < TAB5_DUAL_CORE_OFF) mode = TAB5_DUAL_CORE_OFF;
    if (mode > TAB5_DUAL_CORE_AUTO) mode = TAB5_DUAL_CORE_AUTO;
    s_dual_core_mode = (uint8_t)mode;
}

int tab5_audio_dual_core(void)
{
    return s_dual_core_mode;
}

static void tab5_audio_task(void *ignored)
{
    (void)ignored;
    const uint32_t block_period_us = AMY_BLOCK_SIZE * 1000000U / AMY_SAMPLE_RATE;
    int64_t previous_block_start = 0;
    while (true) {
        int64_t block_start = esp_timer_get_time();
        if (previous_block_start != 0) {
            uint32_t interval_us = (uint32_t)(block_start - previous_block_start);
            if (interval_us > s_audio_stats.max_interval_us) {
                s_audio_stats.max_interval_us = interval_us;
            }
            if (interval_us > block_period_us + 1000U) {
                s_audio_stats.interval_overruns++;
            }
        }
        previous_block_start = block_start;

        // The whole block under the render lock, the way AMY's own ESP render
        // loop holds it (esp_fill_audio_buffer_task in amy/src/i2s.c). Each
        // call below takes it internally anyway -- it is recursive per thread,
        // so those are no-ops -- but the gaps between them were not covered,
        // and they are what a teardown walks through: RESET_AMY runs
        // amy_stop() + amy_start() on the sending thread, and oscs_deinit()
        // frees fbl, per_osc_fb, every osc and the mic input block. AMY deletes
        // its own render tasks before that free, but it has no idea this task
        // exists, so it kept rendering straight into freed memory (a Load
        // access fault in amy_render on a NULL fbl, 2026-10-07). Released
        // before the codec write, as AMY's loop does, so an event flush from
        // Python still gets in while we are parked on the DMA.
        amy_grab_render_lock();
        tab5_feed_mic_to_amy();
        amy_execute_deltas();
        const int64_t deltas_done = esp_timer_get_time();
        int16_t *samples = tab5_render_block();
        const int64_t dsp_done = esp_timer_get_time();
        // Split the two halves: event processing (voice allocation, patch
        // loading) behaves very differently from the per-block DSP, and only
        // one of them can be fixed by making the DSP cheaper.
        const uint32_t deltas_us = (uint32_t)(deltas_done - block_start);
        const uint32_t dsp_us = (uint32_t)(dsp_done - deltas_done);
        if (deltas_us > s_audio_stats.max_deltas_us) s_audio_stats.max_deltas_us = deltas_us;
        if (dsp_us > s_audio_stats.max_dsp_us) s_audio_stats.max_dsp_us = dsp_us;
        uint32_t render_us = (uint32_t)(esp_timer_get_time() - block_start);
        if (render_us > s_audio_stats.max_render_us) {
            s_audio_stats.max_render_us = render_us;
        }
        if (render_us > block_period_us) {
            s_audio_stats.render_overruns++;
        }

        // The board's two output channels are crossed: an osc at pan=0, which
        // AMY mixes into channel 0 alone, comes out of the right earpiece.
        // AMY's buffer is not what is wrong -- rendering the same send() on a
        // host fills channel 0 and leaves channel 1 silent -- and nothing
        // between amy_fill_buffer() and the codec write reorders it, so the
        // crossing is in the wiring to the ES8388 (whose own mixer registers
        // the driver sets are the plain L->L, R->R pair). Undo it here, inside
        // the scan that already walks every sample, so it costs a register
        // shuffle rather than a second pass. In place is safe: we hold the
        // render lock, amy_fill_buffer() is done with the block, and the next
        // block overwrites the whole thing.
        _Static_assert(AMY_NCHANS == 2, "the output swap below assumes stereo");
        for (size_t frame = 0; frame < AMY_BLOCK_SIZE; frame++) {
            int16_t *pair = &samples[frame * AMY_NCHANS];
            const int16_t left = pair[0];
            pair[0] = pair[1];
            pair[1] = left;
            for (size_t c = 0; c < AMY_NCHANS; c++) {
                int32_t value = pair[c];
                uint32_t magnitude = (uint32_t)(value < 0 ? -value : value);
                if (magnitude > s_audio_stats.peak_sample) {
                    s_audio_stats.peak_sample = magnitude;
                }
                if (value == INT16_MIN || value == INT16_MAX) {
                    s_audio_stats.clipped_samples++;
                }
            }
        }
        // samples points into AMY's output block, so the scan above belongs
        // inside the hold too.
        amy_release_render_lock();

        int64_t write_start = esp_timer_get_time();
        int result = esp_codec_dev_write(
            s_speaker, samples, AMY_BLOCK_SIZE * AMY_NCHANS * sizeof(int16_t));
        uint32_t write_us = (uint32_t)(esp_timer_get_time() - write_start);
        if (write_us > s_audio_stats.max_write_us) {
            s_audio_stats.max_write_us = write_us;
        }
        s_audio_stats.blocks++;
        if (result != ESP_CODEC_DEV_OK) {
            s_audio_stats.write_errors++;
            ESP_LOGE(TAG, "Speaker write failed: %d", result);
            vTaskDelay(pdMS_TO_TICKS(20));
        } else if (write_us < 150) {
            /* Normally this loop spends most of each block parked in the codec
             * write, which is when the rest of core 1 gets to run. If the write
             * did not block at all we are past overloaded; audio is breaking up
             * regardless, so yield rather than starve everything else. Same
             * guard AMY uses in esp_fill_audio_buffer_task(). */
            vTaskDelay(1);
        }
    }
}

void tab5_audio_init(void)
{
    fprintf(stderr, "Starting AMY on core %d\n", TAB5_AUDIO_TASK_CORE);
    fflush(stderr);

    const i2s_std_config_t i2s_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AMY_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIP_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = BSP_I2S_MCLK,
            .bclk = BSP_I2S_SCLK,
            .ws = BSP_I2S_LCLK,
            .dout = BSP_I2S_DOUT,
            .din = BSP_I2S_DSIN,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    esp_err_t ret = bsp_audio_init(&i2s_config);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Audio I2S init failed: %s", esp_err_to_name(ret));
        return;
    }

    s_speaker = bsp_audio_codec_speaker_init();
    if (s_speaker == NULL) {
        ESP_LOGW(TAG, "Speaker codec init failed");
        return;
    }

    esp_codec_dev_sample_info_t sample_info = {
        .sample_rate = AMY_SAMPLE_RATE,
        .channel = AMY_NCHANS,
        .bits_per_sample = 16,
    };
    int codec_result = esp_codec_dev_open(s_speaker, &sample_info);
    if (codec_result != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "Speaker codec open failed: %d", codec_result);
        return;
    }
    codec_result = esp_codec_dev_set_out_vol(s_speaker, TAB5_SPEAKER_VOLUME);
    if (codec_result != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "Speaker volume setup failed: %d", codec_result);
        return;
    }

    amy_config_t amy_config = amy_default_config();
    amy_config.audio = AMY_AUDIO_IS_NONE;
    amy_config.midi = AMY_MIDI_IS_NONE;
    // AMY's own multicore stays off, but the rendering is still split across
    // both cores -- we do it ourselves, because AMY's version cannot work on
    // this board: it pins its helper to core 0, where this audio task already
    // runs, and it splits at the fixed osc index max_oscs/2 while Tulip packs
    // its voices into the low ones. Measured here that way, 16 voices went from
    // 401us to 2418us peak render and xanadu.py's worst block from 8.1ms to
    // 26.4ms (the block budget is 5.8ms) -- two halves time-slicing one core,
    // one of them with all the oscs, plus a task-notify round trip per block.
    // See "Two-core rendering" above for what replaced it; multithread stays
    // off because this task is the audio loop.
    amy_config.platform.multicore = 0;
    amy_config.platform.multithread = 0;
    amy_config.features.default_synths = 0;
    amy_config.features.startup_bleep = 1;
    amy_config.ram_caps_events = MALLOC_CAP_SPIRAM;
    amy_config.ram_caps_synth = MALLOC_CAP_SPIRAM;
    amy_config.ram_caps_delay = MALLOC_CAP_SPIRAM;
    amy_config.ram_caps_sample = MALLOC_CAP_SPIRAM;
    amy_config.ram_caps_sysex = MALLOC_CAP_SPIRAM;
    // Must be set here too, not just implied by ram_caps_events: AMY derives
    // ram_caps_oscs from ram_caps_events *inside* amy_default_config() (api.c),
    // which already ran above, so the assignments here never reach it. Without
    // this the per-osc synthinfo/mod_synthinfo/breakpoint allocation -- max_oscs
    // (250) of them -- stays in internal RAM, which the display and USB host
    // need. The S3 gets PSRAM for it from api.c's TULIP branch.
    amy_config.ram_caps_oscs = MALLOC_CAP_SPIRAM;
    // Lets Python register callbacks on the sequencer clock (tulip.seq_add_callback,
    // sequencer.TulipSequence). AMY calls this from this task once per tick; the
    // hook itself does nothing until a callback has been registered.
    amy_config.amy_external_sequencer_hook = tulip_amy_sequencer_hook;
    // Makes MIDI arriving on the USB-A port visible to Python: queues it for
    // tulip.midi_in()/sysex_in() and fires tulip.midi_callback(), which is what
    // midi.py listens on.
    amy_config.amy_external_midi_input_hook = tulip_amy_midi_hook;
    // The other end of that: everything AMY sends goes out the USB-A port.
    // amy_config.midi is AMY_MIDI_IS_NONE, so AMY's own midi_out() has no device
    // interface to write to and this hook is the only route off the board.
    // Without it AMY's replies are dropped -- the zI identity ping, and the
    // sysex ACK that flow-controls a sender through tulip_amy_send_sysex().
    amy_config.amy_external_midi_output_hook = send_usb_midi_out;
    // File I/O over MicroPython's VFS, so AMY's `zL` (load a PCM preset from a
    // file) and `zT` file transfer work instead of bailing with "fopen hook not
    // enabled on platform". Definitions come from shared/amy_file_hooks.inc via
    // modtulip_tab5.c. These run MicroPython calls, so they are only safe
    // because every path that parses a wire message on this board now lands on
    // the MP thread: amy.send() from Python is already there, and sysex is
    // deferred through parse_sysex()'s copy-slot ring (amy_midi.c names TAB5)
    // into tulip_amy_send_sysex(). The sequencer replays pre-parsed events and
    // never re-enters the parser. The one hole is cv_trigger.c, which parses
    // from the render path -- unreachable without amy_external_coef_hook, which
    // is not installed below, though tulip.amy_set_cv_from_osc() would. Tulip CC
    // has the same hole; see the board README.
    amy_config.amy_external_fopen_hook = mp_fopen_hook;
    amy_config.amy_external_fseek_hook = mp_fseek_hook;
    amy_config.amy_external_fclose_hook = mp_fclose_hook;
    amy_config.amy_external_fread_hook = mp_fread_hook;
    amy_config.amy_external_fwrite_hook = mp_fwrite_hook;
    // Tells Python when AMY's CPU overload failsafe trips, so a sketch can react
    // instead of the audio just cutting out. AMY has already reset the synth by
    // then; this only reports.
    amy_config.amy_external_overload_hook = tulip_amy_overload_hook;
#ifdef TULIP_USER_C_DSP
    // User C DSP (tulip.install_c_process / install_c_osc): installed effects
    // run on a bus after its FX chain, installed oscillators replace a bound
    // osc's waveform. Both run here on the render task, and both do nothing
    // until something is installed. The render hook needs no chaining with CV
    // output the way the S3's does -- this board installs no other render hook.
    amy_config.amy_external_bus_postprocess_hook = tulip_bus_postprocess_hook;
    amy_config.amy_external_render_hook = tulip_user_render_hook;
#endif
    amy_start(amy_config);

    // The core-1 render helper. Created before the audio task so that
    // tab5_render_block() never sees a half-built one; if either allocation
    // fails the handle stays NULL and every block renders on the audio task,
    // which is just the single-core path.
    s_audible_max = amy_config.max_oscs;
    s_audible = heap_caps_malloc(sizeof(uint16_t) * s_audible_max, MALLOC_CAP_INTERNAL);
    s_cut_ok = heap_caps_malloc(s_audible_max, MALLOC_CAP_INTERNAL);
    s_render_helper_done = xSemaphoreCreateBinary();
    if (s_audible == NULL || s_cut_ok == NULL || s_render_helper_done == NULL) {
        ESP_LOGW(TAG, "No dual-core render: out of internal RAM");
    } else if (xTaskCreatePinnedToCore(
                   tab5_render_helper_task,
                   "tab5_render1",
                   TAB5_RENDER_HELPER_STACK_WORDS,
                   NULL,
                   TAB5_RENDER_HELPER_PRIORITY,
                   &s_render_helper,
                   TAB5_RENDER_HELPER_CORE) != pdPASS) {
        s_render_helper = NULL;
        ESP_LOGW(TAG, "No dual-core render: helper task would not start");
    } else {
        fprintf(stderr, "AMY render helper on core %d\n", TAB5_RENDER_HELPER_CORE);
        fflush(stderr);
    }

    BaseType_t task_result = xTaskCreatePinnedToCore(
        tab5_audio_task,
        "tab5_audio",
        TAB5_AUDIO_TASK_STACK_WORDS,
        NULL,
        TAB5_AUDIO_TASK_PRIORITY,
        NULL,
        TAB5_AUDIO_TASK_CORE);
    if (task_result != pdPASS) {
        ESP_LOGE(TAG, "Failed to create audio render task");
        return;
    }

    s_audio_ready = true;
    ESP_LOGI(TAG, "AMY audio initialized at %d Hz stereo, volume %d",
             AMY_SAMPLE_RATE, TAB5_SPEAKER_VOLUME);
}

bool tab5_audio_ready(void)
{
    return s_audio_ready;
}

bool tab5_audio_set_speaker_volume(int volume)
{
    if (s_speaker == NULL) {
        return false;
    }
    if (volume < 0) volume = 0;
    if (volume > 100) volume = 100;
    int codec_result = esp_codec_dev_set_out_vol(s_speaker, volume);
    if (codec_result != ESP_CODEC_DEV_OK) {
        ESP_LOGW(TAG, "Speaker volume %d rejected: %d", volume, codec_result);
        return false;
    }
    return true;
}

// The 3.5mm jack's insert-detect contact. The jack is 4-pole: tip and ring 1
// carry HPOUT_L/HPOUT_R from the ES8388, ring 2 carries a headset mic into the
// ES7210's MIC4 (wired, but impossible to read back -- the microphone section
// of docs/tulip_api.md has the why), and the detect contact reaches P7 of the first
// IO expander (PI4IOE5V6408 at 0x43 -- the same part the BSP uses for the
// speaker, LCD and camera enables) through the R49/R85 divider.
//
// Two things about that pin had to be measured, neither of them guessable from
// the BSP, and the line reads 0 forever if either is missed. First, the
// expander driver's reset() leaves *every* pin an output (it writes DIR 0xff,
// whose comment says "all inputs" but whose part reads 1 as output), so P7 has
// to be claimed back as an input. Second, that same reset turns the internal
// pull-down on for every pin, and the board only drives this line hard in one
// direction: empty, it sits at ground; with a plug in, it is pulled up through
// the R49/R85 divider, weakly enough that the expander's own pull-down wins and
// the pin still reads 0. Disabling the pull is what makes the plug visible.
#define TAB5_HP_DET_PIN IO_EXPANDER_PIN_NUM_7

bool tab5_audio_jack_level(bool *level)
{
    static bool configured;
    esp_io_expander_handle_t io = bsp_io_expander_init();
    if (io == NULL) {
        return false;
    }
    if (!configured) {
        if (esp_io_expander_set_dir(io, TAB5_HP_DET_PIN, IO_EXPANDER_INPUT) != ESP_OK ||
            esp_io_expander_set_pullupdown(io, TAB5_HP_DET_PIN, IO_EXPANDER_PULL_NONE) != ESP_OK) {
            return false;
        }
        // The divider needs a moment to take the pin wherever it is going once
        // the internal pull lets go of it.
        vTaskDelay(pdMS_TO_TICKS(5));
        configured = true;
    }
    uint32_t mask = 0;
    if (esp_io_expander_get_level(io, TAB5_HP_DET_PIN, &mask) != ESP_OK) {
        return false;
    }
    *level = (mask & TAB5_HP_DET_PIN) != 0;
    return true;
}

bool tab5_audio_jack_present(void)
{
    bool level = false;
    if (!tab5_audio_jack_level(&level)) {
        return false;
    }
    return level;
}

int tab5_audio_get_speaker_volume(void)
{
    // esp_codec_dev keeps the last value written (esp_codec_dev.c's get_out_vol
    // returns dev->volume), so this reads back whatever was set, not the part.
    int volume = TAB5_SPEAKER_VOLUME;
    if (s_speaker != NULL && esp_codec_dev_get_out_vol(s_speaker, &volume) != ESP_CODEC_DEV_OK) {
        volume = TAB5_SPEAKER_VOLUME;
    }
    return volume;
}

void tab5_audio_get_stats(tab5_audio_stats_t *stats)
{
    *stats = s_audio_stats;
}

void tab5_audio_reset_stats(void)
{
    memset((void *)&s_audio_stats, 0, sizeof(s_audio_stats));
}
