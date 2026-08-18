# Tulip CC v4r11 を使い始める

[Tulip Creative Computer](https://github.com/shorepine/tulipcc) へようこそ！ ここでは、[Makerfabs から購入した新しい Tulip ボード](https://tulip.computer)について知っておきたいことをすべて説明します。

始める前に、Tulip の簡単な歴史と、私たちがどのように開発しているかをお話しさせてください。私たちがどんなものを作っていて、どんなコミュニティなのかを理解しておくと、Tulip をずっと楽しめるはずです。

![Tulip](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/tulip4.png)

Tulip はここ数年、[私](https://notes.variogram.com/about)のサイドプロジェクトでした。私はずっと、コードやゲームや音楽を書ける小さくて省電力なコンピュータが欲しいと思っていました。持ち運べて、ウェブブラウザや仕事のメールのような気を散らすものがないものです。この数年で Tulip は、[配線だらけの状態](tulip_breadboard.md)から、Makerfabs の仲間たちから皆さんが購入した、ずっと洗練された安価なボードへと何度も作り直されてきました。その過程で、[Tulip の基盤にあるミュージックシンセサイザー](https://github.com/shorepine/amy)を手伝ってくれるよう[友人](https://scholar.google.com/citations?user=1H4HuCkAAAAJ&hl=en)を誘いました。制約のあるリアルタイムハードウェア上で、優れたアナログ／FM スタイルのシンセを Python から制御できることの力と楽しさに、私たちはすぐ気づきました。皆さんの手元にある Tulip はできたてです。私たちは日々バグを追いかけ、新機能を追加し、新しい音楽やデモを作っています。[Discord](https://discord.gg/TzBFkUb8pG) には、開発と並行して楽しい実験を試す、小さいながらも成長中の同好の士のコミュニティがあります。

**Tulip に関わっている人は誰もこれで儲けていません。** 私たちは全員、この種の小さなプロジェクトを楽しむ趣味人・エンジニア・科学者・ミュージシャンであり、他の人たちが加わってくれることを願っています。Tulip は極めて安価で完全にオープンソースであるように設計されています。[自分で作ることさえできます。](tulip_build.md) このバージョンの Tulip を皆さんの手元に届けるため、私は最初の資金を PCB 設計に費やしました。そして皆さんは、パートナーである Makerfabs による今後の設計作業を支えるために、Tulip の部品代と組立費の原価にごくわずかな上乗せを支払っています。

**Tulip は今でも十分楽しめますが、まだ助けが必要です。** Tulip をいじり倒して楽しんでほしいと思いますが、完璧を期待しないでください。奇妙な挙動や難所にたくさん出会うはずです。そうした制約を、面白く刺激的なものだと感じてもらえたら幸いです。私たちは、日々 Tulip をよくしていくために協力できる人たちのコミュニティを作りたいと考えています。もし Tulip のファームウェアが望みどおりでないなら、購入したハードウェアを自分の創作に使ってもかまわないことを忘れないでください。ESP-IDF や Arduino を使って、私たちのファームウェアを自分のものに置き換えて書き込むのは簡単です。

Tulip で問題が起きたら、GitHub の [issues](https://github.com/shorepine/tulipcc/issues) か [Discord](https://discord.gg/TzBFkUb8pG) で見つけてください。できるかぎりお手伝いします。

-- Brian


## ポートに慣れる

正面から見たときのポートの説明図です。

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/tulipv4r11_front.png" width=600>

## あると便利なアクセサリ

購入した Tulip ボードは単体でも動作します。ただし多くの人は、より快適に使うためにいくつかのアクセサリを追加したくなるでしょう。少なくともコンピュータ用キーボードは用意すべきで、残りは任意です。

### USB キーボード

Tulip はコマンドライン中心のインターフェースに、いくつかのタッチ操作を組み合わせたものです。入力用の USB キーボードを接続すると、格段に使いやすくなります。それによって Tulip は、想像したものを何でも作れる持ち運び可能な「デッキ」になります。

手持ちの USB キーボードなら_ほとんど_そのまま使えるはずです。とはいえ、「お約束」を守らない変わったキーボードには毎回驚かされるので、もし「そのまま動かない」場合は連絡してください。デバッグをお手伝いします。

[よりコンパクトなハードウェアキーボードが欲しい場合、小さな「cardKB」を I2C ポートに接続すれば Tulip でうまく動きます。](https://shop.m5stack.com/products/cardkb-mini-keyboard-programmable-unit-v1-1-mega8a)


### モジュラーシンセ用の DAC / ADC

モジュラーシンセを使っているなら、Tulip はモジュラー環境や古いアナログシンセを Python で「プログラム」するための、強力で楽しいコントロールサーフェスになります。既存の MIDI ポートでも多くのことができますが、CV 機器を持っているなら、それを制御するための I2C DAC を入手することをおすすめします。[モジュラー対応の 3.5mm ジャックを備えた 2 チャンネル DAC を Makerfabs から入手できます。](https://www.makerfabs.com/mabee-dac-gp8413.html) 最大 8 個まで同時に使えるので、合計 16 の CV 出力になります。

[CV 出力を読み取る ADC を含め、Tulip で使えるその他のサポート済み I2C デバイスの一覧はこちらです。](tulip_api.md#i2c--grove--mabee)

### Type-A 3.5mm MIDI からフルサイズへの変換アダプタ

USB MIDI と「標準的な」TRS MIDI の両方をサポートしています。最近の MIDI 機器の多くは MIDI コネクタに 3.5mm ジャックを使っています。古い 5 ピン DIN コネクタを Tulip で使いたい場合は、[Type-A の 3.5mm 変換アダプタ](https://www.amazon.com/Kurrent-Electric-Type-3-5mm-Adapter/dp/B0C2RLB3SL/)をいくつか入手するとよいでしょう。

### USB MIDI 用のハブ

USB MIDI デバイス（キーボードや USB-MIDI アダプタなど）を使いたい場合、Tulip では `USB-KB` コネクタからサポートしています。USB MIDI と入力用キーボードの両方を接続したくなるので、シンプルなハブにも対応しています。USB ハブのサポートは動作しますが継続的に開発中なので、うまく動かない構成があれば教えてください。

### 小型の Li-Po バッテリー

Tulip は省電力デバイスで、バッテリー駆動によるモバイル利用ができます。背面には、フラットな Li-Po や 18650 バッテリーパック用の標準的な JST コネクタがあります。USB の充電／PWR ポート（上側）に USB 電源ケーブルを接続すれば、Tulip がバッテリーを充電します。私自身は[この 1200mAh のバッテリー](https://www.adafruit.com/product/258)を使っています。Tulip の「内部」（背面ケースと PCB の間）に収めたい場合は、厚さ 5mm 程度のバッテリーを探すとよいでしょう。両面テープで PCB のバッテリーエリア内に固定できます。

より大きなバッテリーもうまく動作し、より長持ちしますが、収めるには背面ケースを外す（あるいは別の方法でバッテリーを取り付ける）必要があります。[この 5200mAh のバッテリーパック](https://www.amazon.com/XINLANTECH-Rechargeable-Bluetooth-Electronic-Batteries/dp/B0C2VFTDPY)なら Tulip を何時間も動かせますし、背面ケースを外せば PCB にぴったり収まります。

**極性が正しいことを必ず確認してください。** バッテリーの赤いケーブルが、Tulip ボード上で + 記号が付いている側のコネクタに来るようにします。

### 追加の Alles を 1 台、あるいは 5 台

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/nicoboard-alles.jpg" width=400>

Tulip は [Alles](http://github.com/shorepine/alles) を追加スピーカーとして使えます。AMY シンセサイザーを使って何十台ものスピーカーを無線で制御でき、Tulip 1 台だけで驚くようなマルチチャンネルのオーディオ構成が組めます。[Alles の PCB は仲間の Blinkinlabs から入手できます。](https://shop.blinkinlabs.com/products/alles-pcb)


## Tulip を使い始める


<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/tulipv4r11back.png" width=600>


Tulip が手元に届いたら、次の手順で始めましょう。

 - バッテリーを使う場合は、ドライバーで黒い背面ケースを開けて接続します。背面ケースを外す際は、上図でラベル付けしたタッチスクリーンのコネクタにぶつからないよう注意してください。緩みやすいことが知られています（工場で固定するよう対応を進めています）。
 - 電源アダプタかコンピュータから、Tulip に向かって**上側**の USB コネクタに USB-C ケーブルを接続します。
 - **下側**の USB コネクタに USB キーボードを接続します。
 - 必要に応じて、ステレオ音声ジャック、MIDI、I2C コネクタを配線します。ミキサーや他の音響機器がなければ、音声ジャックにヘッドホンをつなげば十分です。
 - Tulip の電源を入れましょう。スイッチはボードの上部にあります。
 - 次のような画面が表示されるはずです。音声を接続していれば、起動時に「ピッ」という音も聞こえます。

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/tulip4r11firstboot.jpg" width=400>

 - USB キーボードで、この「REPL」と呼ばれる画面に入力できるはずです。Python のコードと、いくつかの簡単なシステムコマンドを受け付けます。`ls` を試すと、ディレクトリの一覧が表示されます。

### まずは Tulip をアップグレードしましょう

Tulip を受け取ったら、必ず**ファームウェアをアップグレード**してください。その後も週に一度くらいはアップグレードするとよいでしょう。私たちは常に新機能を追加し、バグを修正し、API を更新しています。アップグレードするには、まず Wi-Fi に接続します。

 - `tulip.wifi('ssid', 'password')` は成功すると IP アドレスを返します。
 - `tulip.upgrade()` がアップグレードを案内してくれます。システムフォルダとファームウェアの両方のアップグレードを受け入れてください。全体で数分かかります。完了すると Tulip は自動的に再起動します。

[Wi-Fi をすぐに使えない場合や、まだリリースしていないコードを書き込みたい場合は、コンピュータから直接 Tulip に書き込むこともできます。](tulip_flashing.md)

### その他のクイックスタートのヒント

 - 必要だと感じたら `run('calibrate')` と入力してタッチスクリーンをキャリブレーションします。
 - 右下の黒いアイコンはランチャーで、同梱プログラムに素早くアクセスできます。`Drums` をタップすると、ドラムマシンが表示され（音も鳴り）ます。
 - 右上の青い「切り替え」アイコンをタップすると REPL に戻ります。もう一度タップするとドラムマシンに戻ります。`control-Tab` も使えます。
 - 赤い「終了」アイコンをタップするとドラムマシンを終了します。
 - `edit('boot.py')` で、Wi-Fi 接続、キャリブレーション、シンセのセットアップなど、Tulip 起動時に実行したい内容を追加できます。エディタでは `control-X` で保存、`control-Q` で終了できます。
 - TFB/REPL のフォントサイズは `tulip.tfb_font(x)` で切り替えられます。`0` がデフォルトの 8x12 フォント、`1` が小さい 6x8 フォント、`2` が大きい 12x16 フォントです。
 - Wi-Fi に接続したら、`run('worldui')` や `world.ls()` で Tulip World を試して、他の人が投稿したファイルやメッセージを見てみましょう。
 - 他に試せること:
   - ゲームやアニメーション: `run('bunny_bounce')`、`run('planet_boing')`、`run('parallax')`（終了は control-C）
   - 音楽アプリ: `run('voices')`、`run('juno6')`、`run('drums')`
   - 音楽デモ: `run('xanadu')`
   - その他のユーティリティ: `run('wordpad')`、`run('buttons')`
 - これらすべてのコードは公開されており、改造したり学んだりできます。編集したい場合のために、`drums`、`voices`、`juno6` のコピーを `/sys/ex` に `my_drums`、`my_voices`、`my_juno6` として同梱しています。公式版は読み取り専用なので、壊してしまう心配はありません。
 - MIDI を接続しているなら、ノートを弾いてみてください。Tulip は MIDI チャンネル 1 に Juno-6 のパッチ #0 が割り当てられた状態で起動します。この割り当ては `voices` アプリで変更できます。MIDI のノブやスライダーで `run('juno6')` の Juno-6 パラメータなどを操作したい場合は、スライダーを学習させて MIDI マッピングを更新するスクリプトを実行してください: `import learn_midi_codes`。Juno を完全に操作するには、少なくともボタン 13 個、ノブ 8 個、スライダー 9 個を「学習」させる必要があります。
 - Tulip コミュニティによる面白いものとして、`world.download('mc_dance')`、`world.download('tracks')`、`world.download('periodic2')` も試してみてください。


次に読むもの: [Tulip で音楽を作るチュートリアル](music.md)

その次は、[Tulip で自分の音楽・ゲーム・グラフィックスを作るための API](tulip_api.md) を見てみましょう。

Tulip で問題が起きたら、[トラブルシューティングガイド](troubleshooting.md)を確認してください。

[Tulip 自体の開発をしたい場合は、Tulip ファームウェアのコンパイルと書き込みのガイドを参照してください。](tulip_flashing.md)

「本物の」コンピュータで Tulip のコードを書きたくなったら、Tulip ハードウェアをシミュレートする [Tulip Web](https://tulip.computer/run) と [Tulip Desktop](tulip_desktop.md) を試すか、以下のリモートでファイルを転送・編集する方法を参照してください。

<a id="transfer-files-between-tulip-and-your-computer"></a>
## Tulip とコンピュータの間でファイルを転送する方法

Tulip とコンピュータの間でファイルをやり取りする方法はいくつかあります。USB ケーブル（上側の USB ポート、充電/UART/電源ポート）で Tulip をコンピュータに接続して `mpremote` というプログラムを使う方法と、Tulip の Wi-Fi をオンにして Tulip World BBS を使うか、コンピュータ上に小さなサーバーを立てて無線で行う方法です。

### `mpremote` を使う

[`mpremote`](https://docs.micropython.org/en/latest/reference/mpremote.html) は、Tulip を含むさまざまなデバイス上の MicroPython を制御・操作するために MicroPython プロジェクトが提供しているツールです。UART の USB 接続経由で Tulip に接続し、Tulip の REPL を使ったり、ファイルの編集・アップロード・ダウンロードができます。（他にも多くの機能がありますが、ここではファイル転送のみ扱います。）

**お使いのコンピュータの OS 向けに、まず USB→シリアルのドライバをインストールする必要があるかもしれません。** 最初から入っている OS もあれば、そうでない OS もあります。`mpremote` がポートを見つけられない場合は、[CH340K のドライバをインストールしてみてください。](https://www.wch-ic.com/downloads/CH341SER_ZIP.html)

まず、コンピュータに `mpremote` をインストールします。通常は（ターミナルアプリで）`pip install mpremote` です。（`pip` がない場合は、お使いの OS でのインストール方法を検索してください。）インストールできたら、Tulip の上側の USB ポート「USB pwr/charge/program」からコンピュータへ USB ケーブルを接続します。Tulip の電源を入れた状態で、コンピュータのターミナルから `mpremote` を実行するだけです。画面に Tulip の REPL が表示されるはずです。そこに入力すれば結果も表示されます。Tulip のほぼすべてを操作できる、便利な方法です。

コンピュータから Tulip にファイルを転送するには、`mpremote resume fs cp local_file.py :tulip_file.py` を使います。これは、いま自分がいるフォルダにある `local_file.py` を Tulip の現在のディレクトリに転送し、`tulip_file.py` という名前で保存します。かなり高速です。Tulip からコンピュータへ転送するには逆にします: `mpremote resume fs cp :tulip_file.py local_file.py`。

Tulip 上のファイルをコンピュータで直接編集するには、`mpremote resume edit file.py` を実行します。シェルの環境変数 `EDITOR` に設定されているエディタが開きます。エディタで保存すると、Tulip 上に保存されます。

[`mpremote` についてはこちらでさらに詳しく。](https://docs.micropython.org/en/latest/reference/mpremote.html) なお、Tulip での動作が確認できているのは今のところ `fs`、`mount`、`repl` コマンドだけです。また Tulip では、`mpremote` のコマンドに常に `resume` を付ける必要があります。問題があれば教えてください。

## Wi-Fi を使う

Wi-Fi の場合、まず `tulip.wifi('ssid', 'password')` を実行したうえで、Tulip ハードウェアとコンピュータの間でファイルをやり取りする方法が 2 つあります。

 - **Tulip World と Tulip Desktop を使う:** [Tulip Desktop](tulip_desktop.md) では、ファイルは `~/.local/share/tulipcc/user` フォルダにあります。そこで直接ファイルを編集・追加し、Desktop からでもハードウェア Tulip からでも `world.upload(folder)` や `world.upload(filename)` で Tulip World にファイルを置けます。取得は `world.download(folder)` や `world.download(filename)` で行えます。この方法の唯一の欠点は、ファイルが Tulip World 上の誰からも見られる公開状態になることです。個人情報を Tulip World で共有しないでください。

 - **ファイル転送スクリプトを使う:** Python スクリプト [file_server.py](../tulip/shared/util/file_server.py) を使えば、Tulip との間で非公開にファイルを転送できます。このスクリプトをコンピュータのどこかにダウンロードし、コンピュータのターミナルで `$ python file_server.py` のように実行します。スクリプトを実行したフォルダ内のすべてのファイルが、Tulip から参照できるようになります。コンピュータの IP アドレスは OS のネットワーク設定で確認してください（以下の `192.168.1.23` はその例です）。\
 \
 コンピュータ**から** Tulip へのファイル転送は `tulip.url_save(url, filename)` で行えます。例: `tulip.url_save('http://192.168.1.23:8000/file.py', 'file.py')`。`file.py` は `file_server.py` を実行したカレントディレクトリにあるファイルです。\
 \
 Tulip **から**コンピュータへ転送するには `tulip.url_put(url, file)` を使います。例: `tulip.url_put('http://192.168.1.23:8000', 'file.py')`。実行したフォルダにファイルが現れます。

 
[![shore pine sound systems discord](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/shorepine100.png) **Discord で Tulip について語り合いましょう。**](https://discord.gg/TzBFkUb8pG)

楽しんでください。Tulip World でお会いしましょう。
