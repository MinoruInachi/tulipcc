# tulipcc から利用されている AMY C API の全体像 — コード生成に向けた調査

このドキュメントは、自動生成の対象としたいバインディング層をまたいで、*外部の*コードが現在手書きでバインドしている AMY C API 関数をすべて洗い出したものです。

1. **CPython** — `amy/src/pyamy.c`（`amy` pip パッケージの背後にある `c_amy` モジュール）
2. **MicroPython、AMY をリンクした構成** — `tulip/shared/modtulip.c`（AMYboard ハードウェア、Tulip ハードウェア、Tulip Desktop の macOS/Linux/Windows、VCV Rack における `tulip.*`）
3. **Emscripten / JS パススルー** — `tulip/web/static/spss.js` と `tulip/amyboardweb/static/spss.js`（AMY の `Makefile` の `EXPORTED_FUNCTIONS` に列挙された WASM エクスポートに対する `cwrap`）。ウェブ版 MicroPython は AMY を**リンクしない**（`AMY_IS_EXTERNAL`。modtulip.c の `#ifndef __EMSCRIPTEN__` セクションはコンパイルから除外される）ため、`mp.registerJsModule(...)` で MicroPython 側に橋渡ししています。
4. **Godot** — `amy/godot/`（ネイティブでは GDExtension C++、ウェブエクスポートでは `JavaScriptBridge.eval` パススルー。後述の Godot の節を参照）。

調査時点は amy のピン `8fca573f`（v1.2.78）／ tulipcc のブランチ `claude/amy-c-api-codegen-940eec` です。

## 関数の一覧

プラットフォーム列の凡例: **Py** = pyamy.c、**MP** = modtulip.c、**JS** = spss.js（両方のコピー。現状は同一の cwrap ブロックを持っています）。

| AMY の C 関数（シグネチャ） | Py | MP | JS | 現在の Python から見える名前 |
|---|---|---|---|---|
| `void amy_add_message(char*)` | ✅ `send_wire` | ✅ `tulip.amy_send` | ✅ cwrap `['string']` → `amy_js_message` モジュール | `amy.override_send` が各々にルーティング |
| `void amy_add_message_from_sysex(char*)` | ✅ `send_wire_from_sysex` | ✅ `tulip.amy_send_sysex` の内部（リングバッファのスロット + AK ack） | ➖（ウェブ側は代わりにハードウェア*へ* sysex を送る） | — |
| `uint32_t amy_sysclock()` | ✅ `ticks_ms` | ✅ `tulip.amy_ticks_ms` | ✅ cwrap → `amy_sysclock` モジュール → `tulip.amy_ticks_ms`、`amy.ticks_ms` | どこでも `tulip.amy_ticks_ms()` |
| `void amy_reset_sysclock()` | ➖ | ➖ | ➖ | C ホストのみ。下記の注記を参照 |
| `float amy_get_render_load()` | ✅ `render_load` | ✅ `tulip.amy_render_load` | ❌ ウェブでは欠落 | |
| `void amy_set_render_load_threshold(float)` | ✅ | ✅ `tulip.amy_set_render_load_threshold` | ❌ ウェブでは欠落 | |
| `void *yield_synth_commands(uint8_t synth, char *s, size_t len, bool include_fx, void *state)` | ✅ `get_synth_commands` 内でループ | ✅ `tulip.amy_get_synth_commands` 内でループ | ✅ JS の `get_synth_commands()` 内で手動の `_malloc` + ヒープ文字列読み出しを伴うループ。Python のラムダとして設置 | 同じジェネレータループの手書きコピーが 3 つ |
| `char *amy_dump_state_to_string(int *len)` | ✅ `dump_state` | ❌（削除済み。デッドコードだった） | ✅ エクスポート済み。ウェブのツール類が使用 | |
| `int amy_get_output_buffer(int16_t*)` | ➖ | ✅（1024 バイトの bytes オブジェクト） | ✅ 手動のヒープ操作 → `tulip.amy_get_output_buffer` | |
| `int amy_get_input_buffer(int16_t*)` | ➖ | ✅ | 💤 エクスポート済みだが JS ラッパーはコメントアウト | |
| `void amy_set_external_input_buffer(int16_t*)` | ➖ | ✅（バッファプロトコル引数） | 💤 エクスポート済みだがコメントアウト | |
| `void amy_event_midi_message_received(uint8_t*, uint32_t, uint8_t, uint32_t)` | ✅ `inject_midi` | ➖（ハードウェアには本物の MIDI がある） | ➖ | VCV の `vcv_midi.c` からも直接使用 |
| `void convert_midi_bytes_to_messages(uint8_t*, size_t, uint8_t)` | ✅ `inject_midi_bytes` | ➖ | ➖ | |
| `void amy_process_single_midi_byte(uint8_t, uint8_t)` | ➖ | ➖ | ✅（WebMIDI 入力の供給） | |
| `void set_cv_from_osc(int, int)` | ✅ | ➖ | ➖ | |
| `int16_t *amy_simple_fill_buffer()` | ✅ `render_to_list` | ➖ | ➖ | |
| `void amy_start(amy_config_t)` / `amy_default_config()` | ✅ `live`/`start`（kwarg→config のパーサ） | ➖（`amy_connector.c` の `run_amy` から呼ばれ、Python にはバインドされていない） | `amy_start_web`/`amy_start_web_no_synths` のエクスポート経由 | |
| `void amy_stop()` | ✅ `stop` | ➖ | `amy_live_stop` 経由 | |
| `void amy_live_start_web()` / `amy_live_start_web_audioin()` / `amy_live_stop()` | ➖ | ➖ | ✅（非同期 cwrap、オーディオワークレット開始） | |
| `void amy_bleep(uint32_t)` | ➖ | ➖ | ✅ | |
| `uint32_t sequencer_ticks()` | ➖ | ➖（リンク構成では `tulip.seq_ticks` が `amy_global.sequencer_tick_count` を直接読む） | ✅ cwrap（ウェブの `tulip.seq_ticks` は JS 側が保守する `sequencer_tick_count` のミラーを読む） | `tulip.seq_ticks()` |
| `void amy_set_external_hook_context(void*)` / `amy_get_external_hook_context()` | ➖ | ➖ | JS ワークレットの配線用にエクスポート | |
| `int size_of_amy_event()` | ➖ | ➖ | エクスポート済み | |
| グローバル変数への直接書き込み: `external_map[osc]=ch`、`cv_synth_map[synth]=cv` | ➖ | ✅ `tulip.amy_set_external_channel`、`tulip.set_cv_synth`（ESP と VCV のみ） | ➖ | そもそも関数ではない。生成対象にする前に C 側のセッターが必要 |

## Godot（4 番目の利用者）

Godot も結局は同じ 2 系統のバインディングであり、範囲が狭いだけです。新しい規約を考案する必要はありませんが、出力ターゲットが 2 つ増えます。

- **ネイティブ（GDExtension）** — `amy/godot/src/amy_gdextension.cpp` は pyamy や modtulip と同様、AMY を直接リンクします。`ClassDB::bind_method` のラッパー経由で `AmySynth` クラスを公開します。Godot が Variant のマーシャリングを自動で行うため、各ラッパーはごく単純な C++ メンバ関数です。使用している C 関数は `amy_start`/`amy_default_config`、`amy_stop`、`amy_add_message`、`amy_simple_fill_buffer`（`AudioStreamGenerator` 用に `PackedByteArray` へ）、`amy_sysclock` です。`set_chorus`/`set_max_oscs`/… といった長いプロパティメソッド群は、pyamy の `live()` の kwarg→`amy_config_t` パーサに相当する Godot 版です（同じく「関数ごとではなくセットアップ」の分類）。
- **ウェブエクスポート** — `amy/godot/web/godot_amy_bridge.js` と、`godot/amy.gd` からの `JavaScriptBridge.eval()`。JS パススルーパターンの*4 つ目*のコピーであり、追加の制約があります。すべてが eval される**文字列**として渡るため、実用的なのは文字列／スカラー引数の関数だけです（現状はワイヤメッセージの送信と、開始・準備完了のポーリングのみ）。
- **GDScript レイヤ** — `godot/amy.gd` が実行時にネイティブかウェブかを選びます（`OS.get_name() == "Web"`）。そのワイヤ kwarg のマップはすでに `scripts/gen_amy_gd_api.py` によって `# BEGIN/END GENERATED` のマーカーブロック内に生成されています。手で保守しているファイルの中でマーカーベースの再生成を行う先例です。

ドリフトという観点では、Godot は単に*遅れている*だけです。`yield_synth_commands` も、`render_load` も、`dump_state` も、MIDI インジェクションも、バッファアクセスもありません。上の一覧表の行は、`amy_add_message`、`amy_sysclock`（`get_sysclock`）、`amy_simple_fill_buffer`（`fill_buffer`）、`amy_start`/`amy_stop` を除けば、Godot 列はすべて ➖ になります。API テーブルに `godot` プラットフォームのフラグを足し、エミッタを 2 つ（`ClassDB::bind_method` のラッパーブロックと、ブリッジ JS の関数ブロック）追加すれば、ついでに追随させられます。ただしウェブエクスポート用のエミッタは、バッファ形状の関数をスキップする（あるいは文字列にエンコードする）必要があります。

コード生成の対象外（性質が異なるもの）: `tulip/shared/amy_connector.c` の `amy_config_t` フック配線（`run_amy()` の render/fopen/exec/reboot/overload フック）と、pyamy.c の `live()` の kwarg→`amy_config_t` パーサです。これらは関数ごとの定型コードではなく、プラットフォームごとの*セットアップ*です。とはいえ config の kwarg パーサは、後々フィールドマップから生成できるかもしれません。

## 3 つのプラットフォームが現状ずれている箇所（バグの動機）

- `amy_render_load` / `set_render_load_threshold`: CPython と MP にはあるが、**ウェブでは欠落**。
- `dump_state`: CPython とウェブにはあるが、**MicroPython には公開されていない**。
- `get_synth_commands`: ジェネレータのループが**3 回**手書きされており、バッファ戦略も 3 通り（C のスタックバッファ ×2、JS のヒープ malloc）。
- `amy_reset_sysclock`: どのスクリプト層にもバインドされていません。RESET_TIMEBASE は通常のイベントなので、`reset_sysclock()` は各バインディングでそのリセットビットの送信としてネイティブに書かれており、`spss.js` の両コピーも同様です。C 関数自体は C ホスト向けに存在します。
- 入力バッファ関連の関数: WASM にはエクスポートされていますが、JS 側はコメントの中で朽ちています。
- 命名: `ticks_ms`（Py）対 `tulip.amy_ticks_ms`（MP）対 `amy.ticks_ms`（ウェブでは start_amyboard 内のモンキーパッチ。ただし tulip web の start_tulip には*ない*）。

## 実際に使われている呼び出し規約

現状のバインディングはすべて 6 つの形のいずれかに収まります。ジェネレータが必要とする型のボキャブラリはこれで全部です。

1. **`(void) -> int/float`** — `amy_sysclock`、`sequencer_ticks`、`amy_get_render_load`、`size_of_amy_event`。
2. **`(スカラー…) -> void`** — `amy_set_render_load_threshold(float)`、`set_cv_from_osc(int,int)`、`amy_bleep(uint32)`、`amy_process_single_midi_byte(u8,u8)`。
3. **`(cstring) -> void`** — `amy_add_message`、`amy_add_message_from_sysex`。JS: cwrap `['string']` がマーシャリングを担当。Py: `PyArg_ParseTuple("s")`。MP: `mp_obj_str_get_str`。
4. **バイトバッファ入力** — `convert_midi_bytes_to_messages`、`amy_event_midi_message_received`、`amy_set_external_input_buffer`。Py: シーケンス→malloc した `uint8_t*`。MP: バッファプロトコル。JS: ヒープの `_malloc` + `set`。
5. **バイトバッファ出力** — `amy_get_output_buffer` / `amy_get_input_buffer`（呼び出し側が確保する 1024B、件数を返す）、`amy_simple_fill_buffer`（内部ブロックへのポインタを返す）、`amy_dump_state_to_string`（malloc し、長さを出力引数で返し、呼び出し側が解放する）。
6. **ジェネレータ／状態を持つイテレータ** — `yield_synth_commands`: 不透明な `void* state` を伴うループで、呼び出しごとに文字列チャンクを返し、リストに蓄積します。制御フローを持つ唯一の形であり、3 つのポートすべてで同一のロジックです。

加えて、関数ではない形がひとつ: **グローバル変数の直接読み書き**（`amy_global.sequencer_tick_count`、`external_map`、`cv_synth_map`）。ウェブビルドではコード生成からグローバルに手を伸ばせないので、最初のクリーンアップとして AMY 側にきちんとしたアクセサ関数を用意する必要があります。

## コード生成の想定形（次のステップ）

単一の信頼できるテーブル、たとえば `amy/scripts/amy_api.yaml`（あるいは既存の `scripts/gen_amy_js_api.py` の隣に置く Python の dict。これはすでに AMY における「ビルド時にテーブルからバインディングを生成する」パターンを確立しています。Godot 向けの `gen_amy_gd_api.py` も参照）:

```yaml
- name: amy_sysclock
  py_name: ticks_ms          # amy.ticks_ms / tulip.amy_ticks_ms として公開
  args: []
  ret: u32
  platforms: [cpython, micropython, web]
- name: yield_synth_commands
  kind: string_generator     # 特別扱いのテンプレート。3 つすべてで共有
  py_name: get_synth_commands
  args: [{name: synth, type: u8}, {name: include_fx, type: bool, default: true}]
  platforms: [cpython, micropython, web]
- name: amy_simple_fill_buffer
  gd_name: fill_buffer         # すでに API が出荷済みの箇所はプラットフォームごとに名前を上書き
  platforms: [cpython, godot]
- name: amy_get_output_buffer
  kind: bytes_out            # 呼び出し側のバッファ、サイズ 1024、n を返す
  buf_size: 1024
  ...
```

ジェネレータ（ビルドステップとして実行）:

- **`gen_pyamy_api.c`** — 生成されたラッパーと `PyMethodDef` の行。スリム化した pyamy.c（どうしても手書きが必要な部分、すなわち `live()` の config パースとモジュール初期化だけを残す）から `#include` されます。
- **`gen_modtulip_api.h`** — 生成された `STATIC mp_obj_t` ラッパーと `MP_ROM_QSTR` テーブルの断片。modtulip.c の既存の `#ifndef __EMSCRIPTEN__` の内側から `#include` されます。
- **`gen_amy_wasm_api.js`** — 生成された cwrap ブロック、`registerJsModule` の呼び出し、Python のモンキーパッチ用スニペット。**両方の** spss.js コピーから読み込まれます（現在の重複ブロックは削除）。さらに `EXPORTED_FUNCTIONS` のリストを Makefile／em フラグに書き出すことで、関数を追加したときにエクスポート一覧への追加を忘れることが二度と起きないようにします。
- **`gen_amy_gdextension_api.inc`** — `amy_gdextension.cpp` 向けに生成された `ClassDB::bind_method` の行とメンバ関数ラッパー、および `godot/web/godot_amy_bridge.js` 向けの生成関数ブロック（文字列／スカラー関数のみ）。

最終形: 1 関数につき YAML の 1 行だけで、`amy.<py_name>(...)`（および `tulip.amy_*` のエイリアス）が CPython、リンク構成のすべての MicroPython ターゲット、両方のウェブアプリで同一に存在し、同一に振る舞う状態になります。
