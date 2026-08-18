# AMYboard のはじめかた

**AMYboard** へようこそ。29 ドルの、モジュラー対応の小さなボードに載った強力な音楽シンセサイザーです。AMYboard は [AMY シンセサイザー](https://github.com/shorepine/amy)を基盤とし、ESP32-S3 上で [MicroPython](https://micropython.org) を動かします。

**[AMYboard の FAQ もご覧ください。](faq.md)**

<img src="https://amyboard.com/img/amyboard_preview.png" width=500>

AMYboard でできること:

 - **128 個の Juno-6 アナログパッチ**と **128 個の DX7 FM パッチ**、加えてピアノ、ドラム、カスタム音源
 - **MIDI 入出力**（USB ガジェットと TRS）
 - モジュラーシンセ連携のための **CV 入出力**（-10V〜+10V）
 - デジタルオーディオのための **S/PDIF 入出力**
 - 追加ストレージとサンプル用の **SD カード**
 - エンコーダやディスプレイなどの[アクセサリ](accessories.md)を接続する **I2C ポート**
 - **Python でのプログラマビリティ** — シンセのあらゆるパラメータをコードで制御できます
 - パッチ設計とスケッチ管理のための [amyboard.com](https://amyboard.com/editor) の**ウェブエディタ**

AMYboard で問題が起きたら、[トラブルシューティングのページ](troubleshooting.md)を確認するか、[Discord](https://discord.gg/TzBFkUb8pG) で声をかけてください。

-- DAn と Brian

## 箱の中身

AMYboard は [Makerfabs](https://amyboard.com/#get) から出荷されます。箱にはボード本体、無地のアクリル製フロントパネル、取り付け済みのコネクタ、コネクタをパネルに固定するためのナットが入っています。パネルは Eurorack の 10HP 幅です。フロントパネルと Eurorack への取り付けについては[モジュラーシンセのセットアップ](modular.md)を参照してください。


## AMYboard の仕組み — ノブとスケッチ

デフォルトでは、私たちのファームウェアは AMYboard を「スケッチ」と呼ぶものとともに起動します。これは AMYboard 上で好きなことをさせられるコードで、シーケンスされた音楽を作ったり、シンセサイザーを設定したりと、いろいろできます。スケッチには「ノブ」の状態、つまりフィルタ、周波数、ADSR、LFO、エフェクトといったデフォルトシンセの各パラメータも含まれます。

[AMYboard online](online.md) を使えば、パッチを変更したり、新しいプリセットを読み込んだり、ファイル共有ネットワークである AMYboard World で他の人の面白いスケッチを見つけたり、AMYboard 上で動く自分のコードを書いたりできます。

AMYboard は起動すると、各チャンネルに設定されたパッチを構成し、それからユーザーが設定したコードがあればそれを実行します。このコードによって、優れたインタラクティブ環境を作れます。たとえば、デフォルトのパッチを読み込んだうえで、コードでそれを変更したり、CV や MIDI からの入力がパッチにどう作用するかを変えたりできます。

AMYboard World を眺めて、他の人のスケッチからインスピレーションを得るのもよいでしょう。ダウンロードして、自分の用途に合わせて変更できます。

私たちのカスタムファームウェアを外して（元に戻すのも簡単です）、[Arduino](arduino.md) 環境で AMYboard を動かすこともできます。その場合は AMYboard 上のすべてを完全に制御できます。

## ボードの概要

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/assets/img/amyboard_front_panel_annotated.jpg" width=500>

**フロントパネルのコネクタ**（上から下へ、ジャック 10 個）:

| コネクタ | 説明 |
|-----------|-------------|
| **S/PDIF in** | 3.5mm デジタルオーディオ入力 |
| **S/PDIF out** | 3.5mm デジタルオーディオ出力 |
| **Line in** | 3.5mm ステレオアナログ音声入力 — DIP スイッチで 10vpp にできます |
| **Line out** | 3.5mm ステレオアナログ音声出力 — DIP スイッチで 10vpp にできます |
| **MIDI in** | 3.5mm TRS の Type-A / B MIDI 入力 |
| **MIDI out** | 3.5mm TRS の Type-A / B MIDI 出力（ソフトウェアで切り替え可能） |
| **CV1 in** | 3.5mm アナログ入力、-10V〜+10V（ADS1015 ADC） |
| **CV1 out** | 3.5mm アナログ出力、-10V〜+10V（GP8413 DAC） |
| **CV2 in** | 3.5mm アナログ入力、-10V〜+10V（ADS1015 ADC） |
| **CV2 out** | 3.5mm アナログ出力、-10V〜+10V（GP8413 DAC） |


<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/assets/img/amyboard_back_panel_annotated.jpg" width=500>

**その他のコネクタ:**

| コネクタ | 位置 | 説明 |
|-----------|----------|-------------|
| **USB-C** | 側面 | 給電、シリアル REPL、USB MIDI（ガジェットモード。ホストではありません）、ファームウェア更新 |
| **I2C フロントパネル** | 前面 | [アクセサリ](accessories.md)（エンコーダ、ディスプレイ）用の I2C Grove ポート |
| **I2C host** | 背面 | [Tulip Creative Computer](https://github.com/shorepine/tulipcc) との接続用 |
| **MicroSD カード** | 側面 | サンプルやパッチ用の追加ストレージ |
| **モジュラー電源** | 背面 | 標準的な 10 ピンの Eurorack 電源コネクタ |
| **デバッグヘッダ** | 背面 | ファームウェア開発用 |


<a id="power-supplies"></a>
## 電源

AMYboard は 3 通りの方法で給電できます。

 * USB-C コネクタ: USB 経由の標準的な 5V 入力
 * モジュラーの 10 ピンコネクタ: 標準的な Eurorack のコネクタから +12V 経由で給電
 * I2C host: Grove コネクタ経由の Tulip Creative Computer やその他の 3.3V I2C 接続

複数の電源が接続されている場合、AMYboard は利用可能な最も高い電圧を使います。

I2C host / Grove ポートのみで給電する場合（たとえば Tulip から）、AMYboard はその 3.3V ラインから約 **350 mA** を消費します。ポートに給電する側にその余裕があることを確認してください。AMYboard を接続したあと Tulip が再起動を繰り返す場合は、下記の[クイックスタート - Tulip](#quick-start---tulip) を参照してください。

## DIP スイッチ

AMYboard の背面には、音声の入出力レベルを設定する 4 つの DIP スイッチがあります。AMYboard の使い方に応じて、4 つとも同じ向きに設定してください。

 - **ライン機器**（ヘッドホン、スピーカー、オーディオインターフェース、ミキサー）: 4 つとも **OFF** に設定します。つまり、各スイッチをボード背面の**上側**、10 ピン電源ジャック側に倒します。
 - **モジュラーシンセ**（10Vpp の Eurorack 信号）: 4 つとも **ON** に設定します。つまり、各スイッチをボードの**下側**、白いフォトカプラのチップ側に倒します。

10Vpp 動作の詳細は[モジュラーシンセのセットアップ](modular.md)のページを参照してください。

## ファームウェアのアップグレード

何よりも先にファームウェアをアップグレードしてください。いちばん簡単なのはブラウザからのオンライン更新です。方法の詳細は[ファームウェアアップグレードのページ](firmware.md)を参照してください。

## クイックスタート - 単体で使う

1. コンピュータから AMYboard へ **USB-C を接続**します。給電に加えて、シリアルと MIDI の接続が得られます。
2. 音声出力ジャックに**ヘッドホンかスピーカーを接続**します。
3. **MIDI コントローラを接続**して（USB MIDI か TRS MIDI で DAW に）演奏してみましょう。AMYboard はデフォルトで、MIDI チャンネル 1 に Juno-6 のパッチ #0 が割り当てられた状態で起動します。
4. **AMYboard online を試します。** [AMYboard online](online.md) を **Control モード**で使うと、シンセのパッチを変更したり、コード環境を試したり、AMYboard World で他の人のパッチやコードを見たりできます。MIDI 接続（TRS MIDI でも USB でも）経由で、コードやパッチを AMYboard に直接送れます。

## クイックスタート - モジュラーシンセ

1. **モジュラーの 10vpp 出力用に DIP スイッチを切り替えます。** 詳しくは[モジュラーのページ](modular.md)を参照してください。
2. **10 ピンのモジュラー電源を接続します。** 向きを間違えないよう、「キー」付きのケーブルを使ってください。
3. **CV、音声、MIDI のケーブルを接続します。** デフォルトでは AMYboard は MIDI チャンネル 1 でシンセパッチを鳴らしますが、できることはもっとたくさんあります。
4. Eurorack に組み込むと AMYboard の USB コネクタは隠れてしまうので、ケースに入れた AMYboard と [AMYboard online](online.md) の間でスケッチをやり取りするには TRS MIDI を使ってください。

<a id="quick-start---tulip"></a>
## クイックスタート - Tulip

[Tulip Creative Computer](https://tulip.computer) をお持ちなら、GROVE ポートと背面の I2C HOST コネクタで AMYboard を直接接続できます。Tulip が AMYboard に給電し、通信します。

> **⚠️ 電源に関する注意:** この方法で給電する場合、AMYboard は Tulip 自身の約 575 mA に加えて、Tulip の 3.3V レールから約 **350 mA** を引きます。弱い USB 充電器、充電専用や細い USB-C ケーブル、給電なしのハブ、あるいはバッテリー残量が少ない状態では、Tulip の電源電圧が下がって ESP32-S3 がブラウンアウトすることがあります。これは Tulip が**再起動を繰り返す**症状として現れます。その場合は、しっかりした **1〜2 A** の 5V 電源と良質なデータ対応 USB-C ケーブルで（ハブを介さず）Tulip に給電するか、**AMYboard に自前の USB-C から給電**してください。AMYboard は存在する最も高い電圧を使うので、Tulip から電流を引かずに自分の 5V で動きます。

Tulip 側では次のようにするだけです。

```python
from machine import I2C
i2c = I2C(0, freq=400000)
amy.override_send = lambda x: i2c.writeto(0x3f, x)
```
これで音はすべて AMYboard から出るようになります。

## ガイド

 - **[AMYboard Online を使う](online.md)** -- パッチ設計と管理のための amyboard.com のウェブエディタ
 - **[Arduino のセットアップ](arduino.md)** -- Arduino プロジェクトで AMY シンセエンジンを使う
 - **[モジュラーシンセのセットアップ](modular.md)** -- モジュラー環境との CV、ゲート、MIDI 連携
 - **[Python を使う](python.md)** -- MicroPython で AMYboard をプログラムする
 - **[コントロール API（MIDI SysEx）](control_api.md)** -- 自作スクリプトから AMYboard を操作する。双方向のファイル転送、シンセ状態のダンプ、Python の実行、再起動、エラーの取得
 - **[アクセサリ](accessories.md)** -- 対応するディスプレイ、エンコーダ、その他の I2C アクセサリ
 - **[トラブルシューティング](troubleshooting.md)** -- よくある問題とその解決方法
 - **[FAQ](faq.md)** -- AMYboard コミュニティからのよくある質問

## AMY についてもっと知る

AMYboard は、オープンソースのシンセサイザーエンジンである [AMY](https://github.com/shorepine/amy) で動いています。音源合成、パッチ、ワイヤプロトコルについてさらに知るには:

 - [AMY のシンセドキュメント](https://github.com/shorepine/amy/blob/main/docs/synth.md)
 - [AMY の API リファレンス](https://github.com/shorepine/amy/blob/main/docs/api.md)
 - [AMY の MIDI ドキュメント](https://github.com/shorepine/amy/blob/main/docs/midi.md)

## コミュニティ

[![shore pine sound systems discord](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/shorepine100.png) **Discord で AMYboard について語り合いましょう。**](https://discord.gg/TzBFkUb8pG)
