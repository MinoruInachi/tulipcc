# Tulip CC のアップグレード・書き込み・コンパイル

Tulip のファームウェアは定期的に新しいバージョンをリリースしていく方針です。Tulip 自体を開発しているなら、自分のコードをコンパイルして書き込む方法を覚えるとよいでしょう。そうでなければ、組み込みのアップグレード機能か、デバイス全体を書き込むファイルを使って Tulip に書き込めます。

コンピュータから書き込む場合、対象の Tulip に載っているシリアル-USB チップ用のドライバが必要になる*ことがあります*。ボードが CH340X チップを使っていて `idf.py` から検出されない場合は、[こちらの手順に従ってドライバをインストールする必要があるかもしれません。](https://github.com/WCHSoftGroup/ch34xser_macos)


## 動作中の Tulip を最新版にアップグレードする

2024 年 1 月のリリース以降に書き込まれた Tulip が問題なく動いていて、単に最新ファームウェアにアップグレードしたいだけなら、「無線経由」で行えます。Tulip を Wi-Fi に接続したうえで `tulip.upgrade()` を実行してください。

```python
>>> tulip.wifi("ssid", "password")
>>> tulip.upgrade()
```

ファームウェアと `/sys` フォルダ（組み込みのプログラムやサンプルを置く場所）のどちらをアップグレードするか尋ねられます。通常、新しいファームウェアには新機能が入っているので、同じ API を使うように両方アップグレードすることをおすすめします。全体で約 5 分かかり、Tulip は再起動します。保存したファイル（`/user` 内）は無事です。簡単ですね。

## コンパイル済みリリースから Tulip を書き込む

未書き込みの Tulip がある、DIY を終えたばかり、あるいは何らかの理由でフラッシュやファームウェアを壊してしまった場合は、リリースの 1 つのファイルで Tulip 全体とファイルシステムを書き込めます。Tulip は変更のたびに継続的に新バージョンをリリースしています。最新版は常に[ローリング `tulip` リリース](https://github.com/shorepine/tulipcc/releases/tag/tulip)にあります。

**[Makerfabs の Tulip](https://tulip.computer/) をお持ちなら**、最新のフル・ファームウェアバイナリをここから直接ダウンロードできます: [TULIP4_R11](https://github.com/shorepine/tulipcc/releases/download/tulip/tulip-full-TULIP4_R11.bin)。

**自作の Tulip をお持ちなら**（DIY の `N16R8` / `N32R8` ボード、または [T-Deck](../tulip/tdeck/README.md)）: これらは現在では開発者専用のボードなので、ビルド済みバイナリの配布は行っていません。ご自分のボード向けに[ファームウェアを自分でビルドして書き込んでください](#compile-and-flash-tulipcc-for-esp32-s3)。手早くできますし、手順は以下に記載しています。

**[M5Stack Tab5](tab5_porting.md) をお持ちなら**、[ローリング `tab5` リリース](https://github.com/MinoruInachi/tulipcc/releases/tag/tab5)からフルイメージをダウンロードしてください: [tulip-full-TAB5.bin](https://github.com/MinoruInachi/tulipcc/releases/download/tab5/tulip-full-TAB5.bin)。Tab5 は ESP32-S3 ではなく ESP32-P4 を搭載しているため、別の `tulip/esp32p4` ツリーからビルドされ、別途リリースされています。上記の `tulip` リリースには Tab5 のイメージは含まれておらず、こちらは `shorepine/tulipcc` ではなく、この移植を持つフォークから公開されています。書き込みには `--chip esp32p4` を付けてください。

```bash
% esptool.py --chip esp32p4 write_flash 0x0 tulip-full-TAB5.bin
```

この最初の書き込みのあとは Tab5 でも `tulip.upgrade()` が使え、同じローリング `tab5` リリースを参照します。

USB ケーブルで Tulip をコンピュータに接続します。**注意**: Tulip を動かせるボードの多くは USB ポートを 2 つ備えており、一方は UART、TTL、Serial などと呼ばれ、もう一方は NATIVE、JTAG、Host などと呼ばれます。UART 側があればそちらを使い、なければ NATIVE 側を試してください。たとえば Tulip CC ではどちらの USB ポートも使えますが、NATIVE ポートを使う場合は USB ケーブルを挿しながら BOOT ボタンを押し続ける必要があります。書き込みには上側の UART USB コネクタを推奨します。T-Deck では NATIVE ポートしか使えず、電源を入れながら BOOT ボタン（トラックボールのボタン）を押し続ける必要があるかもしれません。両方のポートを試しても以下のコマンドが書き込み先のシリアルポートを見つけられない場合は、[ドライバをインストール済みか](https://github.com/WCHSoftGroup/ch34xser_macos)確認してください。

自分のボード用の `.bin` をダウンロードし、[`esptool.py`](https://docs.espressif.com/projects/esptool/en/latest/esp32/) など任意の ESP32 フラッシュツールで `.bin` 全体をフラッシュに書き込みます。

**注意: これを実行すると、Tulip に保存した内容やデバイスのフラッシュ上の他のデータはすべて消えます。32MB のボードでは最大 10〜15 分かかり、終盤で「止まった」ように見えることもありますが、心配いりません。ちゃんと動いています。**

```bash
% pip install esptool # まだインストールしていない場合
% esptool.py write_flash 0x0 tulip-full-XXX.bin
```

完了すると Tulip が起動するはずです（USB ケーブルを抜き差しする必要があるかもしれません）。この最初の書き込み以降は `tulip.upgrade()` が使えます。


<a id="compile-and-flash-tulipcc-for-esp32-s3"></a>
## ESP32-S3 向けに TulipCC をコンパイルして書き込む

Tulip 自体を開発している場合や、リリース前の最新の状態を見たい場合は、[Tulip CC](../README.md) を自分でコンパイルできます。すべてのプラットフォームでビルドできるはずですが、今のところ macOS と Linux でしかテストしていません。うまくいかない場合は教えてください。

### 初回セットアップ

macOS:
```bash
# まず homebrew をインストール（すでにある場合はスキップ）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
# その後ターミナルを再起動
# esp-idf の必要パッケージをインストール
brew install cmake ninja dfu-util
```


Linux:
```bash
# esp-idf の必要パッケージをインストール
sudo apt install cmake ninja-build dfu-util virtualenv
```

macOS と Linux の両方で、次にサポート対象バージョンの ESP-IDF をダウンロードします。バージョンは 5.4.1 です。取得には `git` が必要なので、なければインストールしてください。Espressif がリリースを更新したら、もう少し簡単な直接ダウンロードリンクを提供できるようになります。

いずれ複数のバージョンを使うことになるので、私は `~/esp/` に置いておくのが好みです。ここでは `~/esp/esp-idf` にあるものとします。

この Tulip リポジトリもクローンしてください。ここでは `~/tulipcc` にあるものとします。

```bash
cd ~
mkdir esp
cd esp
git clone -b v5.4.1 --recursive https://github.com/espressif/esp-idf.git esp-idf-v5.4.1
cd esp-idf-v5.4.1
./install.sh esp32s3
source export.sh

cd ~
git clone https://github.com/shorepine/tulipcc.git 
pip3 install Cython
pip3 install littlefs-python # ファイルシステムの書き込みに必要
cd ~/tulipcc/tulip/esp32s3
```

### コンパイル後に Tulip へ書き込む

Tulip を USB でコンピュータに接続します。どの USB ポートを使うかは上記の注記を参照してください。

自分のボードに合った `MICROPY_BOARD` の値を選びます。

 * [Tulip CC](https://tulip.computer/)（ディスプレイ付きの統合ボード）: `TULIP4_R11`
 * N16R8（16MB フラッシュ）ベースの DIY Tulip ボード: `N16R8`
 * N32R8（32MB フラッシュ）ベースの DIY Tulip ボード: `N32R8`
 * [T-Deck](../tulip/tdeck/README.md): `TDECK`

デフォルトは `TULIP4_R11` なので、省略した場合はこれが使われます。

まずファームウェアをビルドし（`idf.py -DMICROPY_BOARD=[X] build`）、そのうえで**初回インストール時のみ** `fs_create.py tulip flash` を実行して、ファイルシステム全体を Tulip に書き込みます。これで Tulip 上のストレージが用意されます。これはチップごとに一度だけ、あるいは下位のファイルシステムに変更を加えたときにだけ実行してください。以降の書き込みは（はるかに高速な）`idf.py -DMICROPY_BOARD=[X] flash` だけで済み、時間を節約でき、ファイルシステムも上書きされません。

たとえば N32R8 ベースの Tulip4 DIY ボードなら次のようになります。

```bash
idf.py -DMICROPY_BOARD=N32R8 build
# 新品のチップや開発ボードの場合、最初の一度は Tulip のファイルシステムを
# フラッシュメモリに書き込む必要があります。これは一度だけ、あるいは Tulip 自体を
# 開発していて `fs` を変更したときに実行してください。
cd ..
python fs_create.py tulip flash
```

書き込み後に Tulip の再起動が必要な場合がありますが、以降は USB を接続するか電源を入れれば起動するようになります。

ファイルシステムを変更せずにビルドして書き込むには、次のようにします。

```bash
cd tulip/esp32s3
source ~/esp/esp-idf/export.sh # ターミナルウィンドウごとに一度実行
idf.py -DMICROPY_BOARD=[X] flash 
idf.py monitor # Tulip を制御するための stderr と stdin を表示。終了は control-]

# AMY や micropython の下位ライブラリに（あなたが、あるいは私たちが）変更を加えた場合は、
# ビルドを完全にクリーンする必要があります
rm ../../.submodules_ok # これでサブモジュールが再初期化されます
idf.py fullclean
idf.py -DMICROPY_BOARD=[X] flash
```

[GDB を使ったデバッグやコードのプロファイリングについては、ESP32S3 のライブデバッグに関する新しいガイドを参照してください。](tulip_debug.md)


## M5Stack Tab5（ESP32-P4）向けに TulipCC をコンパイルして書き込む

Tab5 は `tulip/esp32s3` ではなく `tulip/esp32p4` からビルドし、上記の 5.4.1 ではなく **ESP-IDF 5.5.4** が必要です。置き換えるのではなく、もう一方と並べてインストールしてください。

```bash
cd ~/esp
git clone -b v5.5.4 --recursive https://github.com/espressif/esp-idf.git esp-idf-v5.5.4
cd esp-idf-v5.5.4
./install.sh esp32p4
source export.sh
```

次にイメージをビルドして組み立てます。`MICROPY_BOARD` は `TAB5` で、`fs_create.py` には `tulip` ではなく `tab5` を渡します。生成される `/sys` の内容は同じですが、esp32p4 ツリーから、esp32p4 チップ向けにビルドされます。

```bash
cd ~/tulipcc/tulip/esp32p4
idf.py -DMICROPY_BOARD=TAB5 build
cd ..
python fs_create.py tab5
```

これにより `tulip/esp32p4/dist/tulip-full-TAB5.bin`（オフセット `0x0` 用の全体イメージ）、`tulip-firmware-TAB5.bin`（`0x10000` 用のアプリ単体）、`tulip-sys.bin` が書き出されます。`fs_create.py` の行に `flash` を付けると、接続中の Tab5 にフルイメージを直接書き込めます。

初回インストールはパーティションテーブルを配置するため、必ずフルイメージである必要があります。それ以降は `idf.py -DMICROPY_BOARD=TAB5 flash` がアプリだけを書き込み、`/user` 内のファイルはそのまま残ります。

ESP32-S3 のビルドとは 2 点異なります。

 * LVGL のバインディングは C プリプロセッサとして `clang` を使って生成されるため、`clang` が `PATH` 上にある必要があります（macOS では最初から通っています）。
 * `tulip/esp32p4/dependencies.lock` は意図的にコミットされており、29 個のマネージドコンポーネントすべてをピン留めしています。ビルドの問題を解決するためにこれを削除しないでください。バージョン固定のない `esp_lvgl_port` は IDF 5.5.4 に対してコンパイルできません。


## 質問

質問がありますか？ [ディスカッションページ](https://github.com/shorepine/tulipcc/discussions)か、[**Discord で Tulip について語り合いましょう。**](https://discord.gg/TzBFkUb8pG)
