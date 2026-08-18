# AMYboard のアクセサリ

重要: アクセサリは、3.5mm ジャックがある側の**フロントパネル**の I2C ジャックに挿してください。背面の "Tulip" 用 I2C ジャックでは**ありません**。背面のジャックは AMYboard を Tulip CC に接続するための別の I2C バスで、そこに挿したアクセサリはコードから到達できません。

AMYboard には、アクセサリを挿すためのフロントパネル I2C ポートがあります。ケーブル（GROVE コネクタ）1 本で接続でき、はんだ付けは不要です。

I2C バス（SCL=18、SDA=17、400kHz）は追加ハードウェアの接続に使えます。

```python
import amyboard
i2c = amyboard.get_i2c()

# 接続されているデバイスをスキャン
print(i2c.scan())

# 任意の I2C デバイスのレジスタを読み書き
val = amyboard.read_register(addr, reg)
amyboard.write_register(addr, reg, val)
```

DAC、ADC、ディスプレイ、センサーを追加して AMYboard の機能を拡張できます。

**注意:** 以下の Adafruit 製ユニットには [GROVE から Stemma QT への変換ケーブル](https://www.adafruit.com/product/4528)が必要です。在庫を見つけにくいことがありますが、はんだごてがあれば自作できます。既存の GROVE ケーブルと Stemma QT ケーブルをそれぞれ中央で切り、むき出しの端同士をつなぐだけです。（GROVE ケーブルでは SDA と SCL の色がメーカーによって異なる点に注意してください。ただし下記の並び順は正しいはずです。つまり SCL が「端」のケーブルです。）

|信号|Stemma QT の色|Grove の色|
|--|--|--|
|GND|黒|黒|
|Vcc|赤|赤|
|SDA|青|白（Seeed）／黄（M5）／白（Adafruit）|
|SCL|黄|黄（Seeed）／白（M5）／緑（Adafruit）|


## 動作確認済みのアクセサリ

### ディスプレイ

 - ![Adafruit 1.5" 128x128 OLED](../../docs/amyboard/img/accessory_adafruit_oled.jpg)  
   [**Adafruit Grayscale 1.5" 128x128 OLED ディスプレイ（STEMMA QT）**](https://www.adafruit.com/product/4741) -- 16 階調のグレースケールを持つ、高コントラストの 128x128 OLED です。AMYboard はこのディスプレイを標準サポートしており、波形の可視化にも対応しています。STEMMA QT ケーブルで直接接続できます。

   AMYboard のフロントパネルには、このディスプレイ用のサイズの切り欠きと、ディスプレイ側の穴とも合う M2 サイズのナット／ボルト穴が 4 つあります。ナットを締めたときに画面のガラスがフロントパネルに押し付けられて割れる（そして壊れる）のを防ぐため、スペーサーの使用を強くおすすめします。

 - [**汎用の SH1107 128x128 OLED ディスプレイ（I2C）**](https://www.amazon.com/HiLetgo-SH1107-128x128-Display-Module/dp/B0CFF17DGH/) -- SH1107 や SSD1327 ベースの汎用ディスプレイの多くは、AMYboard でそのまま動きます。
   
```python
import amyboard

# ディスプレイを初期化（SSD1327 か SH1107 を自動判別）
amyboard.init_display()

# テキストを描く（x, y, 色 0-255）
amyboard.display.text("Hello!", 0, 0, 255)
amyboard.display.text("AMYboard", 0, 16, 128)
amyboard.display_refresh()

# 図形を描く
amyboard.display.fill_rect(10, 40, 50, 20, 200)
amyboard.display.hline(0, 70, 128, 255)   # 水平線: x, y, 幅, 色
amyboard.display.vline(64, 0, 128, 255)   # 垂直線: x, y, 高さ, 色
amyboard.display_refresh()

# OLED を回転（sh1107 のみ）。0, 90, 180, 270 度のいずれか。
amyboard.set_display_rotation(90)

# ライブの波形表示を出す
amyboard.draw_waveform()
```

### ロータリーエンコーダ

AMYboard は 3 種類のロータリーエンコーダのアクセサリに対応しています（後述）。エンコーダの数、LED の配置、I2C のプロトコルがそれぞれ異なるため、どれを使う場合でも（あるいはウェブのシミュレータでも）変更なしに動くスケッチを書くには、統一 API である `amyboard.encoder()` を使うのがいちばん簡単です。接続されているデバイスを自動検出し、一貫したインターフェースを提供します。

```python
import amyboard

enc = amyboard.encoder()   # 接続されているものをすべて自動検出
print(enc.type)            # "adafruit_single", "adafruit_quad", "m5stack", "web",
                           # "multi"（種類の混在）、または None
print(enc.devices)         # 接続中の各デバイスを (type, i2c_address) で
print(enc.encoders)        # 全デバイス合計のエンコーダ数
print(enc.leds)            # アドレス指定可能な LED の合計数

for i in range(enc.encoders):
    pos = enc.read(i)          # 累積位置。0 から始まります
    pressed = enc.button(i)    # 押しボタンを押している間 True
    if i < enc.leds:
        enc.led(i, 0, 64, 0)   # LED i を暗い緑に（r, g, b。それぞれ 0..255）

enc.reset()                # すべてのエンコーダを 0 に戻す（enc.reset(i) で 1 つだけ）
enc.switch()               # M5Stack のトグルスイッチの状態（他のデバイスでは False）
```

**1 つのバスに複数のエンコーダ基板をつなぐ場合。** Adafruit のブレークアウトはどちらもアドレスジャンパを備えており（シングルは 0x36〜0x3D で最大 8 枚、クアッドは 0x49〜0x50 で最大 8 枚）、`amyboard.encoder()` は接続されている*すべて*のデバイスを見つけ、フラットなインデックス空間を持つ 1 つの `Encoder` として提示します。インデックスは固定の順序でデバイスをまたいで並びます。まず M5Stack、次にクアッド、最後にシングルで、それぞれ I2C アドレスの昇順です。したがって 0x36 と 0x37 のシングルエンコーダ 2 台があれば `enc.encoders == 2` となり、0x36 の基板がエンコーダ 0 になります。特定の 1 枚だけにバインドしたい場合は、そのアドレス（必要なら種類も）を渡します。

```python
enc_a = amyboard.encoder(addr=0x36)   # 0x36 の基板だけ
enc_b = amyboard.encoder(addr=0x37)   # 0x37 の基板だけ
```

**回転方向が逆のエンコーダ。** ハードウェアのリビジョンによっては「逆向き」に数えるものがあります（時計回りで減る）。`invert=True` を渡すと `read()` が返す方向を反転できます。エンコーダごとに設定することもできます。

```python
enc = amyboard.encoder(invert=True)   # すべてのエンコーダを反転
enc.invert(True, 2)                   # ...またはエンコーダ 2 だけ反転
enc.invert(False)                     # ハードウェア本来の方向に戻す
```

エンコーダが接続されていない場合、`enc.encoders` は 0 になり、どのメソッドも安全なデフォルト値を返すので、スケッチはそのまま動きます。以下に示すデバイスごとの関数も引き続き使えますが、新しいコードでは `amyboard.encoder()` を優先してください。

 - ![Adafruit STEMMA QT Rotary Encoder](../../docs/amyboard/img/accessory_adafruit_encoder.jpg)  
   [**Adafruit I2C STEMMA QT ロータリーエンコーダ ブレークアウト**](https://www.adafruit.com/product/5880) -- 押しボタンと NeoPixel LED を備えた単一のロータリーエンコーダで、I2C 経由の seesaw ファームウェアで動きます。アドレスジャンパにより 1 つの I2C バスに最大 8 台まで接続できます。AMYboard はエンコーダの位置とボタンの状態を読む Python サポートを内蔵しています。

```python
import amyboard

# エンコーダの位置を読む（0-3）
pos = amyboard.read_encoder(encoder=0)

# エンコーダの押しボタンを初期化して読む
amyboard.init_buttons()
buttons = amyboard.read_buttons()
# 4 つの真偽値のタプルを返します（True = 押下）

# オンボードの単一 NeoPixel を制御します。デフォルトはクアッドの
# ブレークアウト向けなので、5880 の seesaw アドレス（0x36）と NeoPixel のピン（6）を渡します。
amyboard.init_neopixels(num=1, pin=6, seesaw_dev=0x36)
amyboard.set_neopixel(0, 0, 64, 0, seesaw_dev=0x36)  # 暗い緑
amyboard.show_neopixels(seesaw_dev=0x36)

# すべてのエンコーダを OLED ディスプレイで監視
amyboard.monitor_encoders()
```

 - ![Adafruit QT Quad Rotary Encoder](../../docs/amyboard/img/accessory_adafruit_quad_encoder.jpg)  
   [**Adafruit I2C QT クアッドロータリーエンコーダ ブレークアウト**](https://www.adafruit.com/product/5752) -- 押しボタン内蔵のロータリーエンコーダ 4 個と、エンコーダごとの RGB NeoPixel 1 個を 1 枚の I2C ブレークアウトに載せたもので、seesaw ファームウェアで動きます。AMYboard は `read_encoder()`、`init_buttons()`、`read_buttons()`、`init_neopixels()`/`set_neopixel()`/`show_neopixels()` でこれをサポートしています。

```python
import amyboard

# 4 つのエンコーダのいずれかを読む（0-3）
pos = amyboard.read_encoder(encoder=0)

# 4 つの押しボタンを初期化して読む
amyboard.init_buttons()
buttons = amyboard.read_buttons()
# 4 つの真偽値のリストを返します（True = 押下）

# オンボードの 4 つの NeoPixel（エンコーダごとに 1 つ）を制御します。
# デフォルト値はこのブレークアウトに合っています（num=4, pin=18, seesaw_dev=0x49）。
amyboard.init_neopixels()
amyboard.set_neopixel(0, 64, 0, 0)   # エンコーダ 0 -> 暗い赤
amyboard.set_neopixel(1, 0, 64, 0)   # エンコーダ 1 -> 暗い緑
amyboard.set_neopixel(2, 0, 0, 64)   # エンコーダ 2 -> 暗い青
amyboard.set_neopixel(3, 32, 32, 0)  # エンコーダ 3 -> 暗い黄
amyboard.show_neopixels()            # 設定した色を LED に反映
```

 - ![M5Stack 8-Encoder Unit](../../docs/amyboard/img/accessory_m5_8encoder.jpg)  
   [**M5Stack 8-Encoder Unit（STM32F030）**](https://shop.m5stack.com/products/8-encoder-unit-stm32f030) -- RGB LED 付きのロータリーエンコーダ 8 個とトグルスイッチを 1 つの I2C ユニットに搭載しています。複数のシンセパラメータを同時に操作するのに最適です。移植性のあるコードには（上記の）`amyboard.encoder()` を、あるいは低レベルの `m5_8encoder` モジュールを直接使ってください。

```python
import m5_8encoder

# 各エンコーダの累積位置（-2**31 〜 +2**31）
positions = m5_8encoder.read_all_counters()

# 各エンコーダの押しボタンの状態。レジスタはアクティブロウで、
# 0 = 押下、1 = 離した状態です。（amyboard.encoder().button(i) は
# これを True == 押下 に正規化します。）
buttons = m5_8encoder.read_all_buttons()

# 側面のトグルスイッチ（0 または 1）
switch = m5_8encoder.read_switch()

# エンコーダ 0 の LED を赤く光らせる
m5_8encoder.set_led(0, bytes([255, 0, 0]))
```

### ノブとジョイスティック

 - ![M5Stack 8-Angle Unit](../../docs/amyboard/img/accessory_m5_8angle.jpg)  
   [**M5Stack 8-Angle Unit**](https://shop.m5stack.com/products/8-angle-unit-with-potentiometer) -- 1 つの I2C ユニットにポテンショメータのノブが 8 個。各ノブは 0.0〜1.0 の float として読めます。

```python
import m58angle

# ノブ 0 を読む（範囲 0.0〜1.0）
val = m58angle.get(0)

# 8 つのノブすべてを AMY のシンセパラメータにマップする
import amy
for ch in range(8):
    amy.send(osc=ch, amp=m58angle.get(ch))
```

 - ![M5Stack I2C Joystick](../../docs/amyboard/img/accessory_m5_joystick.jpg)  
   [**M5Stack I2C ジョイスティック**](https://shop.m5stack.com/products/i2c-joystick-unit-v1-1-mega8a) -- 押しボタン付きの 2 軸アナログスティックです。

```python
import m5joy

# (x, y, button) を返します。x と y は 0.0..1.0、button は 0/1
x, y, btn = m5joy.get()
```

### アナログ入出力（DAC、ADC、CV）

これらのユニットは、モジュラー機材を駆動する AMYboard の CV 出力や、センサーの読み取りと相性がよいものです。CV については[モジュラーシンセのセットアップ](modular.md)を参照してください。

 - ![Mabee DAC GP8413](../../docs/amyboard/img/accessory_mabee_dac.jpg)  
   [**Mabee DAC（GP8413、2 チャンネル、最大 10V）**](https://www.makerfabs.com/mabee-dac-gp8413.html) -- 1 ユニットあたり CV 出力 2 系統。アドレスジャンパを変えれば 1 つのバスに最大 4 ユニット（8 チャンネル）まで接続できます。

```python
import mabeedac

# チャンネル 0 を 5.0 V に設定
mabeedac.set(5.0, channel=0)
mabeedac.set(2.5, channel=1)
```

 - ![M5Stack DAC2 Unit](../../docs/amyboard/img/accessory_m5_dac2.jpg)  
   [**M5Stack DAC2 Unit（GP8413、2 チャンネル、最大 10V）**](https://shop.m5stack.com/products/dac-2-i2c-unit-gp8413) -- Mabee DAC と同じチップを M5 の筐体に収めたものです。

```python
import m5dac2

m5dac2.set(7.5, channel=0)
m5dac2.set(0.0, channel=1)
```

 - ![M5Stack DAC Unit](../../docs/amyboard/img/accessory_m5_dac.jpg)  
   [**M5Stack DAC Unit（1 チャンネル、最大 3.3V）**](https://shop.m5stack.com/products/dac-unit) -- 12 ビット出力 1 系統、0〜3.3V です。

```python
import m5dac

m5dac.set(1.65)  # フルスケールの半分
```

 - ![M5Stack ADC Unit](../../docs/amyboard/img/accessory_m5_adc.jpg)  
   [**M5Stack ADC Unit（ADS1100、最大 12V）**](https://shop.m5stack.com/products/adc-i2c-unit-v1-1-ads1100?variant=44321440399617) -- 最大 12V の外部電圧（CV 入力など）を読み取ります。

```python
import m5adc

volts = m5adc.get()
print(volts)
```

### 汎用 I/O

 - ![M5Stack Extend I/O Unit](../../docs/amyboard/img/accessory_m5_extend.jpg)  
   [**M5Stack Extend I/O Unit（PCA9554PW）**](https://shop.m5stack.com/products/official-extend-serial-i-o-unit) -- I2C 経由の GPIO 8 ピン。各ピンを入力または出力に設定できます。

```python
import m5extend

# ピン 0 を出力に設定し、High にする
m5extend.set_pin_mode(0, False)   # False = 出力
m5extend.write_pin(0, True)

# ピン 1 を入力に設定し、読み取る
m5extend.set_pin_mode(1, True)    # True = 入力
state = m5extend.read_pin(1)
```

### 時計

 - ![M5Stack 7-Segment Digi-Clock Unit](../../docs/amyboard/img/accessory_m5_digiclock.jpg)  
   [**M5Stack 7 セグメント Digi-Clock Unit**](https://shop.m5stack.com/products/red-7-segment-digit-clock-unit) -- I2C 経由の赤色 7 セグメント 4 桁。4 文字の文字列を渡します。

```python
import m5digiclock

m5digiclock.set("1234")
m5digiclock.set("AMY ")
```

## アクセサリを接続する

アクセサリはすべてフロントパネルの I2C ポートに挿します。複数の I2C デバイスをデイジーチェーン接続できます。

```python
import amyboard

# I2C バスをスキャンして接続中のアクセサリを確認
i2c = amyboard.get_i2c()
print(i2c.scan())
```




AMYboard で I2C デバイスを使う方法については、[Python を使う](python.md)と[モジュラーシンセのセットアップ](modular.md)も参照してください。

[はじめかたに戻る](README.md)
