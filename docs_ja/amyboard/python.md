# Python を使う

AMYboard は MicroPython で動作し、シンセサイザーの制御、入力の読み取り、楽器やエフェクトのリアルタイム構築ができる完全な Python REPL を提供します。

## REPL に接続する

### コンピュータから

AMYboard を USB-C で接続し、シリアルターミナルか `mpremote` を使います。

```bash
pip install mpremote
mpremote resume
```

MicroPython の `>>>` プロンプトが表示されます。Python のコマンドを直接入力できます。

### ウェブから

[amyboard.com](https://amyboard.com) を開いてください。ブラウザ内で動く Python REPL が含まれています。実機に展開する前に、オンラインでコードを試せます。

### Wi-Fi 経由（WebREPL）

AMYboard は MicroPython の **WebREPL** を使って、Wi-Fi ネットワーク越しに REPL を公開することもできます。USB ケーブルなしに、ブラウザから無線で接続できます。ネットワークに接続してから、パスワード（9 文字まで）を指定して WebREPL サーバーを起動します。

```python
import amyboard, webrepl

amyboard.wifi('your_ssid', 'your_password')  # Wi-Fi に接続（ボードの IP を返します）
webrepl.start(password='amyboard')           # ポート 8266 でサーバーを起動
```

`webrepl.start()` は待ち受けアドレスを表示します。たとえば次のようになります。

```
WebREPL server started on http://192.168.1.42:8266/
```

接続するには、[micropython.org/webrepl](https://micropython.org/webrepl/) の WebREPL クライアントを開き、`ws://192.168.1.42:8266/`（自分のボードの IP）を入力して **Connect** をクリックし、パスワードを入力します。これでネットワーク越しに完全な `>>>` プロンプトが使えます。同じクライアントでボードとのファイル転送も行えます。

毎回の起動時に WebREPL を自動で開始したい場合は、上の 3 行（`amyboard.wifi(...)` を含む）を `sketch.py` に追加してください。下記の [sketch.py 起動スクリプト](#the-sketchpy-startup-script)を参照してください。

> **セキュリティ:** WebREPL は、パスワードを知るネットワーク上の誰にでも AMYboard の完全な制御を許します。信頼できるネットワークでのみ使い、単純でないパスワードを選んでください。

## AMY で音を鳴らす

`amy` モジュールはシンセエンジンに直接アクセスできます。

```python
import amy

# 既存の設定をクリア
amy.reset()

# 440Hz のサイン波を鳴らす
amy.send(osc=0, wave=amy.SINE, freq=440, vel=1)

# 音を止める
amy.send(osc=0, vel=0)
```

### パッチを使う

AMY には数百の内蔵パッチがあります。0〜127 が Juno-6 アナログ、128〜255 が DX7 の FM、256 がピアノ、384〜390 が General MIDI のドラムキット（384 TR-808、385 TR-909、386 Linn 9000、387 Univox MR-12、388 Tokyo Synthetics、389 80s Power Kit、390 Percussion）です。シンセにキットを読み込み、GM のノート番号（36=キック、38=スネア、42=ハイハット…）を鳴らせばドラムがトリガされます。キットの切り替えは `amy.send(synth=10, patch=38x)` か、MIDI ならバンクセレクト MSB 3（CC0=3）＋プログラムチェンジ 0〜6 で行います。
一般に、1 つのパッチは複数の osc を使います。`synth` は、複数の osc をまとめて管理するための抽象化です。

```python
# シンセ 1 に Juno-6 のパッチ #10 を読み込む
amy.send(synth=1, patch=10, num_voices=4)

# 中央のドを鳴らす
amy.send(synth=1, note=60, vel=1)

# ノートオフを送る
amy.send(synth=1, note=60, vel=0)

# DX7 のパッチ #5 に切り替える
amy.send(synth=1, patch=133)  # 128 + 5
```

### synth によるポリフォニー

`synth` オブジェクトは指定した数のポリフォニーを提供します。MIDI チャンネルにも直接つながります。

```python
# シンセ 1（MIDI チャンネル 1）に 4 ボイスポリの Juno パッチを設定します。
amy.send(synth=1, patch=0, num_voices=4)

# 個々のボイスで音を鳴らす
amy.send(synth=1, note=60, vel=1)  # ド
amy.send(synth=1, note=64, vel=1)  # ミ
amy.send(synth=1, note=67, vel=1)  # ソ

# ノート番号なしで vel=0 を送ると「オールノートオフ」になります
amy.send(synth=1, vel=0)

# "MIDI in" ジャックに接続した MIDI キーボードや、AMYboard の USB MIDI ガジェットに書き込むコンピュータから
# チャンネル 1 にノートを送れば、ポリフォニックに演奏できます。
```

### C で自作の DSP を書く

AMYboard は **ボード本体で** C のコードをコンパイルし、シンセサイザーの内部で実行できます。バスの FX チェーンの末尾に置くカスタムエフェクトや、まったく新しいオシレータの種類を、音を鳴らしたまま Python からホットスワップできます。

```python
import tulip
tulip.install_c_process('crush', """
    int i = 0;
    while (i < frames * chans) {
        buf[i] = (buf[i] >> 18) << 18;   // ビットクラッシュ！
        i = i + 1;
    }
""")
tulip.c_process('crush', True)
```

サンプル形式、オシレータの API、実例（ビットクラッシャー、ヘビーなディストーション、CZ-101 風のフェイズディストーションオシレータ、バイトビート）を含む完全なガイドは、[C でオーディオエフェクトとオシレータを書く](../user_c_dsp.md)を参照してください。

## `amyboard` モジュール

`amyboard` モジュールは AMYboard のハードウェア固有の機能を提供します。

```python
import amyboard

# CV の入出力
amyboard.cv_out(5.0, channel=0)    # CV out 1 に 5V を出力
volts = amyboard.cv_in(channel=0)   # CV in 1 を読む

# ロータリーエンコーダ。amyboard.encoder() は接続されているアクセサリ
# （Adafruit のシングル／クアッド、M5Stack の 8Encoder）をすべて自動検出し、
# 共通の API を提供します。複数の基板（アドレスジャンパで区別）は
# 1 つのフラットなインデックス空間に統合されます。
enc = amyboard.encoder()            # 逆向きに数える個体なら invert=True を渡します
print(enc.type, enc.encoders)       # 例: "m5stack" 8。enc.devices は (type, addr) の一覧
print(enc.read(0))                  # エンコーダ 0 の位置（0 から始まります）
print(enc.button(0))                # 押している間 True
if enc.leds:
    enc.led(0, 0, 64, 0)            # エンコーダ 0 の LED を暗い緑に
# 複数基板の詳細、invert、レガシーなデバイスごとの read_encoder()/read_buttons()
# ヘルパーについては accessories.md を参照してください。

# OLED ディスプレイ（接続されている場合）
amyboard.init_display()
amyboard.display.fill(0)
amyboard.display_refresh()

# OLED を回転（sh1107 のみ）。0, 90, 180, 270 度のいずれか。
# 設定は保存され、起動のたびに自動で再適用されます。
amyboard.set_display_rotation(90)
print(amyboard.display_rotation())   # -> 90

# I2C
i2c = amyboard.get_i2c()
devices = i2c.scan()
print(devices)

# SD カード
amyboard.mount_sd()
```

CV とエンコーダの詳しい例は[モジュラーシンセのセットアップ](modular.md)のページを参照してください。

<a id="the-sketchpy-startup-script"></a>
## sketch.py 起動スクリプト

AMYboard は起動時に、現在の環境ディレクトリにある `sketch.py` を自動的に実行します。トップレベルのコードは起動時に一度だけ実行され、`loop(tick)` 関数を定義していれば、シーケンサの 32 分音符ごとに呼ばれます。間隔は `7500 / tempo` ms なので、デフォルトのテンポ 108 では 69 ms、テンポ 60 では 125 ms です。より速いコールバックが必要な場合は [`loop()` はどのくらいの頻度で呼ばれますか？](faq.md#how-often-is-loop-called)を参照してください。既定の構成を設定するのに使います。

```python
# /user/current/sketch.py
import amy, amyboard

# 好みのパッチを設定
amy.send(synth=1, patch=0, num_voices=6)      # チャンネル 1: Juno のパッチ 0、6 ボイスポリ
amy.send(synth=10, patch=384)                 # チャンネル 10: TR-808 の GM ドラムキット（ノート 36=キック…。num_voices はデフォルトの 1 を使用）。キット 385-390: 909/Linn/MR-12/Synthetics/Power/Percussion。

# 起動時に CV out 1 を 0V にする
amyboard.cv_out(0.0, channel=0)

def loop(tick):
    pass
```

`sketch.py` は内蔵のテキストエディタを使って実機上で直接編集できます。`screen` か `mpremote` で AMYboard に接続してください（`idf.py monitor` は完全な端末エミュレーションに対応していないので使えません）。

```bash
screen /dev/YOUR_SERIAL_PORT 115200
# または
mpremote connect /dev/YOUR_SERIAL_PORT
```

REPL で次のようにします。

```python
edit('current/sketch.py')
```

全画面のテキストエディタが開きます。macOS では Ctrl の代わりに **Esc の後にキー**を押してください（macOS のターミナルは多くの Ctrl シーケンスを横取りします）。

 - **Esc, S** — 保存
 - **Esc, Q** — 終了
 - **Esc, X** — 行の切り取り
 - **Esc, V** — 貼り付け
 - **Esc, Z** — 元に戻す
 - **Esc, F** — 検索

Linux や Windows のターミナルでは Ctrl-S、Ctrl-Q などが通常どおり使えます。キーバインドの一覧は [pye のドキュメント](https://github.com/robert-hh/Micropython-Editor)を参照してください。

[AMYboard Online](https://amyboard.com/editor) でファイルを作成・編集し、**Send to AMYboard** で実機に送ることもできます。

## ファイル管理

### 実機上で

```python
from upysh import *

ls                  # ファイル一覧
cat('sketch.py')    # ファイルの内容を表示
cd('/user')         # ディレクトリを移動
pwd                 # 現在のディレクトリを表示
```

### mpremote でファイルを転送する

```bash
# コンピュータから AMYboard にファイルをコピー
mpremote resume fs cp my_script.py :my_script.py

# AMYboard からコンピュータにファイルをコピー
mpremote resume fs cp :sketch.py sketch.py

# ローカルのエディタで AMYboard 上のファイルを編集
mpremote resume edit sketch.py
```

### microSD カードでファイルを転送する

まず、コンピュータで必要なファイルをカードにコピーします。次にカードを AMYboard に挿して再起動します（あるいは `amyboard.mount_sd()` を試します）。その後、Python から直接そのファイルにアクセスするか、ユーザーストレージにコピーできます: `cp('/sd/file.wav', '/user/file.wav')`。


## Python で MIDI を扱う

MIDI を受信するには、`midi.add_callback()` でコールバックを登録します。メッセージが届くたびに、`bytes` オブジェクトとしてメッセージを受け取って関数が呼ばれます。

```python
import midi

def my_midi_callback(message):
    status = message[0]
    if status & 0xF0 == 0x90:  # ノートオン
        note = message[1]
        vel = message[2]
        print(f"Note on: {note} velocity: {vel}")

midi.add_callback(my_midi_callback)
# ... 受信をやめるときは:
midi.remove_callback(my_midi_callback)
```

コールバックはいくつでも登録でき、すべてがすべてのメッセージを受け取ります。AMYboard 自身の機能（`amyboard.show_midi_ccs()` など）もこの方法で MIDI を待ち受けており、工場出荷時の自己診断も MIDI ループバックのチェックに同じパターンを使っています。

なお、AMYboard のシステム MIDI ディスパッチャは起動時から受信キューを吸い出しているので、自分で `tulip.midi_in()` をポーリングしても常に `None` が返ります。ディスパッチャがすでに各メッセージを消費し、登録済みのコールバックに渡しているためです。同様に、`tulip.midi_callback()` を直接呼ばないでください。システムのディスパッチャを置き換えてしまい、MIDI を待ち受ける他のすべてを黙って壊します。必ず `midi.add_callback()` / `midi.remove_callback()` を使ってください。

sysex メッセージについては、`midi.sysex_callback` に関数を設定します。sysex のペイロード全体を `bytes` として受け取ります。

```python
import midi

def my_sysex_callback(payload):
    print("sysex:", payload.hex())

midi.sysex_callback = my_sysex_callback
```

### MIDI を送出する

AMYboard から MIDI を送出する方法は 2 つあります。

単発や任意のメッセージには、生の MIDI バイト列（リスト、タプル、`bytes`）を渡す `tulip.midi_out()` を使います。即座に、MIDI out ジャックと USB MIDI 接続の両方へ送出されます。

```python
import tulip

tulip.midi_out([0x90, 60, 127])  # ノートオン、チャンネル 1、ノート 60、ベロシティ 127
tulip.midi_out([0x80, 60, 0])    # ノートオフ、チャンネル 1、ノート 60
tulip.midi_out([0xB0, 74, 64])   # コントロールチェンジ、チャンネル 1、CC 74 = 64
```

音楽的なタイミングでの出力には、AMY に MIDI を送らせます。オシレータの `wave` を `amy.AMY_MIDI` に設定すると、その osc は音を出す代わりに、ノートを受け取るたびに MIDI のノートオン／オフを送出します。通常の osc なので AMY のシーケンサから駆動でき、AMY のテンポにロックされたサンプル精度の MIDI 出力が得られます。たとえば外部シンセを鳴らすパターンは次のようになります。

```python
import amy

amy.send(osc=0, wave=amy.AMY_MIDI)                    # osc 0 は音声ではなく MIDI を送出するようになります

# 4 分音符（48 ティック）ごとにチャンネル 1 の MIDI ノートを鳴らし、8 分音符分保持します。
amy.send(osc=0, note=60, vel=1, ticks="0,48,1")    # 各周期のティック 0 でノートオン
amy.send(osc=0, note=60, vel=0, ticks="24,48,2")   # 各周期のティック 24 でノートオフ
```

`amy.AMY_MIDI` は常に MIDI チャンネル 1 に送出し、MIDI 入力から入ってきたノートはそのまま送り返されません。詳細は [AMY の MIDI ドキュメント](https://github.com/shorepine/amy/blob/main/docs/midi.md#sending-midi-out)を参照してください。

#### MIDI out の TRS タイプ（A と B）

MIDI out ジャックは 3.5mm の TRS コネクタで、これには互換性のない 2 つの配線規格があります。**Type A**（MIDI 規格の公式標準で、最近の機材の多くが採用）と **Type B**（古い規格で、初期の Arturia / Novation / Akai の一部機器が採用）です。TRS のチップとリングが入れ替わっているので、外部機器が MIDI を受け取れない場合は、もう一方のタイプを期待している可能性が高いです。AMYboard のデフォルトは **Type A** です。影響を受けるのは MIDI out のみで、MIDI in はどちらのタイプでも動きます。

実行時の切り替えは `amyboard.set_midi_type()` で行います（再起動は不要で、AMY は動いたままです）。

```python
import amyboard

amyboard.set_midi_type('B')   # MIDI out を Type B に切り替え
amyboard.set_midi_type('A')   # Type A（デフォルト）に戻す
amyboard.midi_type()          # -> 現在有効な 'A' か 'B'
```

これは再起動をまたいで保持されません。AMYboard は常に Type A で起動します。

### MIDI クロックへの同期

AMYboard のシーケンサはデフォルトでは自前の内部クロックで動きます。外部の MIDI クロックに**追従**して DAW やドラムマシンにスレーブすることも、MIDI クロックを**送出**して AMYboard から他の機材を駆動することもできます。

**外部クロックに追従する。** 外部リアルタイム同期はデフォルトでオフで、入ってくるクロックは無視され、AMYboard は自分のテンポを保ちます。`tulip.external_midi_sync()` で有効にします。

```python
import tulip

tulip.external_midi_sync(True)   # 入ってくる MIDI クロックに追従
tulip.external_midi_sync(False)  # AMYboard の内部クロックに戻す（デフォルト）
```

追従が有効な間、MIDI in（DIN ジャックまたは USB）に届くリアルタイムメッセージがシーケンサを駆動します。`F8`（タイミングクロック）がテンポを設定してシーケンサを歩調を合わせて進め（4 分音符あたり 24 クロック）、`FA`（スタート）が開始し、`FC`（ストップ）が停止します。スタート／ストップは同期が有効なときにのみ効くので、明示的に有効化しないかぎり、接続した DAW が AMYboard のパターンを勝手に開始・停止することはありません。同期をオフに戻せば内部クロックに戻ります。

**クロックを送出する。** 「同期を送る」という専用のスイッチはありません。（上記と同様に MIDI out ジャックと USB へ出す）`tulip.midi_out()` で自分でリアルタイムバイトを送出してください。

```python
tulip.midi_out([0xFA])   # スタート
tulip.midi_out([0xF8])   # タイミングクロック 1 ティック分 — 4 分音符あたり 24 回送ります
tulip.midi_out([0xFC])   # ストップ
```

AMYboard 自身のテンポにロックした安定したクロックを送るには、シーケンサのコールバックから `F8` を送出します。シーケンサは（上記のとおり）4 分音符あたり 48 ティックで動き、MIDI クロックは 24 なので、1 つおきのティックで送ります（`period=2`）。

```python
import tulip, sequencer

sequencer.tempo(120)             # AMYboard のシーケンサのテンポ（BPM）

def send_clock(tick):
    tulip.midi_out([0xF8])

tulip.midi_out([0xFA])                           # 下流の機材にスタートを伝える
slot = tulip.seq_add_callback(send_clock, 0, 2)  # 2 ティックごとに発火 -> 4 分音符あたり 24

# ...停止するときは:
# tulip.midi_out([0xFC])
# tulip.seq_remove_callback(slot)
```

`tulip.seq_add_callback(fn, tick, period)` は `tick_count % period == tick` のたびに `fn(tick_count)` を呼び、停止用に `tulip.seq_remove_callback()` に渡すスロット ID を返します。

## 例: シンプルなアルペジエータ

```python
import amy, time

notes = [60, 64, 67, 72]  # C メジャーのアルペジオ
amy.send(synth=1, patch=0, num_voices=4)   # Juno のパッチ 0

for _ in range(10):
    for note in notes:
        amy.send(synth=1, note=note, vel=1)
        time.sleep(0.15)
        amy.send(synth=1, note=note, vel=0)
        time.sleep(0.05)
```
**注意:** ループの実行中はキーボード割り込み（Ctrl-C）が効かないので、抜けられなくならないよう気をつけてください。「Reset」ボタンを押せばやり直せます。

## 例: CV でピッチを制御する

```python
import amy, amyboard, time

amy.reset()
amy.send(osc=0, wave=amy.SINE, vel=1)  # サイン波、オン

for _ in range(100):
    v = amyboard.cv_in(channel=0)
    # -10..+10V を 100..1000 Hz にマップ
    freq = 100 + ((v + 10) / 20.0) * 900
    amy.send(osc=0, freq=freq)
    time.sleep(0.05)
```

## さらに詳しく

 - [AMY のワイヤプロトコルリファレンス](https://github.com/shorepine/amy/blob/main/docs/api.md) -- 制御できるすべてのパラメータ
 - [AMY のシンセアーキテクチャ](https://github.com/shorepine/amy/blob/main/docs/synth.md) -- オシレータ、フィルタ、モジュレーションの詳細
 - [MicroPython のドキュメント](https://docs.micropython.org/en/latest/) -- MicroPython 全般のリファレンス

[はじめかたに戻る](README.md)
