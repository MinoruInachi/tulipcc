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
    ${MICROPY_BOARD_DIR}/../../../shared/u8g2_fonts.c
    ${MICROPY_BOARD_DIR}/shared_renderer_tab5_glue.c
    ${MICROPY_BOARD_DIR}/audio_tab5.c
    ${MICROPY_BOARD_DIR}/touch_tab5.c
    ${MICROPY_BOARD_DIR}/keyboard_tab5.c
    ${MICROPY_BOARD_DIR}/usb_host_tab5.c
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


