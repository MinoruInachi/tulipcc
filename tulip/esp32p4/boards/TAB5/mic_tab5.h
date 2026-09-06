// Tab5 built-in dual microphone. See mic_tab5.c for the shape of the API.
//
// The Tab5 carries two analog MEMS mics fed into an ES7210 4-channel ADC that
// shares the ESP32-P4's single I2S controller with the ES8388 speaker, in full
// duplex: bsp_audio_init() (run from tab5_audio_init()) already brought up both
// the TX and RX halves of that controller at AMY_SAMPLE_RATE / 16-bit / stereo
// for AMY playback, so the mic RX path is clocked off the very same BCLK/WS as
// the speaker. That is the reason the sample rate here is fixed at
// AMY_SAMPLE_RATE and the format at 16-bit stereo: the two mics are the two I2S
// slots, and reconfiguring the clock to record at some other rate would pull it
// out from under the speaker mid-block. Software can decimate a copy afterwards
// if it wants a lower rate.
//
// The ES7210 itself is programmed lazily, on the first tab5_mic_start(), so the
// shared audio bus is only touched once a caller has actually asked to record.
// A capture task on core 0 (beside the display, touch and camera tasks, whose
// interrupts already live there) blocks in esp_codec_dev_read() and pushes each
// block into a PSRAM ring under a lock; a reader on the MicroPython task takes
// the lock, copies out whatever frames it wants, and lets go -- it never waits
// on the ADC, and if it falls behind the ring drops its oldest frames and counts
// the overrun rather than stalling the capture task.
#pragma once

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

// Fixed by the shared full-duplex I2S: two mics == two 16-bit slots.
#define TAB5_MIC_CHANNELS 2
#define TAB5_MIC_BITS 16

typedef struct {
    bool initialized;      // the ES7210 was programmed at least once
    bool running;          // capture task alive, ring filling
    uint32_t sample_rate;  // Hz, == AMY_SAMPLE_RATE
    uint8_t channels;      // == TAB5_MIC_CHANNELS
    uint8_t bits;          // == TAB5_MIC_BITS
    int gain;              // ADC input gain in dB, as last set
    uint32_t blocks;       // capture blocks read since the last start
    uint32_t read_errors;  // esp_codec_dev_read() failures
    uint32_t overruns;     // frames dropped because a reader fell behind
    uint32_t available;    // frames currently sitting in the ring
    uint32_t capacity;     // ring size in frames
    uint32_t peak_left;    // last block's peak |sample|, mic 0 (0..32767)
    uint32_t peak_right;   // last block's peak |sample|, mic 1 (0..32767)
} tab5_mic_info_t;

// Program the ES7210 (once), open the codec for reading and start the capture
// task. Idempotent while running: a second call only re-applies the gain.
// `gain_db` is the ADC input gain, clamped to the ES7210's 0..37.5 dB range.
esp_err_t tab5_mic_start(int gain_db);

// Stop the capture task and close the codec. The ES7210 stays programmed, so
// the next start is quick. The shared I2S controller is left up for the speaker.
esp_err_t tab5_mic_stop(void);

bool tab5_mic_running(void);
void tab5_mic_info(tab5_mic_info_t *out);

// Pull up to `max_frames` interleaved stereo frames (L,R int16 each) out of the
// ring into `dst`, which must hold max_frames * TAB5_MIC_CHANNELS int16s. If the
// ring is empty this blocks up to `timeout_ms` for the first frame to arrive
// (0 returns immediately). Returns the number of frames written.
size_t tab5_mic_read(int16_t *dst, size_t max_frames, uint32_t timeout_ms);

// Frames currently available to read without blocking.
size_t tab5_mic_available_frames(void);

// Discard everything buffered (e.g. before a fresh recording).
void tab5_mic_flush(void);

// Recent per-mic peak level, 0.0..1.0, from the last captured block. Cheap: no
// data is copied. Either pointer may be NULL.
void tab5_mic_levels(float *out_left, float *out_right);

// Re-apply the ADC input gain (dB) while running.
esp_err_t tab5_mic_set_gain(int gain_db);

#ifdef __cplusplus
}
#endif
