# AMYboard の Arduino セットアップ

AMYboard はデフォルトで MicroPython のファームウェアを動かしますが、その基盤である **AMY シンセサイザーエンジン**はスタンドアロンの Arduino ライブラリとしても利用できます。つまり、Arduino のスケッチから AMYboard の ESP32-S3 ハードウェア（あるいは[他の多くのボード](https://github.com/shorepine/amy/blob/main/docs/arduino.md)）で AMY の音源を使えます。

### 1. AMY ライブラリと AMYboard のボードサポートをインストールする

AMY ライブラリと AMYboard のボードサポートは、どちらも Arduino のライブラリマネージャから入手できます。次の順に進めてください。

<img src="https://github.com/shorepine/tulipcc/raw/main/docs/amyboard/img/arduino-board.jpg" width="400"/>

 1. **ESP32 のボードサポートを更新またはインストール**: すでに `esp32 by Espressif` のボードパッケージが入っている場合は、バージョン 3.3.8 以上であることを確認してください。まだ入っていない場合は、Arduino IDE の **File > Preferences** で ESP32 のボードマネージャ URL `https://espressif.github.io/arduino-esp32/package_esp32_index.json` を追加します。その後 **Tools > Board > Boards Manager** を開き、Espressif の **"esp32"** を検索してインストールします。
 2. **AMYboard のボードを選択**: **Tools > Board > ESP32 Arduino** から **AMYboard** を選びます（いちばん下にあります）。
 3. **AMY ライブラリをインストール**: **Sketch > Include Library > Manage Libraries** を開き、**"AMY"** を検索してインストールします。

<img src="https://github.com/shorepine/amy/raw/main/docs/arduino1.png" width="400"/>

 4. サンプルスケッチを開きます: **File > Examples > AMY > AMY_MIDI_Synth**
 5. ボードとして AMYboard が選択されていることを確認し、**Upload** をクリックします。

このサンプルは MIDI 入力を受け取って Juno-6 のパッチを鳴らします。どんなプロジェクトでも良い出発点になります。

### 2. Arduino のスケッチを AMYboard に書き込む

特別な操作なしに USB-C 経由でそのまま書き込めるはずです。アップロードエラーが出る場合は、AMYboard を DFU（ブートローダ）モードにしてください。

 1. ボード裏面の **BOOT** と **RST** の両ボタンを押し続けます。
 2. 先に **RST** を離し、次に **BOOT** を離します。両方のボタンの上を下から上へ指を「転がす」ような感覚です。
 3. もう一度アップロードを試します。

いつでも標準の [AMYboard MicroPython ファームウェア](firmware.md)に戻せます。

## 最小の Arduino スケッチ

```c
#include <AMY-Arduino.h>

void setup() {
  amy_config_t config = amy_default_config();
  amy_config.features.default_synths = 1;
  amy_start(config);
}

void loop() {
  amy_update();
}
```

この最小スケッチは USB 経由の MIDI 入力に反応します。コンピュータや DAW の MIDI 入力デバイス一覧で "AMYboard" を探してください。MIDI ノートを送ればデフォルトの Juno-6 パッチが鳴ります。

シーケンサのデモやマルチボイス構成など、他のアイデアは **File > Examples > AMY** の他のサンプルを見てみてください。

## Arduino からノートを鳴らす

プリセットのシンセパッチを読み込む:
```c
amy_event e = amy_default_event();
e.synth = 1;         // 設定対象のシンセ。
e.patch_number = 6;  // Juno A17 Choir のプリセットパッチ。
e.num_voices = 4;    // 同時発音数をいくつ確保するか。
amy_add_event(&e);
```
設定済みのシンセでノートを鳴らす:
```c
amy_event e = amy_default_event();
e.synth = 1;         // 使うシンセ
e.midi_note = 60;    // 中央のド
e.velocity = 1;      // 最大の強さ
amy_add_event(&e);
```

## さらに詳しく

 - [AMY の Arduino 入門ガイド](https://github.com/shorepine/amy/blob/main/docs/arduino.md) -- サポートされている全ボードの詳細なセットアップ
 - [AMY の API リファレンス](https://github.com/shorepine/amy/blob/main/docs/api.md) -- C API の完全なドキュメント
 - [対応ボードと機能の一覧表](https://github.com/shorepine/amy/issues/354)

[はじめかたに戻る](README.md)
