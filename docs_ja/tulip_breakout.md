# Tulip ブレークアウトボード版

![Tulip Breakout](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/breakout.jpg)


[Tulip CC](../README.md) を恒久的な形で作りたくて、スルーホールのはんだ付けができるなら、[私が設計したこのブレークアウト PCB を OSH Park で購入できます。](https://oshpark.com/shared_projects/L1xtM8pM)

もっとオールインワンなものを作りたければ、代わりに[完全統合の表面実装ボード](tulip_board.md)を作れますし、暫定的なものを試したい、あるいははんだ付けをしたくないなら、[ブレッドボードで Tulip を組む](tulip_breadboard.md)こともできます。

[ブレークアウト基板の PCB は 3 枚で 30 ドル](https://oshpark.com/shared_projects/L1xtM8pM)です（この代金はすべて基板を製造する OSH Park に渡り、私は何も受け取っていません）。出荷までおよそ 1 週間かかります。この PCB は、ジャンパワイヤを使わずにブレークアウトボード同士を接続するためだけのものです。

[このブレークアウト PCB の Eagle ファイルはこちらです。](https://github.com/shorepine/tulipcc/tree/main/docs/pcbs/tulip4_breakout_v3)

**注意**: 現行版のブレークアウトボード Tulip はジョイスティックポートに対応していません。近いうちに追加します。どうしても必要な場合は、ブレッドボード版か（r7 以降の）統合ボード版を作ってください。

![Tulip Breakout](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/breakout_bare.png)


必要なもの:

- [ESP32-S3 WROOM-1 N32R8 開発ボード](https://www.adafruit.com/product/5364)。N32R8 は 32MB のフラッシュを搭載しています。16MB フラッシュの N16R8 も使えます。
- [静電容量式タッチパネル付きの 58 ドルの RGB ドットクロック 10.1 インチディスプレイ。](https://www.hotmcu.com/101-inch-1024x600-tft-lcd-display-with-capacitive-touch-panel-p-215.html) 他のサイズや解像度の RGB ドットクロックディスプレイも使えますが、ピン番号が異なるため、コード内の解像度を更新する必要があります。
- [ディスプレイ用の 40 ピン FPC ヘッダ。](https://www.adafruit.com/product/4905)
- ステレオライン出力／ヘッドホンジャックの [UDA1334 DAC。](https://www.aliexpress.com/item/3256803337983466.html?gatewayAdapt=4itemAdapt)
- USB キーボードは_ほぼ_どれでも動作します。
- コネクタとその他の部品:
   - [ESP とオーディオジャックを PCB に直接はんだ付けしなくて済むよう、メスヘッダの使用を推奨します。](https://www.adafruit.com/product/598)
   - ディスプレイ FPC ブレークアウト用に、この [2x20 シュラウド付きヘッダ](https://www.adafruit.com/product/1993)も入手するとよいでしょう。
   - [USB メス A コネクタ 1 個](https://www.amazon.com/Uxcell-a13081900ux0112-Female-Socket-Connector/dp/B00H51E7B0)
   - [5 ピン DIN MIDI メスジャック 2 個](https://www.adafruit.com/product/1134)
   - [6N138 フォトカプラ 1 個](https://www.amazon.com/Optocoupler-Single-Channel-Darlington-Output/dp/B07DLTSXC1)と [8 ピンソケット](https://www.adafruit.com/product/2202)
   - [抵抗 7 本](https://www.amazon.com/BOJACK-Values-Resistor-Resistors-Assortment/dp/B08FD1XVL6): `R1`: 4.7K、`R2`: 4.7K、`R3`: 4.7K、`R4`: 220、`R5`: 470、`R6`: 33、`R7`: 10。`R4` から `R7` は厳密にこの値である必要はなく、手持ちで最も近い値を使ってください。
   - ダイオード 1 個: [1N4001](https://www.adafruit.com/product/755)

ブレークアウト PCB の組み立ては簡単です。DISPLAY、ESP32S3L、ESP32S3R、AUDIO の各列にヘッダをはんだ付けします。6N138 の位置に 8 ピンソケットをはんだ付けします。抵抗を正しい位置に、ダイオードを（極性に注意して）はんだ付けします。USB コネクタをはんだ付けします。MIDI コネクタをはんだ付けします。あとは ESP32-S3 ブレークアウトを差し込み、付属の FPC コネクタを（下向きに、FPC ケーブルが基板から離れる向きで）取り付け、I2S ボードを差し込み、6N138 を差し込み、USB キーボードとディスプレイを FPC コネクタに接続します（ディスプレイ側の "RGB" 入力へ。コネクタの両側とも青い面を上に向けます）。以上です。

組み立てが終わったら、[Tulip のコンパイルと書き込みの方法を読んでください。](tulip_flashing.md)

質問がありますか？ [ディスカッションページでお気軽にどうぞ。](https://github.com/shorepine/tulipcc/discussions)
