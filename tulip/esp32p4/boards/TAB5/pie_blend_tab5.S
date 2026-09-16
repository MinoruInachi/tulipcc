/* Alpha-keyed blend on the ESP32-P4's PIE unit (128-bit SIMD), Tab5 only.
 *
 * The compositor's overlay step is "copy src over dst, except where src is the
 * transparent key" -- once per pixel, for up to 1280 pixels a row and 720 rows
 * a frame. Measured on device, that step costs ~27 ms a frame with the terminal
 * full of text, against ~6.6 ms to memcpy the same 1.84 MB: four times the cost
 * of moving the bytes, so most of it is the per-pixel loop rather than the
 * memory, and worth handing to the vector unit.
 *
 * .option arch enables the extension for this file alone, so nothing in the
 * build system has to know about it. The assembler spells it xespv here even
 * though the -march name is xesppie.
 *
 * Only ever call this from a task, and only from one pinned to a core: the
 * FreeRTOS port saves and restores q0-q7 lazily, per pinned task, and panics
 * outright ("Coprocessors must not be used in ISRs!") if a coprocessor is
 * touched from an interrupt. On Tab5 the compositor runs in the display task,
 * which is pinned to core 0; on the ESP32-S3 the same compositor runs in an
 * ISR, which is why none of this is shared code.
 */
	.option arch, +xespv
	.text
	.align 2
	.global tab5_pie_blend_vec
	.type tab5_pie_blend_vec,@function

/* void tab5_pie_blend_vec(uint16_t *dst, const uint16_t *src, uint32_t blocks,
 *                         const uint16_t *key8);
 *
 *   a0  dst, 16-byte aligned, read and written
 *   a1  src, 16-byte aligned
 *   a2  number of 8-pixel blocks, must be >= 1
 *   a3  8 copies of the transparent key, 16-byte aligned
 *
 * The caller guarantees the alignment and the block count; the C wrapper falls
 * back to a scalar loop for anything that does not fit.
 */
tab5_pie_blend_vec:
	esp.vld.128.ip	q1, a3, 0		/* q1 = the key, in all 8 lanes */
1:
	esp.vld.128.ip	q0, a1, 16		/* q0 = 8 source pixels */
	esp.vld.128.ip	q3, a0, 0		/* q3 = the 8 pixels already there */
	esp.vcmp.eq.s16	q2, q0, q1		/* q2 = lane mask, src == key */
	esp.andq	q4, q2, q3		/* where src is the key: keep dst */
	esp.notq	q5, q2
	esp.andq	q5, q5, q0		/* everywhere else: take src */
	esp.orq		q4, q4, q5
	esp.vst.128.ip	q4, a0, 16
	addi	a2, a2, -1
	bnez	a2, 1b
	ret
	.size tab5_pie_blend_vec, .-tab5_pie_blend_vec
