set(IDF_TARGET esp32p4)

# The ESP32-P4/TAB5 path is a scaffold only for now.
# Keep the board split explicit so the future port can add display/touch/audio
# sources without disturbing the current ESP32-S3 tree.

set(MICROPY_PY_TINYUSB ON)

get_filename_component(AMY_DIR ${MICROPY_BOARD_DIR}/../../../../amy ABSOLUTE)

# ulab (numpy/scipy subset) as a MicroPython user C module. Built in rather than
# passed on the idf.py command line so a plain `idf.py -DMICROPY_BOARD=TAB5
# build` gets it; -DUSER_C_MODULES=... on the command line still wins.
get_filename_component(ULAB_DIR ${MICROPY_BOARD_DIR}/../../../../ulab ABSOLUTE)
if(NOT USER_C_MODULES AND EXISTS ${ULAB_DIR}/code/micropython.cmake)
    set(USER_C_MODULES ${ULAB_DIR}/code/micropython.cmake)
endif()

set(BOARD_DEFINITION1 TAB5)
set(BOARD_DEFINITION2 TAB5)

add_compile_definitions(TAB5)
add_compile_definitions(STATIC=static)
add_compile_definitions(MALLOC_CAPS_DEFINED)
# Bake AMY's TR-808 ROM sample set (amy/src/pcm_gamma808.h) instead of the
# 11-sample pcm_tiny default, matching the ESP32-S3 tree (its esp32_common.cmake
# defines GAMMA9001 the same way). Costs ~268 KB of the app partition -- the ROM
# blob goes from 51053 to 188358 frames -- and takes the built-in PCM set from 11
# samples to 19, so the GM drum synth on channel 10 comes up as a real 808 kit
# (patch 384) rather than the tiny drum patch 258.
#
# The other half of GAMMA9001 on the S3 -- the 136 extra bank presets at 256+ --
# streams from a 3.7 MB 'drums' flash partition that TAB5 does not have and
# cannot afford (see partitions-8MiBplus-ota.csv). That is fine: patch 384 only
# references ROM presets 0..18, so the default kit is complete without it, and
# pcm.c guards every bank lookup on `gamma9001_pcm != NULL`. Presets 256+ fall
# back to preset 0 until something calls amy_set_gamma9001_pcm().
add_compile_definitions(GAMMA9001)
list(APPEND MICROPY_CPP_FLAGS_EXTRA -DLV_CONF_SKIP)
# The qstr extraction pass preprocesses the source list with MICROPY_CPP_FLAGS,
# which unlike the real compile does not inherit ESP_PLATFORM from the IDF
# toolchain. shared/keyscan.c reaches for <SDL.h> without it (that is its
# desktop branch), so the pass has to see the same platform the compiler does.
# The ESP32-S3 tree does this too, via target_compile_options in
# esp32_common.cmake.
#
# TAB5 goes with it: add_compile_definitions(TAB5) above only reaches the
# compiler, and shared/display.h keys off the ESP_PLATFORM/TAB5 pair to decide
# between the S3's RGB-panel header and this board's DSI backend.
list(APPEND MICROPY_CPP_FLAGS_EXTRA -DESP_PLATFORM -DTAB5)
# GAMMA9001 goes with them for the same reason: modtulip_tab5.c guards its
# tulip.gamma9001_load() module-table entry on it, and a qstr the extraction
# pass never sees is a qstr the compiler cannot resolve.
list(APPEND MICROPY_CPP_FLAGS_EXTRA -DGAMMA9001)

set(SDKCONFIG_DEFAULTS
    boards/TAB5/sdkconfig.board
)

list(APPEND MICROPY_QSTRDEFS_PORT
    ${MICROPY_BOARD_DIR}/../../qstrdefsport.h
)

set(MICROPY_SOURCE_BOARD
    ${CMAKE_BINARY_DIR}/esp-idf/main/tab5_lv_mpy.c
    ${MICROPY_BOARD_DIR}/modtulip_tab5.c
    ${MICROPY_BOARD_DIR}/tsequencer_tab5.c
    ${MICROPY_BOARD_DIR}/display_tab5.c
    ${MICROPY_BOARD_DIR}/lv_mem_tab5.c
    ${MICROPY_BOARD_DIR}/../../../shared/display.c
    ${MICROPY_BOARD_DIR}/../../../shared/lodepng.c
    ${MICROPY_BOARD_DIR}/../../../shared/smallfont.c
    ${MICROPY_BOARD_DIR}/../../../shared/bigfont.c
    # keyscan.c carries the USB HID scan-code decoder that usb_host_tab5.c needs,
    # and with it the CP437 converter display.c calls. That converter used to be
    # duplicated in shared/utf_cp437.c, written only because this file was not
    # built here; the two were the same code down to the tables.
    ${MICROPY_BOARD_DIR}/../../../shared/keyscan.c
    ${MICROPY_BOARD_DIR}/../../../shared/bresenham.c
    ${MICROPY_BOARD_DIR}/../../../shared/polyfills.c
    ${MICROPY_BOARD_DIR}/../../../shared/tulip_helpers.c
    ${MICROPY_BOARD_DIR}/../../../shared/editor.c
    ${MICROPY_BOARD_DIR}/../../../shared/ui.c
    ${MICROPY_BOARD_DIR}/../../../shared/lvgl_u8g2.c
    ${MICROPY_BOARD_DIR}/../../../shared/u8fontdata.c
    ${MICROPY_BOARD_DIR}/../../../shared/u8fontdata_jp.c
    ${MICROPY_BOARD_DIR}/../../../shared/jpfont.c
    ${MICROPY_BOARD_DIR}/../../../shared/u8g2_fonts.c
    ${MICROPY_BOARD_DIR}/shared_renderer_tab5_glue.c
    ${MICROPY_BOARD_DIR}/audio_tab5.c
    ${MICROPY_BOARD_DIR}/touch_tab5.c
    ${MICROPY_BOARD_DIR}/keyboard_tab5.c
    ${MICROPY_BOARD_DIR}/usb_host_tab5.c
    ${MICROPY_BOARD_DIR}/camera_tab5.c
    ${MICROPY_BOARD_DIR}/mic_tab5.c
    ${MICROPY_BOARD_DIR}/imu_tab5.c
    ${MICROPY_BOARD_DIR}/tab5_revision.c
    ${MICROPY_BOARD_DIR}/power_tab5.c
    ${MICROPY_BOARD_DIR}/storage_tab5.c
    ${MICROPY_BOARD_DIR}/tab5_startup.c
    ${AMY_DIR}/src/algorithms.c
    ${AMY_DIR}/src/custom.c
    ${AMY_DIR}/src/patches.c
    ${AMY_DIR}/src/amy.c
    ${AMY_DIR}/src/delay.c
    ${AMY_DIR}/src/envelope.c
    ${AMY_DIR}/src/filters.c
    ${AMY_DIR}/src/oscillators.c
    ${AMY_DIR}/src/amy_midi.c
    ${AMY_DIR}/src/transfer.c
    ${AMY_DIR}/src/api.c
    ${AMY_DIR}/src/sequencer.c
    ${AMY_DIR}/src/pcm.c
    ${AMY_DIR}/src/i2s.c
    ${AMY_DIR}/src/log2_exp2.c
    ${AMY_DIR}/src/interp_partials.c
    ${AMY_DIR}/src/parse.c
    ${AMY_DIR}/src/instrument.c
    ${AMY_DIR}/src/midi_mappings.c
    ${AMY_DIR}/src/cv_trigger.c
)


