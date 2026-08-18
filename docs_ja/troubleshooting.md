# Tulip のトラブルシューティング

Tulip で問題が起きたときに確認するページです。よくある問題とその解決策を挙げ、その後に一般的な診断方法を説明します。

ここで扱うのは [Makerfabs](https://tulip.computer) から入手したハードウェア版 Tulip CC です。ハードウェア Tulip が DIY のものである場合や、Tulip Desktop / Tulip Web で問題が起きている場合は、コミュニティに助けを求めてください。

<a id="reach-us"></a>
## 連絡先

困ったときに助けてくれる Tulip のコミュニティは次のとおりです。

 * 私たちの [Discord](https://discord.gg/TzBFkUb8pG)
 * [Tulip の GitHub issues](https://github.com/shorepine/tulipcc/issues)
 * [Tulip の GitHub discussions](https://github.com/shorepine/tulipcc/discussions)
 * [Makerfabs への問い合わせ](https://makerfabs.com/contact.html)

**ご留意ください**: Tulip はボランティアによって支えられています。Makerfabs はハードウェア的な不良のないテスト済みのボードを出荷し、コミュニティは Tulip の使い方やファームウェアの問題の解決を可能なかぎりサポートします。問題が起きたときは、このガイドに従い、疑問があれば[連絡してください](#reach-us)。ただし気長にお待ちいただけると助かります。

**受け取ったボードにハードウェア的な不良があると思われる場合は、Makerfabs に直接連絡してください。コミュニティは配送・検品・出荷対応は行っていません。** ただし、まずはこのページで問題の切り分けと解決を試みてください。

## まずは必ず Tulip をアップグレードする

まず、必ず [Tulip を最新のファームウェアにアップグレード](tulip_flashing.md)してください。Tulip が起動していて、キーボードと Wi-Fi が動いているなら、まず `tulip.wifi(ssid, password)` を実行してから `tulip.upgrade()` を実行してリリース版へのアップグレードを試してください。再起動後に画面に表示される Tulip OS の日付を控えておきましょう。この日付は、変更のたびに更新される[ローリング `tulip` リリース](https://github.com/shorepine/tulipcc/releases/tag/tulip)と一致しているはずです。それより古い場合は、もう一度試すか、手動での書き込みを試してください。

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/tulipv4r11_front.png" width=600>

## Tulip がうまく起動しない、画面が真っ暗

まず、Tulip の電源が入っていること（Tulip に向かって右側のスイッチ）と、正しく給電されていることを確認してください。REPL 画面が出ていなくても、LCD のバックライトの光は見えるはずです（Tulip には他に LED はありません）。USB コネクタやバッテリーがきちんと差さっているか確認してください。USB アダプタは少なくとも 1A を供給できるものが必要です。

**AMYboard** や他の Grove / I2C アクセサリを接続してからこの症状が出た場合は、電源の問題である可能性が高いです。[AMYboard や I2C アクセサリを接続すると Tulip が再起動を繰り返す](#tulip-reboots-in-a-loop-after-connecting-an-amyboard-or-i2c-accessory)を参照してください。

給電は正しいのに起動しないと思われる場合は、まず Tulip をリセットしてみてください。電源スイッチを数回オンオフします。RESET ボタンを数回押します。USB キーボードを接続しているなら外してみてください。数秒待ちます。まれに（多くは温度に起因して）チップが正しく起動しないことがありますが、たいていは 1〜2 回の再起動で直ります。

起動音は鳴るのに画面が出ない場合は、画面側に問題がある可能性があります。起動時の「ピッ」という音は、システムが正しく起動したときにのみ鳴ります。

<a id="tulip-reboots-in-a-loop-after-connecting-an-amyboard-or-i2c-accessory"></a>
## AMYboard や I2C アクセサリを接続すると Tulip が再起動を繰り返す

Tulip 単体では問題なく起動するのに、[AMYboard](amyboard/README.md) や他の I2C / Grove アクセサリを接続した途端に**再起動を繰り返す**（あるいは REPL まで到達しない）場合、ほぼ確実に**電源**の問題であり、どちらのボードの不良でもありません。

給電が必要なアクセサリは、Grove ポート経由で Tulip の 3.3V レールから電流を引きます。たとえば AMYboard は、Tulip 自身の約 575 mA に加えて約 **350 mA** を消費します。電源が両方をまかなえないと Tulip の電圧が下がり、ESP32-S3 のブラウンアウト保護がチップをリセットし、それが繰り返されます。

対処方法:

 * **十分な容量の 5V 電源（1〜2 A）**と、確実に動作する**データ対応** USB-C ケーブルを使い、ハブを介さず直接接続してください。
 * 可能であれば**アクセサリに別途給電**してください。たとえば AMYboard は自前の USB-C を持っており、Tulip から電流を引く代わりにそちらから動作します（存在する最も高い電圧を使います）。
 * シリアルポート経由で[診断出力を確認](#see-the-diagnostic-output-of-a-tulip)してください。`Brownout detector was triggered` というメッセージが出ていれば電源の問題で確定です。
 * 充電したてのバッテリーを試す、USB とバッテリーを切り替えて試すなどして、電源が弱い可能性を潰してください。

## Tulip を手動で書き込む

Tulip がまったく起動しない場合や、まだリリースに入っていない修正が公開された場合にも、手動での書き込みが必要になることがあります。手順は [コンパイル済みリリースから Tulip を書き込む](tulip_flashing.md) の節を参照してください。これにはコンピュータと Tulip の `USB program` ポート（上側）をつなぐ USB ケーブルが必要です。

<a id="see-the-diagnostic-output-of-a-tulip"></a>
## Tulip の診断出力を見る

`USB program / charge` ポートは、接続するとコンピュータ上でシリアルポートとして見えます。コンピュータに接続してシリアルターミナルプログラムを実行すれば、Tulip の起動時の出力を見られます（そのポートから Tulip を操作することもできます）。Mac と Linux では標準の `screen` が使えます。まずシリアルポートを探し（`ls /dev/*usb*` を実行し、`/dev/cu.wchusbserialXXXX` のようなものを見つけます）、`screen /dev/cu.wchusbserialXXX 115200` を実行します。他のプラットフォームでは任意のシリアルターミナルを用意し、Tulip の USB ポートに接続して 115200 ボーで動かしてください。

そのうえで Tulip の `BOOT` ボタンを押すと、次のような画面が表示されます。

```
...
Starting MIDI on core 0
UART MIDI running on core 0
Starting USB host on core 1
Starting display on core 0
Starting touchscreen on core 0 
Resetting touch i2c RST pin twice
this is the TULIP SPECIAL esp_lcd
Starting Alles on core 1
i2s started blck 8 dout 5 lrck 2
Starting Sequencer (timer)
```

この画面には、[私たちに共有](#reach-us)できる重要な診断・切り分け情報が表示されることがあります。

## Tulip のタッチスクリーンがまったく反応しない

タッチスクリーンが反応しないように見える場合、最も可能性が高いのは、輸送中やバッテリー取り付け時にタッチパネルのケーブルが緩んだことです。信頼性を高めるため、このコネクタをテープで固定するよう Makerfabs と調整を進めています。ただし、起きてしまった場合の修正は簡単です。背面ケースを外し、黒いクランプを持ち上げて（上図でラベル付けした）タッチコネクタを外し、少し力を加えながらケーブルをしっかり奥まで差し込みます。クランプを戻し、コネクタのすぐ手前でケーブルを押さえるようにテープを貼ります。次の写真のようになります。

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/tuilpv4r11ctp.jpg" width=600>

## タッチは効くが、メニュー・切り替え・終了ボタンが押しづらい

新しめの Tulip の一部は、タッチパネルの向きがわずかに異なる状態で出荷されており、従来のキャリブレーションでは精度が出ません。まずは Tulip World の `paint.py` でタッチスクリーンをテストしてみてください。`world.download('paint.py')` を実行してから `run('paint')` します。何人かのユーザーから、新しめの Tulip ではデフォルトの 0.8 ではなく `y_scale` を 0.75 にしたほうが挙動がよい、との報告がありました。修正するには `run('calibrate')` を実行し、提案される `touch_delta` を控え、聞かれたら `boot.py` に書き込みます。キャリブレーションは現在 `y_scale` を推定しないので、`edit('boot.py')` で `touch_delta()` の中の `0.8`（3 つの数値の最後）を `0.75` に変更してください。そのうえで再度 `run('paint')` を試してみましょう。それでも解決しない場合は、[さらなるデバッグにぜひご協力ください。](#reach-us)

## USB キーボードで打った文字が違う文字になる

Tulip は低レベルの USB キーボードスキャンコードを使って、キー入力を画面表示用の ASCII に変換しています。世界中のさまざまなロケールのキーボードを OS が変換してくれる、という贅沢はここにはありません。US 配列以外のキーボードを使う場合は、いくつかのキーをリマップする必要があります。`tulip.remap()` を使えば、対話的にキーマッピングを尋ねられながら 1 つずつ設定できます。

 - ドイツ語キーボードの場合、Tulip コミュニティの友人 `olav` が Tulip World にキーマップを提供してくれています。`world.download('keys_de.txt')` で入手できます。
 - フランス語キーボードの場合は、`remis` が `world.download('keys_fr.txt')` を作ってくれています。

使い方は Olav の `boot.py` を見るとわかります: `world.download('boot.py', 'olav')`

## USB のコンピュータ用キーボードが動かない

USB キーボードが動かない場合は、[issues](https://github.com/shorepine/tulipcc/issues) か [Discord](https://discord.gg/TzBFkUb8pG) で声をかけてください。お手伝いします。多くのキーボードをテストしていますが、このサポートは自前で実装しているため、まだ遭遇していないエッジケースがある可能性が高いです。

## Mabee DAC がモジュラーシンセに CV / ゲート値を正しく送れない

DAC でモジュラーシンセをうまく制御できない場合、現行リビジョンの <a href="https://www.makerfabs.com/mabee-dac-gp8413.html">Mabee DAC</a> は TRS（ステレオ 3.5mm）ケーブル向けの構成になっている点に注意してください。モノラルのモジュラー用パッチケーブルで使うには、DAC 側にステレオ→モノラルの<a href="https://www.amazon.com/3-5mm-Stereo-Adapter-Plated-Female/dp/B0919C5D93">アダプタ</a>か<a href="https://www.amazon.com/Gold-Plated-Connector-Splitter-RFAdapter-Headphone/dp/B096XNHTH3/">ケーブル</a>を挟む必要があります。モノラルケーブルのプラグを少しだけ抜き気味に差すという手もあります。今後のロットで改善すべく作業中で、対応でき次第この記述を更新します。

## MIDI 入力を接続すると Tulip の音声出力からハムが聞こえる

Tulip の **TRS MIDI 入力**に他のシンセやコントローラを接続した途端、Tulip の音声出力に大きな電源ハム（60 Hz / 120 Hz、北米以外では 50 Hz / 100 Hz）が乗る場合、これは**グラウンドループ**です。現行の Tulip ボードは MIDI 入力ジャックのスリーブをグラウンドに接続しており、ほとんどの MIDI 機器は（MIDI 規格が定めるとおり）MIDI 出力側でケーブルシールドをグラウンドに落としています。オーディオケーブルでも 2 台がつながっていると、ハム電流がループを流れてオーディオ経路に入り込みます。

対処法は **MIDI ケーブル側でグラウンド接続を切ること**です。MIDI データはグラウンドをまったく使わないカレントループで伝わるので、他には何も影響しません。

 * **グラウンドリフト（「グラウンドループアイソレータ」）の TRS アダプタまたはケーブル**か、スリーブが未接続の TRS→DIN アダプタを使う。
 * あるいは DIN の MIDI ケーブルの**片端でシールド接続を切る**。
 * 両方の機器を同じコンセント／電源タップから給電する、ループを作らない機器を経由して MIDI をパッチする、といった方法でも静かになることがあります。

今後のボードリビジョンでは、MIDI 入力のスリーブをハードウェア的にリフトする予定です。[issue #1198](https://github.com/shorepine/tulipcc/issues/1198) を参照してください。

## その他の問題

その他の問題があれば[お知らせください](#reach-us)。ここに追記していきます。
