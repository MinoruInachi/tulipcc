# Set location of base MicroPython directory.

# The Tab5 display bridge rotates through the ESP32-P4 PPA, which is not part of
# the stock MicroPython esp32 component list.
list(APPEND IDF_COMPONENTS esp_driver_ppa esp_lcd)
# The USB-A connector is a host port (usb_host_tab5.c). MicroPython's esp32
# component list only carries the device-side pieces.
list(APPEND IDF_COMPONENTS usb)
# Tab5 drives the touch controllers directly rather than through bsp_touch_new(),
# so it can raise the I2C clock and keep the TP_INT interrupt.
list(APPEND IDF_COMPONENTS
    espressif__esp_lcd_touch
    espressif__esp_lcd_touch_gt911
    espressif__esp_lcd_touch_st7123)

include(../esp32_common.cmake)

if(NOT CMAKE_BUILD_EARLY_EXPANSION)
	find_package(Python3 REQUIRED COMPONENTS Interpreter)
	find_program(TAB5_C_PREPROCESSOR clang REQUIRED)

	set(TAB5_LV_BINDINGS_DIR ${CMAKE_CURRENT_LIST_DIR}/../../../lv_binding_micropython_tulip)
	set(TAB5_LVGL_DIR ${CMAKE_CURRENT_LIST_DIR}/../managed_components/lvgl__lvgl)
	set(TAB5_LV_FONTS_HEADER ${MICROPY_BOARD_DIR}/lv_fonts_tab5.h)
	set(TAB5_LV_PP ${CMAKE_CURRENT_BINARY_DIR}/tab5_lvgl.pp)
	set(TAB5_LV_MPY ${CMAKE_CURRENT_BINARY_DIR}/tab5_lv_mpy.c)

	file(GLOB_RECURSE TAB5_LVGL_HEADERS ${TAB5_LVGL_DIR}/src/*.h)

	add_custom_command(
		OUTPUT ${TAB5_LV_PP}
		COMMAND ${TAB5_C_PREPROCESSOR}
			-E -std=c99 -DPYCPARSER -DLV_CONF_SKIP -DLV_KCONFIG_PRESENT
			-include ${CMAKE_BINARY_DIR}/config/sdkconfig.h
			-include ${TAB5_LVGL_DIR}/src/lvgl_private.h
			-include ${TAB5_LV_FONTS_HEADER}
			-I ${TAB5_LV_BINDINGS_DIR}/pycparser/utils/fake_libc_include
			-I ${TAB5_LVGL_DIR}
			-I ${MICROPY_BOARD_DIR}
			${TAB5_LVGL_DIR}/lvgl.h
			-o ${TAB5_LV_PP}
		DEPENDS
			${TAB5_LVGL_DIR}/lvgl.h
			${TAB5_LVGL_HEADERS}
			${TAB5_LV_FONTS_HEADER}
			${CMAKE_BINARY_DIR}/config/sdkconfig.h
		VERBATIM
	)

	add_custom_command(
		OUTPUT ${TAB5_LV_MPY}
		COMMAND ${Python3_EXECUTABLE}
			${TAB5_LV_BINDINGS_DIR}/gen/gen_mpy.py
			-M lvgl -MP lv -E ${TAB5_LV_PP}
			${TAB5_LVGL_DIR}/lvgl.h
			> ${TAB5_LV_MPY}
		DEPENDS ${TAB5_LV_PP} ${TAB5_LV_BINDINGS_DIR}/gen/gen_mpy.py
		VERBATIM
	)

	set_source_files_properties(
		${TAB5_LV_MPY}
		PROPERTIES COMPILE_OPTIONS
			"-include;${TAB5_LVGL_DIR}/src/lvgl_private.h;-include;${TAB5_LV_FONTS_HEADER}"
	)

	# CONFIG_LV_GLOBAL_CUSTOM_INCLUDE names lv_global_tab5.h, which lives in the
	# board directory. main already has that on its include path via
	# idf_component_register; the lvgl component does not, and every one of its
	# sources reaches it through lv_global.h.
	idf_component_get_property(TAB5_LVGL_LIB lvgl__lvgl COMPONENT_LIB)
	target_include_directories(${TAB5_LVGL_LIB} PUBLIC ${MICROPY_BOARD_DIR})

endif()
