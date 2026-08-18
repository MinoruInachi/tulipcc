# Tulip Creative Computer ボード

##  🌈🌈 リビジョン 9 🌈🌈
このページは**リビジョン 9** 向けです。

![Tulip Board](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/nicoboard-pcb.png)

TulipCC ファンの NicoVR が、統合型の [Tulip CC](../README.md) ボードを設計しました。自分で Tulip を DIY する方法としては最も素晴らしいものです。ディスプレイ FPC コネクタ、充電・給電・書き込み用の USB-C コネクタ、USB キーボードコネクタ、オーディオ + ヘッドホン出力・MIDI 入力・MIDI 出力の 3.5mm ジャック 3 つ、電源スイッチ、I2C の "grove / stemma" ヘッダを備えています。

表面実装のはんだ付けに慣れているなら、自分でボードを組み立てるのは比較的簡単です。PCB は OSH Park、PCBway、JLCPCB といった任意の基板メーカーに発注できます。BOM ファイルをサービスにアップロードして、ボード全体を製造してもらうこともできます。

[最新リビジョンの BOM はこちらです。](https://github.com/shorepine/tulipcc/blob/main/docs/pcbs/tulip4_board_v4r9/tulipcc-bom.xlsx) [KiCad ファイルはこちらです。](https://github.com/shorepine/tulipcc/tree/main/docs/pcbs/tulip4_board_v4r9)

USB キーボードは_ほぼ_どれでも動作します。

これは難しすぎると感じるなら、代わりに[スルーホールのはんだ付けだけでブレークアウトボードを作る](tulip_breakout.md)か、[はんだ付けなしでブレッドボードに組む](tulip_breadboard.md)こともできます。

![Tulip Board](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/nicoboard-assembled.jpg)


## 組み立てのコツ

Tulip の組み立てには、ホットプレートとステンシルを使うのが一番楽だと感じています。ステンシルは基板を発注するときに PCB メーカーから入手できます。

FPC ケーブルはディスプレイの RGB ポートに接続します。ディスプレイに付属のケーブルが使えます。


## 電源

ボードは USB-C からでもバッテリーからでも給電できます。

![With Alles](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/nicoboard-alles.jpg)

## MIDI

3.5mm の MIDI ジャックは Type A の TRS MIDI コネクタ用です。フルサイズの MIDI コネクタに配線したい場合は、[Type A 変換アダプタ](https://www.amazon.com/ZAWDIO-Breakout-LittleBits-Female-Electribe/dp/B08WHSP7ZL/)を入手してください。機材の多くが Type B なら、Type A から Type B への変換ケーブルも入手できます。

## 書き込みと起動

組み立てが終わったら、[Tulip のコンパイルと書き込みの方法を読んでください。](tulip_flashing.md)
