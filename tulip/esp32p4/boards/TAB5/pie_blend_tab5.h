#pragma once

#include <stdbool.h>
#include <stdint.h>

/* Alpha-keyed blend, vectorised on the P4's PIE unit. See pie_blend_tab5.c. */

/* Run the self-test and decide whether the vector path may be used. Call once,
 * from the task that will do the blending (it must be pinned to a core, and
 * must not be an ISR). */
void tab5_pie_init(void);

/* Whether the self-test agreed with the scalar reference. */
bool tab5_pie_ok(void);

/* dst[i] = (src[i] == key) ? dst[i] : src[i], for n pixels. Always correct:
 * falls back to the scalar loop when the vector path is unavailable or the
 * buffers are not shaped for it. */
void tab5_pie_blend(uint16_t *dst, const uint16_t *src, uint32_t n, uint16_t key);
