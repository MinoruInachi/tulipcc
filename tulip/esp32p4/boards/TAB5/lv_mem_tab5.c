#include <string.h>

#include "lvgl.h"

#include "py/gc.h"
#include "py/misc.h"

/*
 * LVGL allocates out of the MicroPython GC heap, exactly like every other Tulip
 * target does through LV_USE_STDLIB_MALLOC = LV_STDLIB_MICROPYTHON in
 * lv_binding_micropython_tulip/lv_conf.h. TAB5 is built with LV_CONF_SKIP and
 * takes its LVGL settings from Kconfig, which offers no MicroPython option --
 * only builtin/clib/custom -- so we select CONFIG_LV_USE_CUSTOM_MALLOC and
 * reimplement that same policy here.
 *
 * This is not a tuning choice, it is a correctness requirement. The lvgl
 * MicroPython binding roots lv_global_t (mp_lv_roots) in the GC heap and reaches
 * the Python-side wrapper of every widget as
 *
 *     root -> lv_global_t -> lv_display_t -> screen -> child lv_obj_t
 *          -> lv_obj->user_data -> mp_lv_obj_t
 *
 * Every hop but the last is memory that LVGL allocated. Serving those from
 * heap_caps_malloc puts them outside the GC heap, the collector cannot follow
 * the chain, and any widget whose only remaining reference is LVGL's own
 * user_data pointer gets collected. Drums registers its CLICKED handler on a
 * local `bottom_rect` that __init__ drops, so releasing a drum switch had LVGL
 * call back through a dangling user_data and panic with a load access fault
 * inside get_native_obj().
 *
 * Consequence: lv_malloc/lv_free must only ever be reached from the MicroPython
 * task. That already holds -- lv_task_handler() runs only from the
 * mp_sched_schedule() callback in modtulip_tab5.c, and setup_lvgl() runs from
 * tulip.ui_init().
 */

void lv_mem_init(void)
{
}

void lv_mem_deinit(void)
{
}

lv_mem_pool_t lv_mem_add_pool(void *mem, size_t bytes)
{
    (void)mem;
    (void)bytes;
    return NULL;
}

void lv_mem_remove_pool(lv_mem_pool_t pool)
{
    (void)pool;
}

void *lv_malloc_core(size_t size)
{
    return m_malloc(size);
}

void *lv_realloc_core(void *ptr, size_t size)
{
    return m_realloc(ptr, size);
}

void lv_free_core(void *ptr)
{
    m_free(ptr);
}

void lv_mem_monitor_core(lv_mem_monitor_t *monitor)
{
    gc_info_t info;
    gc_info(&info);

    memset(monitor, 0, sizeof(*monitor));
    /* gc_info() reports used/free in bytes but max_free in blocks. It has no
     * notion of an allocation count, so free_cnt/used_cnt stay zero rather than
     * carrying a number that means something else. */
    monitor->total_size = info.total;
    monitor->free_size = info.free;
    monitor->free_biggest_size = info.max_free * MICROPY_BYTES_PER_GC_BLOCK;
    monitor->max_used = info.used;
    if (monitor->total_size != 0) {
        monitor->used_pct = 100 - (monitor->free_size * 100 / monitor->total_size);
    }
    if (monitor->free_size != 0) {
        monitor->frag_pct = 100 - (monitor->free_biggest_size * 100 / monitor->free_size);
    }
}

lv_result_t lv_mem_test_core(void)
{
    return LV_RESULT_OK;
}
