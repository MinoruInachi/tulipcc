#include <stdbool.h>
#include <string.h>

#include "esp_log.h"
#include "esp_task.h"
#include "esp_codec_dev.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "bsp/m5stack_tab5.h"
#include "../../../../amy/src/amy.h"

#include "audio_tab5.h"
// tulip_amy_midi_hook() -- queues MIDI in for Python.
#include "modtulip_tab5.h"
// tulip_amy_sequencer_hook() -- fans a sequencer tick out to Python callbacks.
#include "tsequencer_tab5.h"

static const char *TAG = "TAB5-AUDIO";
static esp_codec_dev_handle_t s_speaker;
static bool s_audio_ready;
static volatile tab5_audio_stats_t s_audio_stats;

#define TAB5_AUDIO_TASK_STACK_WORDS (16 * 1024 / sizeof(StackType_t))
/* This task is the render half of AMY's multicore split: the other half runs in
 * AMY's own esp_render_task at ESP_TASK_PRIO_MAX - 1 on core 0, and
 * esp_render_on_cores() blocks until both halves finish. At tskIDLE_PRIORITY + 2
 * this half sat below nearly every IDF service task on core 1 (esp_timer,
 * esp-hosted, USB), so a block's render time was really "render time plus
 * whatever preempted us" -- which is where the multi-millisecond spikes and the
 * audible dropouts came from. AMY runs its equivalent loop at the same priority
 * used here; it is safe because the loop always blocks in the codec write. */
#define TAB5_AUDIO_TASK_PRIORITY (ESP_TASK_PRIO_MAX - 1)
// Core 0. Core 1 runs MicroPython, and this task sits at the top of the priority
// range: leaving it there starved mp_task (and the idle task, which trips the
// task watchdog) whenever AMY ran long. Core 0 only carries the display and touch
// tasks, both of which are far lower priority and tolerate preemption.
#define TAB5_AUDIO_TASK_CORE 0
#define TAB5_SPEAKER_VOLUME 100

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

        amy_execute_deltas();
        const int64_t deltas_done = esp_timer_get_time();
        int16_t *samples = amy_render_audio();
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

        for (size_t sample = 0; sample < AMY_BLOCK_SIZE * AMY_NCHANS; sample++) {
            int32_t value = samples[sample];
            uint32_t magnitude = (uint32_t)(value < 0 ? -value : value);
            if (magnitude > s_audio_stats.peak_sample) {
                s_audio_stats.peak_sample = magnitude;
            }
            if (value == INT16_MIN || value == INT16_MAX) {
                s_audio_stats.clipped_samples++;
            }
        }

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
    // Measured on Tab5, not assumed: AMY's ESP multicore path splits rendering by
    // a static osc index range (0..max_oscs/2 on core 0, the rest here) and blocks
    // on a task notify round-trip every block. Typical Tulip workloads pack their
    // audible oscs into the low indices, so core 0 does nearly all the work while
    // this task waits -- and the handshake cost dwarfs anything it saves. With it
    // off, 16 voices went from 2418us to 401us peak render, and xanadu.py's worst
    // block from 26.4ms to 8.1ms (the block budget is 5.8ms).
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
    amy_start(amy_config);

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

void tab5_audio_get_stats(tab5_audio_stats_t *stats)
{
    *stats = s_audio_stats;
}

void tab5_audio_reset_stats(void)
{
    memset((void *)&s_audio_stats, 0, sizeof(s_audio_stats));
}
