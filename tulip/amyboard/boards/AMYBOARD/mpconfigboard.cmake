set(IDF_TARGET esp32s3)

set(MICROPY_PY_TINYUSB ON)

# ulab (numpy/scipy subset) as a MicroPython user C module, as on TAB5;
# -DUSER_C_MODULES=... on the command line still wins.
get_filename_component(ULAB_DIR ${MICROPY_BOARD_DIR}/../../../../ulab ABSOLUTE)
if(NOT USER_C_MODULES AND EXISTS ${ULAB_DIR}/code/micropython.cmake)
    set(USER_C_MODULES ${ULAB_DIR}/code/micropython.cmake)
endif()


set(BOARD_DEFINITION1 AMYBOARD)
set(BOARD_DEFINITION2 MAKERFABS)

set(SDKCONFIG_DEFAULTS
    ../../micropython/ports/esp32/boards/sdkconfig.base
    ../../micropython/ports/esp32/boards/sdkconfig.240mhz
    ../esp32s3/boards/sdkconfig.tulip
    boards/AMYBOARD/sdkconfig.board
)

#list(APPEND MICROPY_SOURCE_BOARD
#)