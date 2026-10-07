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
	uint32_t max_dsp_us;      /* amy_render_audio() alone */
} tab5_audio_stats_t;

void tab5_audio_init(void);
bool tab5_audio_ready(void);
// The codec's output gain, 0..100. What tulip.speaker_volume() reads and writes;
// turning it down is how you keep a loud patch from browning the board out on a
// current-limited supply. The value is clamped, and false means the codec is not
// up (or refused it) -- nothing else reports a failure.
bool tab5_audio_set_speaker_volume(int volume);
int tab5_audio_get_speaker_volume(void);
void tab5_audio_get_stats(tab5_audio_stats_t *stats);
void tab5_audio_reset_stats(void);
