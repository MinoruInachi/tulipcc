# Set location of base MicroPython directory.

# The Tab5 display bridge rotates through the ESP32-P4 PPA, which is not part of
# the stock MicroPython esp32 component list.
list(APPEND IDF_COMPONENTS esp_driver_ppa esp_lcd)
# The USB-A connector is a host port (usb_host_tab5.c). MicroPython's esp32
# component list only carries the device-side pieces.
list(APPEND IDF_COMPONENTS usb)
# The built-in camera (camera_tab5.c): esp_video and the sensor drivers come in
# through the BSP, the P4's hardware JPEG encoder does not.
list(APPEND IDF_COMPONENTS esp_driver_jpeg espressif__esp_video espressif__esp_cam_sensor)
# The built-in IMU (imu_tab5.c) reads the BMI270 through its standalone driver,
# which is pulled in through the BSP; name it so main links against it.
list(APPEND IDF_COMPONENTS espressif__bmi270)
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

	# The generated binding is argument-marshalling glue around every LVGL call,
	# a quarter of a megabyte of it at -O2, and none of it is on a hot path:
	# the work happens inside LVGL. -Os on this one file (the last -O flag on
	# the command line wins) buys back most of what the camera stack costs in
	# the app partition. ulab gets the same treatment: matplotlib plots and the
	# occasional FFT do not need -O2 loop unrolling, and it is the next largest
	# body of code that is not a font or frozen Python.
	set_source_files_properties(
		${TAB5_LV_MPY}
		PROPERTIES COMPILE_OPTIONS
			"-include;${TAB5_LVGL_DIR}/src/lvgl_private.h;-include;${TAB5_LV_FONTS_HEADER};-Os"
	)
	get_filename_component(TAB5_ULAB_DIR ${CMAKE_CURRENT_LIST_DIR}/../../../ulab ABSOLUTE)
	file(GLOB_RECURSE TAB5_ULAB_SOURCES ${TAB5_ULAB_DIR}/code/*.c)
	set_source_files_properties(${TAB5_ULAB_SOURCES} PROPERTIES COMPILE_OPTIONS "-Os")

	# CONFIG_LV_GLOBAL_CUSTOM_INCLUDE names lv_global_tab5.h, which lives in the
	# board directory. main already has that on its include path via
	# idf_component_register; the lvgl component does not, and every one of its
	# sources reaches it through lv_global.h.
	idf_component_get_property(TAB5_LVGL_LIB lvgl__lvgl COMPONENT_LIB)
	target_include_directories(${TAB5_LVGL_LIB} PUBLIC ${MICROPY_BOARD_DIR})

	# The camera stack is control-plane code -- ioctl plumbing, sensor register
	# tables, the ISP statistics loop -- with the per-pixel work done by the
	# CSI/ISP/JPEG hardware, so it gains nothing from the -O2 the rest of the
	# firmware is built with, and the app partition is tight.
	foreach(cam_lib espressif__esp_video espressif__esp_cam_sensor espressif__esp_sccb_intf
	                esp_driver_isp esp_driver_cam esp_driver_jpeg)
		idf_component_get_property(TAB5_CAM_LIB ${cam_lib} COMPONENT_LIB)
		if(TAB5_CAM_LIB)
			target_compile_options(${TAB5_CAM_LIB} PRIVATE -Os)
		endif()
	endforeach()

endif()
