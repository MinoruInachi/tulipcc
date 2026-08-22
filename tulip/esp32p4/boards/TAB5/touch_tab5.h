#pragma once

#include <stdint.h>

#include "esp_err.h"

esp_err_t tab5_touch_init(void);
void run_tab5_touch(void *param);
uint32_t tab5_touch_task_entries(void);
uint32_t tab5_touch_poll_count(void);
uint32_t tab5_touch_down_count(void);
uint32_t tab5_touch_read_errors(void);

/* ESP_OK once the controller is up, ESP_ERR_INVALID_STATE before the touch
 * task has tried. Reported through tulip.tab5_diag(): a touch controller
 * that never initialized is otherwise indistinguishable from a screen
 * nobody touched, and the log line that says so does not reliably survive
 * the USB-Serial-JTAG console. */
int tab5_touch_init_error(void);

/* How many creates the controller needed. More than one says it was still
 * waking up when the touch task first asked -- which only happens on the
 * first boot after the rail was actually off. */
uint32_t tab5_touch_init_attempts(void);
