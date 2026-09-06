// Tab5 built-in camera: the SC2356 (register-compatible with the SC202CS the
// esp_cam_sensor component knows) on the ESP32-P4 MIPI-CSI port, brought up
// through the BSP and esp_video, and read out over V4L2.
//
// The sensor streams RAW8 at 1280x720 / 30 fps and the ISP turns that into
// RGB565, which is also the screen's resolution and LVGL's colour depth here.
// A small task keeps the newest frame dequeued so that reads from Python never
// wait on the camera; everything below that hands out pixels copies from that
// frame under a lock.
#pragma once

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define TAB5_CAMERA_WIDTH 1280
#define TAB5_CAMERA_HEIGHT 720

typedef struct {
    bool initialized;   // esp_video is up and the sensor answered
    bool running;       // streaming, capture task alive
    uint16_t chip_id;   // sensor PID as read over SCCB (0 until initialized)
    const char *sensor; // driver name for that PID
    uint32_t width;
    uint32_t height;
    uint32_t frames;    // frames received since the last start
    uint32_t errors;    // DQBUF failures / frames flagged with an error
    uint32_t dropped;   // frames handed straight back because a reader held the newest one
    uint32_t fps;       // frames counted in the last full second
    bool hflip;
    bool vflip;
    int gain;            // sensor analog gain index (V4L2_CID_GAIN), -1 if unreadable
    int exposure;        // sensor exposure (V4L2_CID_EXPOSURE, lines), -1 if unreadable
    int red_balance;     // ISP red gain x1000 (V4L2_CID_RED_BALANCE), -1 if unreadable
    int blue_balance;    // ISP blue gain x1000, -1 if unreadable
} tab5_camera_info_t;

// Power the sensor, initialise esp_video once, open the CSI device and start
// streaming. Idempotent while running.
esp_err_t tab5_camera_start(void);
// Stop streaming, release the frame buffers and close the device. The sensor
// stays powered and esp_video stays initialised, so the next start is quick.
esp_err_t tab5_camera_stop(void);
bool tab5_camera_running(void);
void tab5_camera_info(tab5_camera_info_t *out);

// Block until a frame newer than `seq` has arrived, or `timeout_ms` passes.
// Returns the new sequence number, or 0 on timeout / not running.
uint32_t tab5_camera_wait(uint32_t seq, uint32_t timeout_ms);
uint32_t tab5_camera_seq(void);

// Copy the newest frame into `dst` as w x h RGB565 (native uint16 per pixel,
// w*h*2 bytes), nearest-neighbour scaled from the full frame.
esp_err_t tab5_camera_read_rgb565(uint16_t *dst, int w, int h);

// Draw the newest frame into Tulip's BG plane at (x, y), scaled to w x h and
// converted to the BG's RGB332. The caller checks the rectangle.
esp_err_t tab5_camera_draw_bg(int x, int y, int w, int h);

// Encode the newest frame. Both hand back a buffer the caller frees with
// free_caps(); JPEG goes through the P4's hardware encoder, PNG through
// lodepng (24-bit RGB, no palette). `quality` is 1..100.
esp_err_t tab5_camera_encode_jpeg(int quality, uint8_t **out, size_t *out_len);
esp_err_t tab5_camera_encode_png(uint8_t **out, size_t *out_len);

// Mirror the image on the sensor. -1 leaves that axis alone.
esp_err_t tab5_camera_set_flip(int hflip, int vflip);

// Replace the scene with the sensor's test pattern, a ramp from black at the
// left to white at the right. The way to check the path -- frames arriving,
// orientation, byte order -- without pointing the camera at anything. Not a
// colour reference: the ramp goes through the ISP like a scene and the auto
// white balance settles on a tint for it (magenta, on the units measured).
esp_err_t tab5_camera_set_test_pattern(bool on);

#ifdef __cplusplus
}
#endif
