# Tulip Web と Tulip Desktop

[Tulip CC のハードウェア](../README.md)を作ったり買ったりしなくても Tulip を動かす方法が 2 つあります。ローカルでの開発や、まだ Tulip を持っていない人に自分の作品を見せるときに便利です。

[![Tulip Web](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/tulipweb.png)](https://tulip.computer/run/)

* [Tulip Web](https://tulip.computer/run) は、どのブラウザでも、モバイル端末でも Tulip を始められる最も手軽な方法です。Tulip Web は自分のコード専用のローカルファイルシステムを保持し、Tulip World にアップロード／ダウンロードして他の人の作品を取得できます。MIDI、オーディオ、グラフィックスを含め、「実機」の Tulip ができることはすべてサポートしています。

* Tulip Desktop はデスクトップコンピュータ向けの Tulip です。macOS 向けのビルドを定期的に作っており、Raspberry Pi を含む多くの Linux マシンにも対応しています。WSL 上の Windows で動作したという報告もあります。

私たちの小さなチームでは、あらゆる OS とコンピュータの組み合わせをサポートするのは難しいため、Tulip のハードウェアと [Tulip Web](https://tulip.computer/run) に注力していきます。とはいえ、自分のコンピュータで Tulip Desktop をコンパイル・実行する方法については以下をお読みください。

## macOS 版 Tulip Desktop

![Tulip Desktop](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/desktop.png)

Tulip Desktop は [Tulip CC](../README.md) 本体のデスクトップコンピュータ版です。Tulip CC ができることはすべてサポートし、ディスプレイとハードウェアを可能なかぎり再現します。ハードウェアを用意したり長い書き込みサイクルを待ったりせずに、Tulip の使い方を学んだり、Tulip 自体の開発をしたりするのにうってつけです。

Tulip Desktop は [macOS ユニバーサルビルド（Apple Silicon と Intel）、10.15 以降](https://github.com/shorepine/tulipcc/releases/)としてダウンロードできます。

### macOS 版 Tulip Desktop のコンパイル

自分で開発やコンパイルをしたい場合は、まずこのリポジトリをクローンします。


```bash
git clone https://github.com/shorepine/tulipcc
cd tulipcc
```

Tulip Desktop をビルドするには（macOS 10.15（Catalina）以降、Apple Silicon または x86_64）:

```bash
cd tulip/macos

# ローカル開発用（ネイティブアーキテクチャのみ、stderr をターミナルに表示）
./build.sh
./dev/Tulip\ Desktop.app/Contents/MacOS/tulip

# 配布用にパッケージング（ユニバーサルバイナリを作成）
./package.sh # dist に .app バンドルを作る。ローカルで使うだけなら不要
```

### Linux 版 Tulip Desktop のコンパイル（Windows WSL2 と Raspberry Pi を含む）

まず、このリポジトリをクローンして cd します。

```bash
git clone https://github.com/shorepine/tulipcc
cd tulipcc
```

依存パッケージをインストールします。

```bash
# Ubuntu、Debian など（WSL2 上の Windows 11 を含む）
sudo apt install build-essential libsdl2-dev alsa-utils

# Fedora 40 以降など
sudo dnf install gcc make SDL2-devel alsa-utils

# Arch
sudo pacman -S sdl2
```

ビルドして実行します。

```bash
cd tulip/linux
./build.sh
./dev/tulip
```

### トラブルシューティング

Mac OS X 上の Tulip は、起動時に出力に使うオーディオデバイスを選択します。音が出ない場合は、利用可能なオーディオデバイスを次のように確認できます。

```shell
$ <path_to_app_bundle>/Tulip\ Desktop.app/Contents/MacOS/tulip -l
0 - DELL U2715H
1 - DELL U2715H
2 - External Headphones
3 - MacBook Pro Speakers
4 - Microsoft Teams Audio
5 - ZoomAudioDevice
```

そのうえで、`-d <デバイス番号>` オプションで使いたいデバイスを選択します。たとえば次のようにします。

```shell
$ <path_to_app_bundle>/Tulip\ Desktop.app/Contents/MacOS/tulip -d 2
```

詳細は <https://github.com/shorepine/tulipcc/issues/103> を参照してください。

## 質問

質問がありますか？ [ディスカッションページでお気軽にどうぞ。](https://github.com/shorepine/tulipcc/discussions)
