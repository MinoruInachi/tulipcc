# AMYboard / Tulip 向けユーザー C DSP プラグイン — 設計と実現可能性

*ステータス: 提案、2026-07-11。中核となる未知の部分は実験で検証済み（§3 参照）。その後すべてのプラットフォームで出荷済み。作り方ではなく**使い方**を知りたい場合はユーザーガイド [C でオーディオエフェクトとオシレータを書く](user_c_dsp.md) を参照してください。*

アイデア: 小さな C 関数（ビットクラッシャー、風変わりなオシレータなど）を **Python の文字列**として書き、**デバイス本体で**コンパイルし、AMY のレンダーループにオーディオレートで注入できるようにする、というものです。ツールチェーンも再書き込みも不要で、誰かが思いつくエフェクトごとにワイヤプロトコルを拡張する必要もありません。

```python
crusher = """
    int i = 0;
    while (i < frames * chans) {
        buf[i] = (buf[i] >> 18) << 18;
        i = i + 1;
    }
"""
tulip.install_c_process("crush", crusher)   # コンパイル + ロード、数ミリ秒
tulip.c_process("crush", True)              # バス 0 の FX チェーン末尾に挿入
```

ユーザーコードは **AMY の S8.23 バッファ上で直接**動きます（int32、1.0 == `1<<23`）。`frames` サンプルのチャンネルブロックが `chans` 個連続して並び、コピーはゼロです。エクスポートされたヘルパーが固定小数点を扱いやすくしています（どのスニペットからも利用可能）: `cos_lut(phase_q23)`（AMY のコサインテーブル。位相は 1 周期を S8.23 に正規化）、`fxmul(a, b)`（S8.23 の乗算）、そして 16 ビット PCM で考えたい人のための `to_int16(s)` / `from_int16(v)` です。

**結論: ESP32-S3 上で十分実現可能、デスクトップでは容易、ウェブでも見込みあり。** 必要な機構のほとんどはすでに存在しており、本当に新しく作る部分はわずかです。

---

## 1. すでに存在するもの

### AMY のフックポイント（現在のピン `d1f5d18` 時点）

- **オシレータ** — `amy_config.amy_external_render_hook(uint16_t osc, SAMPLE *buf, uint16_t len)` は、AMY がレンダリングした直後に**すべての可聴 osc について**呼ばれます（[amy.c:1804]）。非ゼロを返すと「この osc のオーディオは自分が処理／置換したので、AMY のミックスはスキップ」の意味になります。tulipcc は `tulip/shared/amy_connector.c:427`（`external_cv_render`）で `external_map` を介した CV 出力のために**すでにこれをチェーンしています**。ユーザーオシレータはその関数の中のディスパッチ分岐が 1 つ増えるだけで、AMY 側の変更はゼロです。（AMY にはコンパイル時の `CUSTOM` 波形タイプと `amy_set_custom()` の 7 関数構造体もあります。`examples/AMY_custom_osc` を参照。ただしランタイム経路はレンダーフックです。）

- **エフェクト** — 現状、**バスごとの FX チェーン末尾にはフックがありません**。`amy_fill_buffer()` はバスごとに `fbl[0][bus]` に対して EQ → コーラス → エコー → リバーブを実行し（SAMPLE は int32 の S8.23 固定小数点、チャンネルあたり `AMY_BLOCK_SIZE` フレーム、チャンネルはインターリーブではなく連続）、その後バスをミックス → ボリューム → ソフトクリップ → int16 の `output_block` とし、ESP では AMY 自身がそれを I2S に書き出します（`src/i2s.c: esp_fill_audio_buffer_task`）。したがってエフェクトの挿入点は、**AMY 側に小さなフックを新設する**ことになります（§4）。最近の `examples/AMY_custom_dsp` のビット削減の例は別の手法（オーディオ入力 → ユーザーバッファ → `AUDIO_EXT0/1` 波形）で、有用ではありますがバスへのインサートではありません。

### 整数オーディオはここでは長所

AMY の内部サンプル型は **int32**（S8.23）で、後述のオンデバイスコンパイラは**整数演算のみ（float なし）**をサポートするため、パイプライン全体が整数のままになります。これは S3 上でもむしろ望ましい形です。ユーザーコードは生の S8.23 バッファを受け取り（ゼロコピー、ヘッドルームも完全。クリップ前のバス値は ±1.0 を超えることがあります）、各バックエンドのシンボル機構で解決されるヘルパー関数（`cos_lut`、`fxmul`、`to_int16`/`from_int16`）が用意されているので、誰も固定小数点を*導出*する必要はありません。ヘルパーを呼ぶだけです。（xcc700 のフォークは、16 ビットの状態をコンパクトに持ちたいエフェクトのために `int16_t` のポインタ／配列も引き続きサポートしています。）

## 2. オンデバイスのコンパイラとローダ（ESP32-S3）

まさにこの計画のために作られたかのような、既製の部品が 2 つあります。

- **[xcc700](https://github.com/valdanylchuk/xcc700)**（MIT、700 行の .c ファイル 1 つ）: **Xtensa の REL ELF** を出力するミニ C コンパイラで、Mac/Linux 上でも*ESP32-S3 本体上でも*動きます（セルフホスト: S3 上で毎秒約 3,900 行 — ユーザーエフェクトは約 10 ms でコンパイルされます）。サポートするサブセットは `while`、`if/else`、`int/char/ポインタ/配列`、`enum`（`#define` の代用）、関数定義と呼び出し、算術・ビット演算、`.bss` のグローバル（ユーザーの状態やディレイライン）です。ないもの: `for`、float、struct、プリプロセッサ、初期化付きグローバル。整数領域で本格的な DSP を書くには十分です。将来アーキテクチャを移す場合に備え、ESP32 の RISC-V 系向けに **[rcc700](https://github.com/valdanylchuk/rcc700)**（P4 でテスト済み）もあります。

- **[espressif/elf_loader](https://components.espressif.com/components/espressif/elf_loader)**（IDF コンポーネント。ソースは [esp-iot-solution](https://github.com/espressif/esp-iot-solution/tree/master/components/elf_loader)）: ESP32-S3 上で実行時に ELF をロードして再配置します。**PSRAM からのロード済みコードの実行に対応**しており（8 MB あるので IRAM を圧迫しません）、ELF のインポートを**ファームウェアがエクスポートするシンボルテーブル**に対して解決します（`symbols.py` が生成します。ユーザーに呼ばせたい AMY/tulip のヘルパーを好きなだけエクスポートでき、ユーザーは xcc700 流に宣言するだけです: `int amy_osc_freq_q16(int osc);`）。名前で `process` を取り出すための `dlopen`/`dlsym` 風の API もあります。

xcc700 の README は、その出力が「ESP-IDF の elf_loader コンポーネントで直接実行でき、ロード時に再配置テーブル経由でファームウェア側が公開している任意のものへリンクされる」と明記しています。

## 3. 実験による検証（本日、この Mac 上で）

clang で xcc700 をビルドし、このドキュメント冒頭のビットクラッシャーをコンパイルしました。

```
> IN  : 14 Lines / 52 Tokens
> SYM : 2 Funcs / 1 Globals
> OUT : 139 B .text / 608 B ELF
```

`xtensa-esp32s3-elf-objdump -d` は正しいウィンドウド ABI の Xtensa コードを示し（`entry a1,80` …、シフトには `ssr/sra` と `ssl/sll` …、`retw.n`）、シンボルテーブルは `dlsym` 用に `process` をエクスポートしています（`00000034 g F .text process`）。

**コスト見積り**: コード生成は素朴なスタックマシンで、クラッシャーのループはサンプルあたり約 40 命令です。44.1 kHz ステレオ、256 フレームのブロックでは、約 20k 命令 / 5.8 ms ブロック ≈ **240 MHz コア 1 つの 2% 未満**です。これより 10 倍重いユーザーエフェクトでも余裕で収まりますし、コンパイラは 700 行なので、覗き穴最適化を入れたくなればフォークできます。

## 4. 必要な AMY 側の変更（小さな PR 1 本）

`amy_config_t` にある既存の `amy_external_*_hook` パターン（amy.h:726）に倣います。

```c
// 各バスの FX チェーンの末尾（EQ/コーラス/エコー/リバーブの後）、
// バスが出力にミックスされる前に呼ばれる。buf は AMY_NCHANS 個の
// 連続したチャンネルブロックで、各 len SAMPLE（S8.23）。
void (*amy_external_bus_postprocess_hook)(uint8_t bus, SAMPLE *buf, uint16_t len);
```

呼び出し箇所は `amy_fill_buffer()` のバスごとの FX ループ末尾に 1 つ。任意で、最終的な int16 インターリーブの `output_block` に対する後段のフック（ボリューム／クリップ後。「マスター出力を処理する」に相当）を追加してもよいですが、AMY のルーティング（「バスにエフェクトを差す」）とうまく合成でき、整数のみのコンパイラのサブセットとも整合するのはバスごとの SAMPLE フックのほうです。オシレータ側は **AMY の変更が不要**です（レンダーフックが既に存在します）。

オン／オフとミックスの制御: まずは tulip 側で始めます（`tulip.c_process(name, bus, on)` は `amy_connector.c` のディスパッチを切り替えるだけ）。後々シーケンス可能・ワイヤからアドレス可能にしたくなったら、AMY にバスごとの「ユーザーエフェクトレベル」パラメータを追加します。ただしそれは v2 の話であり、ワイヤプロトコルの設計待ちで足を止める必要はありません。

## 5. Tulip への統合（`tulip/shared/amy_connector.c` と新モジュール）

- `xcc700.c`（MIT）を `tulip/shared/3rdparty/` にベンダリングし、その「option D」（fd ではなくインメモリ出力で、関数として呼び出す形。小さなパッチ）に沿って適合させます。
- `tulip/amyboard`（および esp32s3）の `idf_component.yml` に `elf_loader` を追加し、選定したヘルパー API をエクスポートする最小限のシンボルテーブルを生成します。
- `tulip.install_c_process(name, src, kind="effect"|"osc")`: コンパイル → ELF を PSRAM にロード → `dlsym("process"/"render")` → スロットテーブルに格納。xcc700 のエラーは行番号付きの Python 例外として送出します。
- エフェクトのディスパッチ: tulip の `bus_dsp` コールバック（新設の AMY フックとして登録）がバスごとに有効なスロットを走査します。オシレータのディスパッチ: `external_cv_render` の `external_map` にユーザー osc のエントリを拡張し、サブセットには float がないので、msynth のパラメータをエクスポート済みヘルパー経由で固定小数点としてユーザーコードに公開します（`amy_osc_freq_q16(osc)`、`amy_osc_amp_q23(osc)`、`amy_osc_phase(osc)` など）。
- **デスクトップ（macOS の Tulip）** — *実装済み*: Python API は同じで、裏側は組み込みの **libtcc**（TinyCC、`tinycc` サブモジュール、LGPL）による JIT です。Xcode CLT は不要です。`tulip/shared/user_c_dsp.c` がスロットテーブルとディスパッチャ（`amy_external_bus_postprocess_hook` として登録）および tcc バックエンドを保持します。`tulip/macos/build.sh` が `libtcc.a` をビルドし、tcc のランタイム（`libtcc1.a` とヘッダ）をアプリバンドルの `Resources/tcc` に配置します。コンパイルエラーは tcc の行番号付きで `ValueError` を送出します。同じ名前で再インストールするとコードがその場で差し替わり、バスの有効化状態は維持されます（編集して再インストールする開発ループ）。`tulip.c_process_calls(name)` でコードが動いていることを確認できます。注意点: tcc は xcc700 がハードウェア上で受け付ける以上の C を通してしまいます（後で xcc700 の lint パスを追加予定）。また、署名・公証を伴う `package.sh` ビルドでこの機能を出荷するには、ハードンドランタイムの `com.apple.security.cs.allow-jit` エンタイトルメントとアーキテクチャごとの libtcc ビルドが必要です（それまでは、そのビルドからはコンパイル時に除外されます）。

  ```python
  tulip.install_c_process("crush", src)   # エフェクト -> スロット。不正な C なら ValueError
  tulip.c_process("crush", True, 0)       # バス 0 で有効化（on, bus=0）
  tulip.install_c_osc("cz", osc_src)      # オシレータ種別
  tulip.c_osc("cz", 20)                   # AMY の osc 20 が自作 C コードを鳴らす
  tulip.c_process_calls("crush")          # ここまでに処理したブロック数
  tulip.uninstall_c_process("crush")
  ```

  `src` は**完全なプログラムでも、関数本体だけでも**かまいません。本体だけの場合はシグネチャと標準の include が自動で巻き付けられます（`#line` を使うので tcc のエラーはユーザーの行番号を保ちます）。エフェクトの本体からは `buf/frames/chans` が、osc の本体からは `buf/frames/osc/phase_inc_q16/amp_q15` が見えます（buf はモノラル。ディスパッチャが `msynth` から位相ステップとエンベロープを導出するのでピッチベンドと ADSR がそのまま効き、パンは引き続き AMY が適用します）。**呼び出し間の状態**は本体内の `static` 変数で、ポリフォニックな osc ごとの状態は `osc` でインデックスする static 配列です。同梱の例 `tulip/fs/tulip/ex/c_dsp_demo.py`（ビットクラッシャーと CZ-101 風のフェイズディストーション osc）を参照してください。

### 失敗モードについて正直なところ

これはオーディオタスク上で動くネイティブコードです。ポインタが暴走すればパニックして再起動します。他のファームウェアのバグと同じです。xcc700 のエラー処理は「極めて楽観的」です。やっておく価値のある緩和策: `.text`/`.bss` のサイズ上限を設ける。エクスポートテーブル外のシンボルをインポートする ELF を拒否する（ローダは本質的にこれを行います。未解決シンボル = きれいなロード失敗）。インストール時にスクラッチバッファ上で `process()` を一度ドライランする（レンダー経路に入る前に、いちばん間抜けなクラッシュを捕まえられます）。そして「シンセをクラッシュさせられます。それも楽しみのうちです」と明記する。

## 6. ウェブ

**出荷済み。** 実装は、注入の仕組みという点で当初のスケッチと異なります（ワークレットのポートを介した structured clone も、`addFunction` も使いません）。

- **コンパイラ**: ベンダリングした xcc700 フォークの wasm バックエンドである `xcc700w.c`（Xtensa 版 `xcc700t` と同じ小さな C のフロントエンド）。ページ上の micropython の wasm モジュール内で動き、完結したスタンドアロンの wasm モジュールを出力します。リニアメモリはインポート（`env.memory`）、statics/rodata はホストが AMY のヒープから確保したインポート済みベースグローバル上に配置、未宣言の関数は `env.*` のインポートとして扱い、`fxmul`/`to_int16`/`from_int16` は組み込みとしてインライン化されます。
- **注入**: AMY のレンダーループは AudioWorklet 内で動き、そのスコープは `amy.js` しか評価しません。ページからそこにコードを注入することはできないため、ウェブビルドはコピーした `amy.js` に `tulip/shared/user_c_dsp_web.js` を*追記*し、両方のスコープで動くようにしています。AMY の js フック（`amy_render_js_hook`、`amy_bus_postprocess_js_hook`。ワークレットスレッド上でブロックごとに呼ばれます）が自分のスコープの `Module` を渡し、ページとワークレットは AMY のリニアメモリ内の共有コントロールブロック（SharedArrayBuffer）で待ち合わせます。このブロックは AMY の `amy_set_external_hook_context()` で登録されます。インストール時は、そのブロック内のスロットごとのメールボックスにモジュールのバイト列を送ります。ワークレットは AMY の `wasmMemory` とエクスポートをインポートしてそれをインスタンス化し（`cos_lut` は wasm 同士のまま）、フックから呼び出します。バスマスク、osc のバインド、呼び出し回数もこのブロックに置かれているので、有効化・バインド・呼び出し回数の取得にはメッセージのやり取りが一切不要です。
- 上位の Python/JS API は同じ。int32 の SAMPLE バッファも同じで、ゼロコピーです。

## 7. 段階的な計画

1. **AMY の PR**: `amy_external_bus_postprocess_hook`（と呼び出し箇所）を追加。ごく小さく、既存の流儀に従います。（標準的な AMY の PR フロー: amy の PR → `make test` → tulipcc でピン → マージ → 再ピン。）
2. **ESP でのスパイク**（amyboard ブランチ）: xcc700 をベンダリングし、elf_loader コンポーネントとシンボルテーブルを追加、`tulip.install_c_process()` とエフェクトのディスパッチを実装し、ハードウェア上でビットクラッシャーをベンチテスト。
3. **オシレータ種別**: `external_cv_render` でのユーザー osc ディスパッチと固定小数点のパラメータヘルパー。素朴な矩形波デモ。
4. clang/dlopen による**デスクトップの機能同等化**。
5. **ウェブ**: xcc700 の wasm バックエンドフォーク（あるいは暫定的にサーバー側コンパイル）とワークレットへの注入。amy 側に Makefile フラグの PR。
6. ドキュメントと、同梱するいくつかの例（クラッシャー、サンプルレートリデューサ、リングモジュレータ、チップチューン osc）。これらはテストコーパスも兼ねます。
