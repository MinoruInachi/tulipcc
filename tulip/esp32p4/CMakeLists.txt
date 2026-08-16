# Top-level cmake file for building the Tab5 / ESP32-P4 port.

cmake_minimum_required(VERSION 3.12)

set(IDF_TARGET esp32p4)

if(NOT MICROPY_BOARD)
    set(MICROPY_BOARD TAB5)
endif()

if(NOT MICROPY_BOARD_DIR)
    set(MICROPY_BOARD_DIR ${CMAKE_CURRENT_LIST_DIR}/boards/${MICROPY_BOARD})
endif()

if(NOT EXISTS ${MICROPY_BOARD_DIR}/mpconfigboard.cmake)
    message(FATAL_ERROR "Invalid MICROPY_BOARD specified: ${MICROPY_BOARD}")
endif()

set(SDKCONFIG ${CMAKE_BINARY_DIR}/sdkconfig)

include(${MICROPY_BOARD_DIR}/mpconfigboard.cmake)

if (NOT MICROPY_FROZEN_MANIFEST)
    set(MICROPY_FROZEN_MANIFEST ${CMAKE_CURRENT_LIST_DIR}/boards/manifest.py)
endif()

file(WRITE ${CMAKE_BINARY_DIR}/sdkconfig.combined.in "")
foreach(SDKCONFIG_DEFAULT ${SDKCONFIG_DEFAULTS})
    file(READ ${SDKCONFIG_DEFAULT} CONTENTS)
    file(APPEND ${CMAKE_BINARY_DIR}/sdkconfig.combined.in "${CONTENTS}")
endforeach()
configure_file(${CMAKE_BINARY_DIR}/sdkconfig.combined.in ${CMAKE_BINARY_DIR}/sdkconfig.combined COPYONLY)
set(SDKCONFIG_DEFAULTS ${CMAKE_BINARY_DIR}/sdkconfig.combined)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

project(micropython)
