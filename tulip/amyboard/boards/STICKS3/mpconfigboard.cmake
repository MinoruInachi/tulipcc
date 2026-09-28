set(IDF_TARGET esp32s3)

set(MICROPY_PY_TINYUSB ON)

# ulab (numpy/scipy subset) as a MicroPython user C module, as on TAB5;
# -DUSER_C_MODULES=... on the command line still wins.
get_filename_component(ULAB_DIR ${MICROPY_BOARD_DIR}/../../../../ulab ABSOLUTE)
if(NOT USER_C_MODULES AND EXISTS ${ULAB_DIR}/code/micropython.cmake)
    set(USER_C_MODULES ${ULAB_DIR}/code/micropython.cmake)
endif()

# AMYBOARD itself is always defined by esp32_common.cmake; these select the
# StickS3 pins (pins.h) and AMY's ESP-as-I2S-master path (amy/src/i2s.c).
set(BOARD_DEFINITION1 AMYBOARD_STICKS3)
set(BOARD_DEFINITION2 AMY_I2S_MASTER)

set(SDKCONFIG_DEFAULTS
    ../../micropython/ports/esp32/boards/sdkconfig.base
    ../../micropython/ports/esp32/boards/sdkconfig.240mhz
    ../esp32s3/boards/sdkconfig.tulip
    boards/STICKS3/sdkconfig.board
)
