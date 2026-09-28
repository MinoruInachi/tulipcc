# AMYboard "main component" CMakeLists.txt contents.
#
# This is micropython/ports/esp32/esp32_common.cmake (at the pinned micropython)
# with AMYboard's additions, each marked "AMYboard:". Keep it that way: when the
# micropython pin moves, re-copy the upstream file and re-apply those blocks.

# Set location of base MicroPython directory.
if(NOT MICROPY_DIR)
    get_filename_component(MICROPY_DIR ${CMAKE_CURRENT_LIST_DIR}/../../micropython ABSOLUTE)
endif()

# AMYboard: this directory, Tulip's shared sources, and AMY.
get_filename_component(AMYBOARD_DIR ${CMAKE_CURRENT_LIST_DIR} ABSOLUTE)
get_filename_component(TULIP_SHARED_DIR ${CMAKE_CURRENT_LIST_DIR}/../shared ABSOLUTE)
get_filename_component(AMY_DIR ${CMAKE_CURRENT_LIST_DIR}/../../amy ABSOLUTE)

# Set location of the ESP32 port directory.
if(NOT MICROPY_PORT_DIR)
    get_filename_component(MICROPY_PORT_DIR ${MICROPY_DIR}/ports/esp32 ABSOLUTE)
endif()

# RISC-V specific inclusions
if(CONFIG_IDF_TARGET_ARCH_RISCV)
    list(APPEND MICROPY_SOURCE_LIB
        ${MICROPY_DIR}/shared/runtime/gchelper_native.c
        ${MICROPY_DIR}/shared/runtime/gchelper_rv32i.s
    )
endif()

if(NOT DEFINED MICROPY_PY_TINYUSB)
    if(CONFIG_IDF_TARGET_ESP32S2 OR CONFIG_IDF_TARGET_ESP32S3 OR CONFIG_IDF_TARGET_ESP32P4)
        set(MICROPY_PY_TINYUSB ON)
    endif()
endif()

# Enable error text compression by default.
if(NOT MICROPY_ROM_TEXT_COMPRESSION)
    set(MICROPY_ROM_TEXT_COMPRESSION ON)
endif()

# Include core source components.
include(${MICROPY_DIR}/py/py.cmake)

# CMAKE_BUILD_EARLY_EXPANSION is set during the component-discovery phase of
# `idf.py build`, so none of the extmod/usermod (and in reality, most of the
# micropython) rules need to happen. Specifically, you cannot invoke add_library.
if(NOT CMAKE_BUILD_EARLY_EXPANSION)
    # Enable extmod components that will be configured by extmod.cmake.
    # A board may also have enabled additional components.
    if (NOT DEFINED MICROPY_PY_BTREE)
        set(MICROPY_PY_BTREE ON)
    endif()

    include(${MICROPY_DIR}/py/usermod.cmake)
    include(${MICROPY_DIR}/extmod/extmod.cmake)
endif()

list(APPEND MICROPY_QSTRDEFS_PORT
    ${MICROPY_PORT_DIR}/qstrdefsport.h
)

list(APPEND MICROPY_SOURCE_SHARED
    ${MICROPY_DIR}/shared/readline/readline.c
    ${MICROPY_DIR}/shared/netutils/netutils.c
    ${MICROPY_DIR}/shared/timeutils/timeutils.c
    ${MICROPY_DIR}/shared/runtime/interrupt_char.c
    ${MICROPY_DIR}/shared/runtime/mpirq.c
    ${MICROPY_DIR}/shared/runtime/stdout_helpers.c
    ${MICROPY_DIR}/shared/runtime/sys_stdio_mphal.c
    ${MICROPY_DIR}/shared/runtime/pyexec.c
)

list(APPEND MICROPY_SOURCE_LIB
    # AMYboard: user C DSP (tulip.install_c_process). esp_elf.h (a managed
    # component header) isn't on the qstr-preprocess include path;
    # user_c_dsp.c guards it with NO_QSTR.
    ${TULIP_SHARED_DIR}/user_c_dsp.c
    ${TULIP_SHARED_DIR}/3rdparty/xcc700/xcc700t.c
    ${MICROPY_DIR}/lib/littlefs/lfs1.c
    ${MICROPY_DIR}/lib/littlefs/lfs1_util.c
    ${MICROPY_DIR}/lib/littlefs/lfs2.c
    ${MICROPY_DIR}/lib/littlefs/lfs2_util.c
    ${MICROPY_DIR}/lib/mbedtls_errors/esp32_mbedtls_errors.c
    ${MICROPY_DIR}/lib/oofatfs/ff.c
    ${MICROPY_DIR}/lib/oofatfs/ffunicode.c
)

# AMYboard: vendored third-party style (upstream xcc700): keep it diffable,
# don't restyle. (Guarded: this file is also include()d during IDF's early
# script-mode pass, where set_source_files_properties is not scriptable.)
if(NOT CMAKE_BUILD_EARLY_EXPANSION)
    set_source_files_properties(${TULIP_SHARED_DIR}/3rdparty/xcc700/xcc700t.c
        PROPERTIES COMPILE_OPTIONS "-Wno-misleading-indentation")
endif()

list(APPEND MICROPY_SOURCE_DRIVERS
    ${MICROPY_DIR}/drivers/bus/softspi.c
    ${MICROPY_DIR}/drivers/dht/dht.c
)

if(MICROPY_PY_TINYUSB)
    string(TOUPPER OPT_MCU_${IDF_TARGET} tusb_mcu)

    list(APPEND MICROPY_DEF_TINYUSB
        CFG_TUSB_MCU=${tusb_mcu}
    )

    list(APPEND MICROPY_SOURCE_TINYUSB
        ${MICROPY_DIR}/shared/tinyusb/mp_usbd.c
        ${MICROPY_DIR}/shared/tinyusb/mp_usbd_cdc.c
        # AMYboard: CDC + MIDI composite descriptor instead of the stock CDC one.
        ${AMYBOARD_DIR}/mp_usbd_descriptor.c
        ${MICROPY_DIR}/shared/tinyusb/mp_usbd_runtime.c
    )

    list(APPEND MICROPY_INC_TINYUSB
        ${MICROPY_DIR}/shared/tinyusb/
    )

    # Build the Espressif tinyusb component with MicroPython shared/tinyusb/tusb_config.h
    idf_component_get_property(tusb_lib espressif__tinyusb COMPONENT_LIB)
    target_include_directories(${tusb_lib} PRIVATE
        ${MICROPY_DIR}/shared/tinyusb
        ${MICROPY_DIR}
        ${MICROPY_PORT_DIR}
        ${MICROPY_BOARD_DIR}
        # AMYboard: mpconfigboard.h includes pins.h from here.
        ${AMYBOARD_DIR})
endif()

list(APPEND MICROPY_SOURCE_PORT
    panichandler.c
    adc.c
    main.c
    ppp_set_auth.c
    uart.c
    usb.c
    usb_serial_jtag.c
    gccollect.c
    mphalport.c
    fatfs_port.c
    help.c
    machine_bitstream.c
    machine_timer.c
    machine_pin.c
    machine_touchpad.c
    machine_dac.c
    machine_i2c.c
    network_common.c
    network_lan.c
    network_ppp.c
    network_wlan.c
    network_wlan_csi.c
    mpnimbleport.c
    modsocket.c
    lwip_patch.c
    modesp.c
    esp32_nvs.c
    esp32_partition.c
    esp32_pcnt.c
    esp32_rmt.c
    esp32_ulp.c
    esp32_ldo.c
    modesp32.c
    machine_hw_spi.c
    mpthreadport.c
    machine_rtc.c
    machine_sdcard.c
    modespnow.c
)
list(TRANSFORM MICROPY_SOURCE_PORT PREPEND ${MICROPY_PORT_DIR}/)
# AMYboard: its own main.c (task layout, PSRAM heap, restart on soft reset)
# and mphalport.c, plus the board support (I2C follower, CV, bg I2C queue).
list(REMOVE_ITEM MICROPY_SOURCE_PORT
    ${MICROPY_PORT_DIR}/main.c
    ${MICROPY_PORT_DIR}/mphalport.c
)
list(APPEND MICROPY_SOURCE_PORT
    ${AMYBOARD_DIR}/main.c
    ${AMYBOARD_DIR}/mphalport.c
    ${AMYBOARD_DIR}/amyboard_support.c
)
list(APPEND MICROPY_SOURCE_PORT ${CMAKE_BINARY_DIR}/pins.c)

# AMYboard: the tulip module and AMY.
list(APPEND MICROPY_SOURCE_EXTMOD
    ${TULIP_SHARED_DIR}/modtulip.c
    ${TULIP_SHARED_DIR}/polyfills.c
    ${TULIP_SHARED_DIR}/tulip_helpers.c
    ${TULIP_SHARED_DIR}/help.c
    ${TULIP_SHARED_DIR}/tsequencer.c
    ${TULIP_SHARED_DIR}/amy_connector.c
    ${AMY_DIR}/src/algorithms.c
    ${AMY_DIR}/src/custom.c
    ${AMY_DIR}/src/patches.c
    ${AMY_DIR}/src/amy.c
    ${AMY_DIR}/src/api.c
    ${AMY_DIR}/src/instrument.c
    ${AMY_DIR}/src/i2s.c
    ${AMY_DIR}/src/amy_midi.c
    ${AMY_DIR}/src/parse.c
    ${AMY_DIR}/src/delay.c
    ${AMY_DIR}/src/envelope.c
    ${AMY_DIR}/src/filters.c
    ${AMY_DIR}/src/oscillators.c
    ${AMY_DIR}/src/transfer.c
    ${AMY_DIR}/src/sequencer.c
    ${AMY_DIR}/src/pcm.c
    ${AMY_DIR}/src/log2_exp2.c
    ${AMY_DIR}/src/interp_partials.c
    ${AMY_DIR}/src/midi_mappings.c
    ${AMY_DIR}/src/note_output.c
    ${AMY_DIR}/src/cv_trigger.c
)

list(APPEND MICROPY_SOURCE_QSTR
    ${MICROPY_SOURCE_PY}
    ${MICROPY_SOURCE_EXTMOD}
    ${MICROPY_SOURCE_USERMOD}
    ${MICROPY_SOURCE_SHARED}
    ${MICROPY_SOURCE_LIB}
    ${MICROPY_SOURCE_PORT}
    ${MICROPY_SOURCE_BOARD}
    ${MICROPY_SOURCE_TINYUSB}
)

list(APPEND IDF_COMPONENTS
    app_update
    bootloader_support
    bt
    driver
    esp_adc
    esp_app_format
    esp_mm
    esp_common
    esp_driver_gptimer
    esp_eth
    esp_event
    esp_hw_support
    esp_netif
    esp_partition
    esp_pm
    esp_psram
    esp_ringbuf
    esp_rom
    esp_system
    esp_timer
    esp_wifi
    freertos
    hal
    heap
    log
    lwip
    mbedtls
    newlib
    nvs_flash
    sdmmc
    soc
    spi_flash
    ulp
    usb
    vfs
    # AMYboard
    esp_driver_i2c
    esp_driver_i2s
    esp_driver_uart
    esp_driver_tsens
    espressif__elf_loader
)

if($ENV{IDF_VERSION} VERSION_GREATER_EQUAL "5.4")
    list(APPEND IDF_COMPONENTS
        esp_driver_touch_sens)
endif()

# Provide the default LD fragment if not set
if (MICROPY_USER_LDFRAGMENTS)
    set(MICROPY_LDFRAGMENTS ${MICROPY_USER_LDFRAGMENTS})
endif()

if (UPDATE_SUBMODULES)
    # ESP-IDF checks if some paths exist before CMake does. Some paths don't
    # yet exist if this is an UPDATE_SUBMODULES pass on a brand new checkout, so remove
    # any path which might not exist yet. A "real" build will not set UPDATE_SUBMODULES.
    unset(MICROPY_SOURCE_TINYUSB)
    unset(MICROPY_SOURCE_EXTMOD)
    unset(MICROPY_SOURCE_LIB)
    unset(MICROPY_INC_TINYUSB)
    unset(MICROPY_INC_CORE)
endif()

# Register the main IDF component.
idf_component_register(
    SRCS
        ${MICROPY_SOURCE_PY}
        ${MICROPY_SOURCE_EXTMOD}
        ${MICROPY_SOURCE_SHARED}
        ${MICROPY_SOURCE_LIB}
        ${MICROPY_SOURCE_DRIVERS}
        ${MICROPY_SOURCE_PORT}
        ${MICROPY_SOURCE_BOARD}
        ${MICROPY_SOURCE_TINYUSB}
    INCLUDE_DIRS
        # AMYboard: ahead of the port directory, so its headers win.
        ${AMYBOARD_DIR}
        ${MICROPY_INC_CORE}
        ${MICROPY_INC_USERMOD}
        ${MICROPY_INC_TINYUSB}
        ${MICROPY_PORT_DIR}
        ${MICROPY_BOARD_DIR}
        ${CMAKE_BINARY_DIR}
        # AMYboard
        ${TULIP_SHARED_DIR}
        ${TULIP_SHARED_DIR}/3rdparty/xcc700
        ${AMY_DIR}/src
    LDFRAGMENTS
        ${MICROPY_LDFRAGMENTS}
    REQUIRES
        ${IDF_COMPONENTS}
)

# Set the MicroPython target as the current (main) IDF component target.
set(MICROPY_TARGET ${COMPONENT_TARGET})

# Define mpy-cross flags, for use with frozen code.
if(CONFIG_IDF_TARGET_ARCH_XTENSA)
    set(MICROPY_CROSS_FLAGS -march=xtensawin)
elseif(CONFIG_IDF_TARGET_ARCH_RISCV)
    if (CONFIG_IDF_TARGET_ESP32P4)
        set(MICROPY_CROSS_FLAGS "-march=rv32imc -march-flags=zcmp")
    else()
        set(MICROPY_CROSS_FLAGS -march=rv32imc)
    endif()
endif()

# Set compile options for this port.
target_compile_definitions(${MICROPY_TARGET} PUBLIC
    ${MICROPY_DEF_COMPONENT}
    ${MICROPY_DEF_CORE}
    ${MICROPY_DEF_BOARD}
    ${MICROPY_DEF_TINYUSB}
    MICROPY_VFS_FAT=1
    MICROPY_VFS_LFS2=1
    FFCONF_H=\"${MICROPY_OOFATFS_DIR}/ffconf.h\"
    LFS1_NO_MALLOC LFS1_NO_DEBUG LFS1_NO_WARN LFS1_NO_ERROR LFS1_NO_ASSERT
    LFS2_NO_MALLOC LFS2_NO_DEBUG LFS2_NO_WARN LFS2_NO_ERROR LFS2_NO_ASSERT
    # AMYboard
    MICROPY_VFS_LFS1=1
    ESP_PLATFORM
    STATIC=static
    AMYBOARD
    AMY_WAVETABLE
    GAMMA9001
    TULIP_USER_C_DSP
    MALLOC_CAPS_DEFINED  # malloc_caps() comes from shared/polyfills.c, not amy.c
    ${BOARD_DEFINITION1}
    ${BOARD_DEFINITION2}
)

# Disable some warnings to keep the build output clean.
target_compile_options(${MICROPY_TARGET} PUBLIC
    ${MICROPY_COMPILE_COMPONENT}
    -Wno-clobbered
    -Wno-deprecated-declarations
    -Wno-missing-field-initializers
    # AMYboard
    -Wno-uninitialized
    -Wno-dangling-pointer
    -Wno-unused-const-variable
    -fsingle-precision-constant
    -Wno-strict-aliasing
)

# User C modules don't pick up certain compile options set by the IDF, most
# importantly the optimisation level.  So set them here.
idf_build_get_property(idf_compile_options COMPILE_OPTIONS)
target_compile_options(usermod INTERFACE ${idf_compile_options})

# Additional include directories needed for private NimBLE headers.
target_include_directories(${MICROPY_TARGET} PUBLIC
    ${IDF_PATH}/components/bt/host/nimble/nimble
)

# Add additional extmod and usermod components.
if (MICROPY_PY_BTREE)
    target_link_libraries(${MICROPY_TARGET} $<TARGET_OBJECTS:micropy_extmod_btree>)
    target_link_libraries(${MICROPY_TARGET} "-u abort_")  # micropy_extmod_btree links to this symbol found in MICROPY_TARGET
endif()
target_link_libraries(${MICROPY_TARGET} usermod)

# Extra linker options
# (when wrap symbols are in standalone files, --undefined ensures
# the linker doesn't skip that file.)
target_link_options(${MICROPY_TARGET} PUBLIC
  # Patch LWIP memory pool allocators (see lwip_patch.c)
  -Wl,--undefined=memp_malloc
  -Wl,--wrap=memp_malloc
  -Wl,--wrap=memp_free

  # Enable the panic handler wrapper
  -Wl,--undefined=esp_panic_handler
  -Wl,--wrap=esp_panic_handler
  -Wl,--wrap=esp_efuse_rtc_calib_get_ver

  # AMYboard: with the runtime USB device off, nothing else in main refers to
  # mp_usbd_descriptor.c, so pull its TinyUSB descriptor callbacks in by name.
  -Wl,--undefined=tud_descriptor_device_cb
  -Wl,--undefined=tud_descriptor_configuration_cb
  -Wl,--undefined=tud_descriptor_string_cb
)

# Collect all of the include directories and compile definitions for the IDF components,
# including those added by the IDF Component Manager via idf_components.yaml.
foreach(comp ${__COMPONENT_NAMES_RESOLVED})
    micropy_gather_target_properties(__idf_${comp})
    micropy_gather_target_properties(${comp})
endforeach()

# Explicitly add extra definitions for MicroPython's preprocessing stage
# (these are not picked up by the above micropy_gather_target_properties).
list(APPEND MICROPY_CPP_DEF_EXTRA "ESP_PLATFORM")

# Include the main MicroPython cmake rules.
include(${MICROPY_DIR}/py/mkrules.cmake)

# Generate source files for named pins (requires mkrules.cmake for MICROPY_GENHDR_DIR).

set(GEN_PINS_PREFIX "${MICROPY_PORT_DIR}/boards/pins_prefix.c")
set(GEN_PINS_MKPINS "${MICROPY_PORT_DIR}/boards/make-pins.py")
set(GEN_PINS_SRC "${CMAKE_BINARY_DIR}/pins.c")
set(GEN_PINS_HDR "${MICROPY_GENHDR_DIR}/pins.h")

if(EXISTS "${MICROPY_BOARD_DIR}/pins.csv")
    set(GEN_PINS_BOARD_CSV "${MICROPY_BOARD_DIR}/pins.csv")
    set(GEN_PINS_BOARD_CSV_ARG --board-csv "${GEN_PINS_BOARD_CSV}")
endif()

target_sources(${MICROPY_TARGET} PRIVATE ${GEN_PINS_HDR})

add_custom_command(
    OUTPUT ${GEN_PINS_SRC} ${GEN_PINS_HDR}
    COMMAND ${Python3_EXECUTABLE} ${GEN_PINS_MKPINS} ${GEN_PINS_BOARD_CSV_ARG}
        --prefix ${GEN_PINS_PREFIX} --output-source ${GEN_PINS_SRC} --output-header ${GEN_PINS_HDR}
    DEPENDS
        ${MICROPY_MPVERSION}
        ${GEN_PINS_MKPINS}
        ${GEN_PINS_BOARD_CSV}
        ${GEN_PINS_PREFIX}
    VERBATIM
    COMMAND_EXPAND_LISTS
)
