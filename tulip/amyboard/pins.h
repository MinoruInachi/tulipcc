// pins.h
// pins (and other MCU constants) 

#define SPI_LL_DATA_MAX_BIT_LEN (1 << 18)

#define I2C_NUM I2C_NUM_0
#define I2C_CLK_FREQ 400000
#define ESP_INTR_FLAG_DEFAULT 0

#ifdef AMYBOARD_STICKS3
// M5Stack StickS3 (ESP32-S3-PICO-1-N8R8). The ES8311 codec is clocked by the
// ESP (I2S master, MCLK out on 18); it and the M5PM1 PMIC share the internal
// I2C bus on 47/48. DIN MIDI goes out the Grove port (G9 out, G10 in) through
// an external MIDI adapter; there is only one MIDI OUT leg, so A == B.
#define CONFIG_I2S_MCLK  18
#define CONFIG_I2S_BCLK  17
#define CONFIG_I2S_LRCLK 15
#define CONFIG_I2S_DOUT  14
#define CONFIG_I2S_DIN   16
#define I2C_FOLLOWER_SCL 5   // Hat2-Bus; the follower is not started on StickS3
#define I2C_FOLLOWER_SDA 4
#define I2C_MASTER_SCL 48
#define I2C_MASTER_SDA 47

#define MIDI_OUT_PIN_A 9
#define MIDI_OUT_PIN_B 9
#define MIDI_IN_PIN 10

#else
// stuff in the eagle
#define CONFIG_I2S_MCLK  3
#define CONFIG_I2S_BCLK  8
#define CONFIG_I2S_LRCLK 2
#define CONFIG_I2S_DOUT 6 // data going to the codec, eg DAC data, also called AMYOUT
#define I2C_FOLLOWER_SCL 5
#define I2C_FOLLOWER_SDA 4 
#define I2C_MASTER_SCL 18
#define I2C_MASTER_SDA 17
#define CONFIG_I2S_DIN 9 // data coming from the codec, eg ADC  data, also called AMYIN

#define MIDI_OUT_PIN_A 14
#define MIDI_OUT_PIN_B 15

#define MIDI_IN_PIN 21
#define MPIO_C0 7

#define SPI0_CS0 10
#define SPI0_MOSI 11
#define SPI0_SCK 12
#define SPI0_MISO 13

#endif // AMYBOARD_STICKS3
