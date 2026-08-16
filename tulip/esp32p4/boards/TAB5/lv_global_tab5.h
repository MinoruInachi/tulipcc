#ifndef LV_GLOBAL_TAB5_H
#define LV_GLOBAL_TAB5_H

/*
 * Hand LVGL's global state to the MicroPython GC, which is what
 * lv_binding_micropython_tulip/lv_conf.h does for every other Tulip target.
 * TAB5 is built with LV_CONF_SKIP and configures LVGL from Kconfig, so this
 * header is pulled in through CONFIG_LV_GLOBAL_CUSTOM_INCLUDE instead.
 *
 * Without it LVGL keeps its own `lv_global` in BSS, which the collector never
 * scans. Everything LVGL allocates then looks unreachable, and once
 * lv_malloc_core() serves from the GC heap (see lv_mem_tab5.c) the collector
 * frees live widgets out from under LVGL. Together the two files restore the
 * binding's intended ownership chain:
 *
 *     GC root -> lv_global_t -> lv_display_t -> screen -> child lv_obj_t
 *             -> lv_obj->user_data -> mp_lv_obj_t
 *
 * lv_init() calls LV_GC_INIT() before it first dereferences
 * LV_GLOBAL_DEFAULT(), which is what allocates and roots the global.
 */

extern void mp_lv_init_gc(void);
#define LV_GC_INIT() mp_lv_init_gc()

extern void *mp_lv_roots;
#define LV_GLOBAL_CUSTOM() ((lv_global_t *)mp_lv_roots)

#endif /* LV_GLOBAL_TAB5_H */
