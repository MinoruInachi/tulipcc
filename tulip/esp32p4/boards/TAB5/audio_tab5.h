#pragma once

#include <stdint.h>

typedef struct {
	uint32_t blocks;
	uint32_t interval_overruns;
	uint32_t render_overruns;
	uint32_t write_errors;
	uint32_t max_interval_us;
	uint32_t max_render_us;
	uint32_t max_write_us;
	uint32_t clipped_samples;
	uint32_t peak_sample;
	uint32_t max_deltas_us;   /* amy_execute_deltas() alone */
	uint32_t max_dsp_us;      /* the whole render, both halves */
	uint32_t helper_blocks;   /* blocks that really split across two cores */
	uint32_t max_helper_us;   /* the core-1 half alone, to see the imbalance */
	uint32_t max_notify_us;   /* notify -> the helper actually starting */
	uint32_t max_join_us;     /* waiting for the helper after our own half */
	uint32_t helper_stalls;   /* joins that stood the split down (flash I/O) */
} tab5_audio_stats_t;

void tab5_audio_init(void);
bool tab5_audio_ready(void);
// The codec's output gain, 0..100. What tulip.speaker_volume() reads and writes;
// turning it down is how you keep a loud patch from browning the board out on a
// current-limited supply. The value is clamped, and false means the codec is not
// up (or refused it) -- nothing else reports a failure.
bool tab5_audio_set_speaker_volume(int volume);
int tab5_audio_get_speaker_volume(void);
// Is something plugged into the 3.5mm jack? Read from the board's
// headphone-detect line; see audio_tab5.c for where that line comes from and
// what had to be measured to read it. A line that cannot be read at all reports
// as "nothing plugged in"; tab5_audio_jack_level() is the pin itself, and
// returns false in that case instead.
bool tab5_audio_jack_present(void);
bool tab5_audio_jack_level(bool *level);

void tab5_audio_get_stats(tab5_audio_stats_t *stats);
void tab5_audio_reset_stats(void);

// Render a block's oscillators across both cores (see audio_tab5.c). This is
// not AMY's own config.platform.multicore -- that pins its helper to the core
// this board already renders on, so it never split anything. What
// tulip.amy_dual_core() reads and writes: 0 off, 1 always, 2 (the default) only
// while the render is expensive enough to be worth taking core 1 from
// MicroPython. Off, the whole block renders on the audio task, which is how the
// board behaved before.
void tab5_audio_set_dual_core(int mode);
int tab5_audio_dual_core(void);
