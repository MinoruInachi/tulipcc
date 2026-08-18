# MIDI SysEx で AMYboard を制御する

AMYboard は、**USB MIDI のシステムエクスクルーシブ（SysEx）**メッセージによる小さな制御プロトコルを備えています。[AMYboard Online エディタ](https://amyboard.com/editor)がボードにスケッチを書き込み、ファイルを読み戻し、シンセの状態をダンプし、Python を実行し、再起動させるのに使っているのと同じプロトコルです。単なる MIDI なので、MIDI ライブラリのある言語ならどれからでも操作できます。このページでは、自分でスクリプトやプログラムを書けるように、そのワイヤフォーマットを説明します。

> ボード上で対話的に Python を書きたいだけなら、REPL や `mpremote` を使ってください（[Python を使う](python.md)を参照）。このコントロール API は、MIDI 経由でボードと会話する*プログラム*（アップローダ、自動化、テスト用のハーネス、CI）のためのものです。

Python による完全な参照実装がリポジトリの [`tools/amyboardctl`](https://github.com/shorepine/tulipcc/tree/main/tools/amyboardctl) にあります。インストール可能なライブラリ（`from amyboardctl import AMYboardLink`）と、コマンドラインのフロントエンドが含まれます。

```
pip install ./tools/amyboardctl
amyboardctl upload_sketch sketch.py
amyboardctl download_sketch -o sketch.py
amyboardctl download_state
amyboardctl run "import amy; amy.send(volume=0.5)"
amyboardctl reboot
```

これは AMYboard World のレコーダ（`tools/amyworld_recorder`）とハードウェア CI（`tulip/amyboard/hwci`）の両方が使う共通クライアントです。

---

## エンベロープ

すべての制御フレームは、AMYboard のメーカー ID `00 03 45` を持つ 1 つの MIDI SysEx メッセージです。

```
F0  00 03 45  <ASCII ペイロード ...>  F7
```

- `F0` … `F7` は標準的な SysEx の開始・終了バイトです。
- `00 03 45` はメーカー ID（"SPSS" — Shorepine Sound Systems）です。これで始まらない SysEx はボードが無視します。
- ペイロードはプレーンな 7 ビット ASCII です。バイナリデータ（ファイル内容、サンプル、状態のダンプ）は 7 ビット SysEx の範囲に収まるよう **base64 エンコード**されます。

ボードは **`AMYboard`** という名前の MIDI ポートとして現れます（入力*と*出力の両方。同じポートが制御フレームを双方向に運び、通常のノートデータも扱います）。このポートの両方向に接続してください。

通常の **MIDI チャンネルボイスメッセージ**（ノートオン／オフ、コントロールチェンジ、プログラムチェンジ、ピッチベンド）は SysEx では*ありません*。読み込まれているシンセを鳴らすには、通常の MIDI として送ってください。[ノートを鳴らす](#playing-notes)を参照してください。

---

## フロー制御: ACK を待つ

ボードは**処理したすべての SysEx 制御フレームに対して**、次を返して応答します。

```
F0 00 03 45 'A' 'K' F7        （つまり F0 00 03 45 41 4B F7）
```

ACK は、単に受信した時点ではなく、メッセージを完全に処理した*後*に送られます。**制御フレームを 1 つ送り、その `AK` を待ってから次を送ってください。** 常に 1 フレームだけを飛ばしている状態を保つことが、複数フレームの転送中にボードの SysEx リングバッファが溢れるのを防ぎます。タイムアウトを設け（ウェブエディタは 5 秒）、ACK が来なければ先へ進んでよいですが、通常はどのフレームもすぐに ACK されます。

`zB`（再起動）と `zI`（ping）は ACK の経路より前に純粋な C の中で処理されるため、`AK` を返し**ません**。`zI` は（後述の）`OK` の応答を返し、`zB` は何も返しません（ボードが再起動します）。チャンネルボイスの MIDI は ACK されません。

---

## コマンド一覧（ホスト → ボード）

以下のペイロードはすべて `F0 00 03 45 … F7` のエンベロープの中に入ります。末尾の `Z` はメッセージ終端のマーカーで、いくつかのコマンドはこれを受け付けて取り除きます。

| ペイロード | 意味 |
|---|---|
| `zT<path>,<size>Z` | `<path>`（例: `/user/current/sketch.py`）への**ファイル書き込みを開始**します。`<size>` は生のバイト数です。実行中のスケッチを停止したうえで、（次の行の）base64 データチャンクを待ちます。 |
| *(base64)* | **ファイル／サンプルのデータチャンク 1 つ。** `zT`（またはサンプル）転送中は、ファイルのバイト列を 188 バイト以下の生チャンクに分け、それぞれを base64 エンコードして 1 フレームにつき 1 チャンクずつ、`<size>` バイト送り終えるまで送ります。 |
| `zD Z` | **シンセの全状態**を SysEx としてホストに送り返します（[状態を読む](#reading-amy-state-zd)を参照）。 |
| `zD<path>Z` | ボードのファイルシステムから**ファイルを読み**、SysEx でストリーム返送します。 |
| `zP<python>Z` | ボード上で **Python を 1 行実行**します。例: `zPimport amyboard; amyboard.restart_sketch()Z`。 |
| `zY1Z` / `zY0Z` | **シーケンサのトランスポート** — MIDI クロックなしでステップシーケンサを開始／停止します。 |
| `zB Z` / `zB0Z` | **ブートローダへ再起動** — 次回起動時に `sketch.py` をスキップします（スケジューラを解放します）。USB は再列挙されます。 |
| `zB1Z` | **通常の再起動** — 次回起動時に `sketch.py` を実行します。 |
| `zB2Z` | **ROM ダウンロード／書き込みモードへ再起動します。** |
| `zIZ` | **Ping** — ボードが（後述の）`OK` を返します。 |
| `z<preset>,<len>,<sr>,<note>,<loopstart>,<loopend>Z` | **PCM サンプルをメモリのプリセットに読み込みます。** 続けて生のサンプルデータチャンクを送ります。`<len>`=0 でプリセットを解放します。（上級者向け。`amy.load_sample` を参照。） |
| `zF<preset>,<path>,<note>Z` | ボードのファイルシステム上にすでにある **WAV ファイルから PCM プリセットを読み込みます。** |
| `zS<preset>,<bus>,<frames>,<note>,<loopstart>,<loopend>Z` / `zOZ` | バスから**プリセットへ音声をサンプリング** / サンプリングを**停止**します。 |

**AMY の `send` コマンド**（人が読める設定プロトコル。例: `amy.send(synth=1, num_voices=6, patch=22)`）は、`zP` でそれらのコマンドを実行することで送れます。ただし、リアルタイムの制御はたいてい MIDI のノートや CC を送るほうが直接的です（あらかじめ CC を設定してある前提です）。生のワイヤコマンド（例: `i1nv6K22Z`）を送りたい場合は、エンコードした sysex ペイロードとしてそのまま送ればよく、`z` のワイヤコードで包む必要はありません。

---

## 応答一覧（ボード → ホスト）

応答もすべて同じエンベロープを使い、メーカー ID の直後に 1 バイトの**タグ**が入ります: `F0 00 03 45 <tag> <data...> F7`。

| タグ（ASCII / hex） | 意味 |
|---|---|
| `A` `K`（`41 4B`） | **ACK** — 直前の制御フレームを処理しました。（フロー制御用。） |
| `O` `K`（`4F 4B`） | **Pong** — `zI` への応答。ボードが生きていて準備できています。 |
| `X`（`58`） | **スケッチのエラー** — `<data>` は Python のトレースバック（ファイル名と行番号付き）の base64 です。スケッチの読み込み・実行に失敗したときに送られます。 |
| `V`（`56`） | **ファームウェアのバージョン** — `<data>` は ASCII のバージョン文字列（例: `20260627-abc1234`）です。`amyboard.report_version()` への応答として送られます。 |
| `0`（`30`） | **ダンプ: 単一フレーム** — `<data>` は `zD` ペイロード*全体*の base64 です（1 フレームに収まった場合）。 |
| `C`（`43`） | **ダンプ: チャンク、続きあり** — `<data>` は複数フレームからなる `zD` ダンプの 1 チャンクの base64 です。 |
| `E`（`45`） | **ダンプ: 最終チャンク** — 複数フレームの `zD` ダンプの最後のフレームです。 |

`zD` のダンプを組み立てるには、`0`（唯一のフレーム）か `E`（複数の `C` フレームの最後）が見えるまでフレームを集め、各フレームのデータを base64 デコードして順に連結します。

---

## レシピ

### ボードにスケッチを書き込む（`zT`）

ウェブエディタが使っている「スケッチを書き込む」流れです。

1. `zT/user/current/sketch.py,<size>Z` を送り、`AK` を待ちます。
2. UTF-8 のファイルバイト列を 188 バイト以下ずつに切り出し、base64 エンコードして送り、`AK` を待ちます。`<size>` バイトすべてを送るまで繰り返します。
3. `zPimport amyboard; amyboard.environment_transfer_done()Z` を送ると、新しいファイルでスケッチが再起動します。（`amyboard.restart_sketch()` でも可。）

受信側は各チャンクを base64 デコードしてファイルに追記し、`<size>` バイトが届いた時点でファイルを閉じ、転送完了となります。

### ボードからファイルを読む（`zD<path>`）

`zD/user/current/sketch.py Z`（実際はスペースなし。見やすさのため空けています）を送ると、ボードがファイルを `C…C E`（あるいは単一の `0`）フレームとして返送します。各フレームの base64 をデコードして連結すれば、ファイルのバイト列が得られます。

> ファイル名の**最後の文字が `Z`** のものはアドレスできません（末尾の `Z` は終端マーカーとして扱われます）。途中に `Z` があるのは問題ありません（例: `/ZIP.py`）。

<a id="reading-amy-state-zd"></a>
### AMY の状態を読む（`zD`）

`zDZ`（ファイル名なし）は、**現在のシンセの全状態**（有効なすべてのインストゥルメントと、グローバルのエフェクト（リバーブ／コーラス／エコー／EQ））を、改行区切りの **AMY ワイヤコマンド行**として吐き出します。フレームは上と同じ方法で組み立てます。デコードされたテキストは、`amy.send(...)` で再生すればその状態を正確に復元できるワイヤプロトコルそのものです。

### Python を実行する／スケッチを再起動する（`zP`）

`zP<code>Z` はボード上で 1 行を実行します。便利なものを挙げます。

- `zPimport amyboard; amyboard.restart_sketch()Z` — `sketch.py` を読み直して再起動します。
- `zPimport amyboard; amyboard.environment_transfer_done()Z` — `zT` の後に再起動します。
- `zPimport amyboard; amyboard.factory_reset()Z` — `current/` を消してデフォルトを読み直します。
- `zPimport amyboard; amyboard.report_version()Z` — ボードが `V` フレームで応答します。
- `zPimport sequencer; sequencer.tempo(120)Z` — テンポを設定します。

`zP` のコードは 255 バイトまでです。短い文（import といくつかの呼び出し）に留めてください。

### まっさらな状態に戻す

2 つの方法があります。軽いものから順に:

- **シンセだけをリセット（再起動なし）:** `zPimport amy; amy.reset()Z`。USB を落とさずに、すべてのオシレータとボイスをクリアします。高速で、ポートも開いたままです。多くのスケッチを連続して読み込むスクリプトに最適です。
- **完全な再起動:** `zBZ`（スケッチをスキップするブートローダ）を送り、`AMYboard` の MIDI ポートが消えて再列挙されるのをポーリングし、再接続し、必要なら `OK` が返るまで `zIZ` を送ります。確実にクリーンな状態になりますが、USB デバイスがリセットされるので MIDI のハンドルを開き直す必要があります。

### Python のエラーを見る

スケッチの読み込みや実行が失敗すると、ボードは**完全なトレースバック**を `X` フレームとして返します。ファイル名と行番号を含む Python の例外の base64 です。`zT`+再起動や、スケッチのコードを走らせる `zP` の後に `X` フレームを監視し、base64 をデコードすれば、シリアルコンソールなしでエラーを得られます。デコード後のペイロードの例:

```
Traceback (most recent call last):
  File "amyboard.py", line 593, in run_sketch
  File "/user/current/sketch.py", line 4, in <module>
NameError: name 'foo' isn't defined
```

### Ping（`zI`）

`zIZ` → `F0 00 03 45 'O' 'K' F7`。ボードが接続されて応答することを確認するのに使います。たとえば再起動待ちの間などです。

### シーケンサのトランスポート（`zY`）

`zY1Z` でステップシーケンサを開始、`zY0Z` で停止します。MIDI クロックを送らずにホストから再生を制御できます。

<a id="playing-notes"></a>
### ノートを鳴らす

読み込まれているシンセを鳴らすには、`AMYboard` ポートに（SysEx ではなく）**通常の MIDI** を送ります。

- ノートオン: `0x90 <note> <velocity>`（ステータス `0x90`〜`0x9F` がチャンネル 1〜16）
- ノートオフ: `0x80 <note> 0`（またはベロシティ 0 のノートオン）
- 他に扱われるもの: コントロールチェンジ（`0xB0`）、プログラムチェンジ（`0xC0`）、ピッチベンド（`0xE0`）、サステインペダル（CC 64）、オールノートオフ（CC 123）。

AMYboard World のスケッチの多くは**チャンネル 1** を待ち受けています。

---

## 最小の例（Python + mido）

```python
import base64, time, threading
import mido

MFR = [0x00, 0x03, 0x45]
ack = threading.Event()

def on_msg(m):
    if m.type == "sysex" and tuple(m.data[:3]) == (0x00, 0x03, 0x45):
        tag = m.data[3]
        if tag == 0x41 and m.data[4] == 0x4B:        # 'AK'
            ack.set()
        elif tag == 0x58:                             # 'X' エラー
            print("sketch error:\n" + base64.b64decode(bytes(m.data[4:])).decode())

inp  = mido.open_input("AMYboard", callback=on_msg)
outp = mido.open_output("AMYboard")

def send(payload: bytes, timeout=5.0):
    ack.clear()
    outp.send(mido.Message("sysex", data=MFR + list(payload)))
    ack.wait(timeout)

# スケッチをアップロード
code = b"import amy\nprint('hello from amyboard')\n"
send(b"zPimport amy; amy.reset()Z")                  # まっさらな状態に
send(("zT/user/current/sketch.py,%dZ" % len(code)).encode())
for i in range(0, len(code), 188):
    send(base64.b64encode(code[i:i+188]))
send(b"zPimport amyboard; amyboard.environment_transfer_done()Z")  # 再起動

# ノートを鳴らす
outp.send(mido.Message("note_on",  note=60, velocity=100, channel=0))
time.sleep(0.5)
outp.send(mido.Message("note_off", note=60, channel=0))
```

チャンクごとの ACK を伴う分割転送、エラーの収集、再起動と再列挙の処理、状態ダンプの再構成、Python の MIDI ライブラリを必要としない ALSA の `amidi` バックエンドなど、より充実した実装が必要な場合は、自作せずに [`tools/amyboardctl`](https://github.com/shorepine/tulipcc/tree/main/tools/amyboardctl) を使ってください。

---

## 実装の場所（信頼できる情報源）

- **転送レイヤのコマンドディスパッチ**（`zT`、`zD`、`zP`、`zY`、`zF`、`zS`、サンプル読み込み）: [`amy/src/parse.c`](https://github.com/shorepine/amy/blob/main/src/parse.c) の `amy_parse_transfer_layer_message()`。
- **再起動 / ping / SysEx の受け口**（`zB`、`zI`、`00 03 45` のゲート）: [`amy/src/amy_midi.c`](https://github.com/shorepine/amy/blob/main/src/amy_midi.c) の `parse_sysex()`。
- **ファイル受信 + base64 + 状態／ファイルダンプのフレーミング**（`C`/`E`/`0` のマーカー）: [`amy/src/transfer.c`](https://github.com/shorepine/amy/blob/main/src/transfer.c)。
- **`AK` の ACK**（処理した SysEx ごとに送出）: [`tulip/shared/modtulip.c`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/modtulip.c) の `tulip_amy_send_sysex()`。
- **スケッチの実行・再起動・エラー報告（`X` フレーム）・バージョン（`V` フレーム）:** [`tulip/shared/amyboard-py/amyboard.py`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/amyboard-py/amyboard.py)。
- **ホスト側の参照実装（ブラウザ）:** [`tulip/amyboardweb/static/spss.js`](https://github.com/shorepine/tulipcc/blob/main/tulip/amyboardweb/static/spss.js)。
