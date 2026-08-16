#pragma once

#include "esp_err.h"

esp_err_t tab5_touch_init(void);
void run_tab5_touch(void *param);
uint32_t tab5_touch_task_entries(void);
uint32_t tab5_touch_poll_count(void);
uint32_t tab5_touch_down_count(void);
uint32_t tab5_touch_read_errors(void);
