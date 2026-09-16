/* Alpha-keyed blend on the P4's PIE unit, with a self-test that has to agree
 * with the scalar loop before the vector path is used at all.
 *
 * The self-test is not ceremony. Espressif documents the PIE instructions
 * thinly, and this code assumes one specific thing about esp.vcmp.eq.s16: that
 * it leaves all-ones in the lanes that matched and zeroes in the rest, so that
 * the select can be done with and/andnot/or. If that assumption is wrong the
 * blend would be subtly wrong -- wrong pixels, not a crash -- and the screen is
 * the last place to find that out. So the assumption is checked against the
 * scalar loop on real data at startup, and the vector path stays off unless the
 * two agree exactly.
 */

#include "pie_blend_tab5.h"

#include <string.h>

/* In pie_blend_tab5.S. */
extern void tab5_pie_blend_vec(uint16_t *dst, const uint16_t *src, uint32_t blocks,
                               const uint16_t *key8);

static bool s_pie_ok = false;

static void blend_scalar(uint16_t *dst, const uint16_t *src, uint32_t n, uint16_t key)
{
    for (uint32_t i = 0; i < n; i++) {
        if (src[i] != key) {
            dst[i] = src[i];
        }
    }
}

#define PIE_SELFTEST_PX 64

void tab5_pie_init(void)
{
    static uint16_t src[PIE_SELFTEST_PX] __attribute__((aligned(16)));
    static uint16_t want[PIE_SELFTEST_PX] __attribute__((aligned(16)));
    static uint16_t got[PIE_SELFTEST_PX] __attribute__((aligned(16)));
    static uint16_t key8[8] __attribute__((aligned(16)));

    /* Any value will do as the key here; the real one is passed in per call. */
    const uint16_t key = 0x4daa;
    uint32_t r = 2463534242u;

    for (int i = 0; i < PIE_SELFTEST_PX; i++) {
        /* xorshift, so the mix of keyed and unkeyed pixels is not a pattern
         * that a broken mask could satisfy by accident. */
        r ^= r << 13; r ^= r >> 17; r ^= r << 5;
        uint16_t v = (uint16_t)(r >> 8);
        if (v == key) {
            v ^= 1u;
        }
        src[i] = ((r & 3u) == 0) ? key : v;
        want[i] = got[i] = (uint16_t)(0xf000u | (unsigned)i);
    }
    for (int i = 0; i < 8; i++) {
        key8[i] = key;
    }

    blend_scalar(want, src, PIE_SELFTEST_PX, key);
    tab5_pie_blend_vec(got, src, PIE_SELFTEST_PX / 8, key8);

    s_pie_ok = (memcmp(want, got, sizeof(want)) == 0);
}

bool tab5_pie_ok(void)
{
    return s_pie_ok;
}

/* The key repeated across a vector, rebuilt only when it changes -- it never
 * does in practice, but a static that silently disagreed with the caller would
 * be the worst kind of bug to chase. */
static uint16_t s_key8[8] __attribute__((aligned(16)));
static uint16_t s_key8_val;
static bool s_key8_valid = false;

void tab5_pie_blend(uint16_t *dst, const uint16_t *src, uint32_t n, uint16_t key)
{
    const uint32_t blocks = n / 8u;
    /* 16-byte alignment is the vector load's requirement. Rows start on it --
     * the chunk buffers are cache-line aligned and a row is 2560 bytes -- but a
     * caller blending a sub-span (the LVGL overlay does) can hand over anything.
     */
    const bool aligned = ((((uintptr_t)dst) | ((uintptr_t)src)) & 15u) == 0u;

    if (!s_pie_ok || blocks == 0u || !aligned) {
        blend_scalar(dst, src, n, key);
        return;
    }

    if (!s_key8_valid || s_key8_val != key) {
        for (int i = 0; i < 8; i++) {
            s_key8[i] = key;
        }
        s_key8_val = key;
        s_key8_valid = true;
    }

    tab5_pie_blend_vec(dst, src, blocks, s_key8);

    const uint32_t done = blocks * 8u;
    if (done < n) {
        blend_scalar(dst + done, src + done, n - done, key);
    }
}
