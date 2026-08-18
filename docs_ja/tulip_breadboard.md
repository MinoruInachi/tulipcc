# Tulip ブレッドボード版

![Tulip Breadboard](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/breadboard_display.jpg)

[Tulip CC](../README.md) は、ESP32S3 ブレークアウトボードをディスプレイとオーディオボードにつなぐだけで、はんだ付けなしに簡単に組み立てられます。ブレッドボードとたくさんのジャンパワイヤがあれば、1 時間もかからずに Tulip を組み上げられます。

もっと恒久的なものを作りたければ、代わりに[スルーホールのはんだ付けだけでブレークアウトボードを作る](tulip_breakout.md)か、[完全統合の表面実装ボードを作る](tulip_board.md)こともできます。


必要なもの:

- ブレッドボード
- [ESP32-S3 WROOM-1 N32R8 開発ボード](https://www.adafruit.com/product/5364)。N32R8 は 32MB のフラッシュを搭載しています。16MB フラッシュの N16R8 も使えます。
- [静電容量式タッチパネル付きの 58 ドルの RGB ドットクロック 10.1 インチディスプレイ。](https://www.hotmcu.com/101-inch-1024x600-tft-lcd-display-with-capacitive-touch-panel-p-215.html) 他のサイズや解像度の RGB ドットクロックディスプレイも使えますが、ピン番号が異なるため、コード内の解像度を更新する必要があります。
- [ディスプレイ用の 40 ピン FPC ヘッダ。](https://www.adafruit.com/product/4905)
- 音声出力は次の 2 択です。[このモノラル I2S スピーカーアンプボード](https://www.adafruit.com/product/3006)（3W スピーカーも必要）か、ステレオライン出力／ヘッドホンジャックの [UDA1334 DAC。](https://www.aliexpress.com/item/3256803337983466.html?gatewayAdapt=4itemAdapt)
- USB キーボードは_ほぼ_どれでも動作します。
- オプションの NES / SNES ジョイスティックに対応させたい場合は、[適切なコネクタを入手してください。](https://www.zedlabz.com/collections/retro-nintendo-snes/products/zedlabz-7-pin-90-degree-female-controller-connector-port-for-nintendo-snes-console-2-pack-grey)
- コネクタとその他の部品:
   - [USB メス A ネジ端子台 1 個](https://www.amazon.com/Poyiccot-Terminal-Connector-Converter-Breakout/dp/B08Y8NKGHL)
   - [5 ピン DIN MIDI メスジャック 2 個](https://www.adafruit.com/product/1134)
   - [6N138 フォトカプラ 1 個](https://www.amazon.com/Optocoupler-Single-Channel-Darlington-Output/dp/B07DLTSXC1)と [8 ピンソケット](https://www.adafruit.com/product/2202)
   - [抵抗 7 本](https://www.amazon.com/BOJACK-Values-Resistor-Resistors-Assortment/dp/B08FD1XVL6): `R1`: 4.7K、`R2`: 4.7K、`R3`: 4.7K、`R4`: 220、`R5`: 470、`R6`: 33、`R7`: 10。`R4` から `R7` は厳密にこの値である必要はなく、手持ちで最も近い値を使ってください。
   - ダイオード 1 個: [1N4001](https://www.adafruit.com/product/755)

必要な配線は次のとおりです。なお、ディスプレイのピン番号（D#）は、**FPC コネクタがある側の基板面**（FPC-40P 0.5MM と書かれている面）の番号に合わせています。

| ラベル        | ESP32 S3 ピン | ESP32-S3-WROOM-1 上の位置    | 接続先         |
| ------------- | ------------ | ---------------------------- | -------------- |
| バックライト PWM | 16        | 左列、上から 9 番目（L9）    | Display 6 (D6) |
| Data Enable   | 42           | 右列、上から 6 番目（R6）    | D7             |
| VSYNC         | 41           | R7                           | D8             |
| HSYNC         | 40           | R8                           | D9             |
| LCD BL EN     | 39           | R9                           | D10            |
| PCLK          | 14           | L20                          | D11            |
| B7            | 21           | R18                          | D13            |
| B6            | 12           | L18                          | D14            |
| B5            | GND          | L22                          | D15            |
| G7            | 46           | L14                          | D21            |
| G6            | 3            | L13                          | D22            |
| G5            | 8            | L12                          | D23            |
| R7            | 15           | L8                           | D29            |
| R6            | 7            | L7                           | D30            |
| R5            | 6            | L6                           | D31            |
| 3v3           | 3v3          | L1                           | D37            |
| GND           | GND          | L22                          | D38            |
| 5V            | 5V           | L21                          | D39            |
| 5V            | 5V           | L21                          | D40            |
| Touch SDA     | 18           | L11                          | D4             |
| Touch SCL     | 17           | L10                          | D3             |
| Touch CTP INT | 5            | L5                           | D1             |
| Touch CTP RST | 未接続       | 未接続                       | D2             |
| USB 5V        | 5V           | L21                          | USB 5V         |
| USB D+        | 20           | R19                          | USB D+         |
| USB D-        | 19           | R20                          | USB D-         |
| USB GND       | GND          | L22                          | USB GND        |
| Audio LRC     | 4            | L4                           | Audio LRC      |
| Audio BCLK    | 1            | R4                           | Audio BCLK     |
| Audio DIN     | 2            | R5                           | Audio DIN      |
| Audio GND     | GND          | L22                          | Audio GND      |
| Audio VIN     | 5V           | L21                          | Audio VIN      |
| MIDI in       | 47           | R17                          | MIDI TX        |
| MIDI out      | 11           | L17                          | MIDI RX        |
| MIDI 5V       | 5V           | L21                          | MIDI 5v        |
| MIDI GND      | GND          | L22                          | MIDI GND       |
| Joy CLOCK     | 13           | L19                          | Joy CLOCK      |
| Joy LATCH     | 48           | R16                          | Joy LATCH      |
| Joy DATA      | 45           | R15                          | Joy DATA       |
| Joy 5V        | 5V           | L21                          | Joy 5V         |
| Joy GND       | GND          | L22                          | Joy GND        |

FPC ケーブルはディスプレイの "RGB" ポートに配線します。ディスプレイに付属のケーブルで問題ありません。両端とも青い面を上に向けてください。

また、ちらつきが見られる場合は、残りのディスプレイピンをすべて GND に落とすとよいかもしれません。ただし `D2` には何も接続しないでください。ここは未接続のままにします。

[MIDI 入出力の配線方法も確認しておくとよいでしょう。](https://diyelectromusic.wordpress.com/2021/02/15/midi-in-for-3-3v-microcontrollers/) 該当するのは `R3`、`R4`、`R5`、`R6`、`R7` と 6N138、ダイオードです。（[Adafruit のこのような MIDI 入出力ブレークアウトボードを買えば手間を省けます](https://www.adafruit.com/product/4740)。その場合は ESP の MIDI 入出力に直接配線するだけです。）

Touch SDA と Touch SCL は、4.7K の抵抗で 3.3V にプルアップする必要があります。ESP のこれらのピンから基板上のどこかの 3.3V へ、抵抗 `R1` と `R2` を渡してください。

組み立てが終わったら、[Tulip のコンパイルと書き込みの方法を読んでください。](tulip_flashing.md)

質問がありますか？ [ディスカッションページでお気軽にどうぞ。](https://github.com/shorepine/tulipcc/discussions)

![Tulip Breadboard](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/breadboard_close.jpg)
