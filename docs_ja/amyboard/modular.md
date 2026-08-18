# モジュラーシンセのセットアップ

AMYboard は Eurorack をはじめとするモジュラーシンセのシステムと直接連携できるように設計されています。10 ピンのモジュラー電源コネクタ、アナログの CV 入出力、MIDI の入出力、拡張用の I2C バスを備えています。

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/assets/img/amyboard_modular_2.jpg" width=600>

## アクセサリ

AMYboard 前面の I2C ポートに接続するだけで、任意の [OLED スクリーンやノブ](accessories.md)を自分で追加できます。

AMYboard には、ジャックと、任意のスクリーンやノブ用の切り欠きが入ったアクリル製の「フロントパネル」が付属します。パネルはジャックのネジで AMYboard に取り付けられます。そのフロントパネルを Eurorack のケースにネジ留めします。

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/assets/img/amyboard_bambu.png" width=600>

用途に合わせて、自分でフロントパネルを 3D プリントすることもできます。[BambuLab の 3MF ファイルはこちら](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pcbs/amyboard/amyboard_front_panel.3mf)から入手できます。フロントパネルの [DXF ファイル](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pcbs/amyboard/amyboard_front_panel.dxf)、[EPS ベクタファイル](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pcbs/amyboard/amyboard_front_panel.eps)、[SVG ベクタファイル](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pcbs/amyboard/amyboard_front_panel.svg)から作ることもできます。


## 10vpp 動作

ほとんどのモジュラーシンセの機器は 10Vpp で動作し、音声信号は -5V から +5V の間を振れます。初期状態の AMYboard は 1Vpp の「ラインレベル」です。AMYboard の音声入出力を 10Vpp に変えるには、ボード背面の DIP スイッチを "ON" / クローズにする必要があります。

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/assets/img/dip_switches.png" width=600>

 - **スイッチ 1 と 2**（入力）: クローズにすると入力を減衰させ、ライン入力を 10Vpp の Eurorack 信号に適した状態にします。
 - **スイッチ 3 と 4**（出力）: クローズにすると出力バッファのゲインを上げ、ライン出力を 10Vpp の Eurorack に適した状態にします。

## CV 出力

AMYboard には 3.5mm ジャックの **CV 出力が 2 つ**あり、GP8413 の 15 ビット DAC（I2C アドレス `0x58`）で駆動されます。出力範囲はおよそ **-10V〜+10V** で、Eurorack のピッチ、フィルタカットオフ、その他 CV で制御されるあらゆるパラメータの制御に適しています。

> **v1.5 のボード:** 最新の量産リビジョンには、半分のスケールで起動する DAC チップのロットが載っています。2026 年 8 月以降のファームウェアは、起動のたびにこれを自動で補正します。古いファームウェアでは CV 出力が -10V〜0V の範囲しかカバーせず、`cv_out(0)` は約 -5V になります。[トラブルシューティング](troubleshooting.md#cv-out-voltages-are-wrong-v15-boards)を参照してください。

```python
import amyboard

# CV out 1 に 3.3V を出力
amyboard.cv_out(3.3, channel=0)

# CV out 2 に -5V を出力
amyboard.cv_out(-5.0, channel=1)
```

`set_cv_out` を使えば、AMY のシンセの音声そのものを CV 出力にルーティングできます。そのシンセの音声はスピーカーからは消え、代わりに DAC に送られるので、任意の AMY 波形を CV ソースとして使えます。

```python
import amy, amyboard

# シンセを作り（num_voices と oscs_per_voice で定義されます）、CV1 にルーティングします
amy.send(synth=5, num_voices=1, oscs_per_voice=1, wave=amy.SAW_DOWN, vel=1, freq=0.5)
amyboard.set_cv_out(channel=0, synth=5)
```

これで 0.5Hz のノコギリ波が CV1 からフルレンジ（-10V〜+10V）で出力されます。波形、周波数、振幅はいつでも `amy.send(synth=5, ...)` で変更できます。

> **最初の `amy.send` で実際にシンセを作る必要があります。** シンセはボイスを持って初めて存在するので、生成するコマンドには `num_voices` **と** `oscs_per_voice`（あるいは `oscs_per_voice` を与えてくれる `patch`）が必要です。これらがないとシンセは確保されず、`set_cv_out` は読むべきオシレータを持てないため、CV 出力は変化しません。

止めるには、ノートをリリースするか、マッピングをクリアします。

```python
amyboard.set_cv_out(channel=0, synth=0)  # CV のマッピングをクリア
```


### 使いどころ

 - **ピッチ CV**: 1V/oct のピッチ電圧を出力して外部オシレータを制御する
 - **モジュレーション**: LFO、エンベロープ、シーケンスされた電圧を Python で生成する
 - **サンプル&ホールド**: 値を読み、加工して、その結果を出力する


## CV 入力

AMYboard には 3.5mm ジャックの **CV 入力が 2 つ**あり、ADS1015 の 12 ビット ADC（I2C アドレス `0x48`）で読み取ります。ジャック自体は **-10V〜+10V** で安全ですが、ADC が分解できるのは **-10V〜約 +6.3V** までで、+6.3V を超える電圧は +6.3V として読まれます。

```python
import amyboard

# CV in 1 の電圧を読む
volts = amyboard.cv_in(channel=0)
print(f"CV in 1: {volts:.2f}V")

# 生の ADC 値を読む
raw = amyboard.ads1015_raw(channel=0)
```

<a id="cv-triggering"></a>
### CV によるトリガ

CV 入力の遷移（立ち上がり／立ち下がり）に応じて発行されるワイヤコードのコマンドを設定することで、AMY のイベントを生成できます。

```
amy.send(cv_trigger='<CV>,<V_TRIG>,<V_RESET>[,<PITCH_CV>,<SCALE>,<OFFSET>],<WIRE_COMMAND_TEMPLATE>')
```
ここで `<CV>` は監視する CV 入力、`<V_TRIG>` はイベントが発火する電圧、`<V_RESET>` はイベントが「再武装」される電圧です。`<V_TRIG>` が `<V_RESET>` より高ければ、電圧の立ち上がりがイベントを発火させ、そうでなければ立ち下がりが発火させます。

任意指定の `<PITCH_CV>` は、トリガ時に「ピッチ」電圧を得るためにサンプリングする 2 つ目の CV チャンネルです。その電圧に `<SCALE>` を掛け、`<OFFSET>` を足した値が「ピッチ」となり、ワイヤコマンドのテンプレート内の `%v` に代入されます。

`<WIRE_COMMAND_TEMPLATE>` は AMY のワイヤコマンドで、イベント発火時に `%v` が置換されたうえで発行されます。

CV 0 でシンセ 1 のノートをトリガし（たとえば 0〜5V の遷移で）、CV 1 でピッチを制御する（1V/オクターブ）場合の設定は次のようになります。

```
amy.send(cv_trigger='0,3.0,2.0,1,1.0,0.0,i1l1n%v')   # CV0 が上昇して 3.0V を超えたらノートオン
amy.send(cv_trigger='0,2.0,3.0,i1l0')                # CV0 が下降して 2.0V を下回ったらノートオフ
```

CV トリガのイベントは積み重ねられます。上の例では、CV in 0 に 2 つのイベント（ノートオン用とノートオフ用）を関連付けています。特定の CV 入力に紐づくイベントをすべてクリアするには、空のコマンドを送ります。

```
amy.send(cv_trigger='0')   # CV 入力 0 に紐づくトリガをすべてクリアします。
```

発音時に 1 つのピッチ値をサンプリングする代わりに、（従来のピッチ CV 入力のように）osc のピッチが CV1 に動的に追随するようにもできます。

```
amy.send(synth=1, patch=1, num_voices=1)    # モノフォニックの JUNO パッチ
amy.send(synth=1, osc=2, freq={'ext1':1})   # 3 つのピッチ付きオシレータすべてを CV1 に追随させる
amy.send(synth=1, osc=3, freq={'ext1':1})
amy.send(synth=1, osc=4, freq={'ext1':1})
amy.send(cv_trigger='0,3,2,i1l1n69')  # CV0 のトリガは A4 のノートオンを送るが、実際のピッチには CV1 が反映される
amy.send(cv_trigger='0,2,3,i1l0')     # CV0 のノートオフトリガ。
```

注意: CV_IN は 20V レンジに対して 12 ビットの分解能で、最小ステップは約 6 セントに相当します。CV によるピッチ変調で細かいビブラートをかけるには粗すぎることがあります。

### 使いどころ

 - **外部 CV から AMY へ**: 入ってくる CV をシンセのパラメータ（フィルタ、ピッチ、振幅）にマップする
 - **ゲート検出**: ゲート信号を読み取って AMY のノートをトリガする
 - **センサー入力**: 任意の電圧源（-10V〜+10V の範囲）を接続して Python から読む

<a id="example-cv-input-controls-amy-filter-with-ctrlcoefs"></a>
### 例: CtrlCoef で CV 入力から AMY のフィルタを制御する

AMY の CtrlCoef の仕組みを使うと、Python のポーリングループなしに CV 入力をシンセのパラメータへ直接マップできます。`filter_freq` のような周波数パラメータでは、CtrlCoef は **log2（1V/oct）空間**で働きます。`const` の値が中心周波数を Hz で指定し、`ext0` の係数が 1V あたり何オクターブ動かすかを決めます。

最終的な周波数の式は次のとおりです。

```
freq = const * 2^(ext0 * cv_voltage)
```

したがって、CV 入力 1（-10V〜+10V）を使ってローパスフィルタをおよそ 100 Hz から 1000 Hz まで掃引するには:

1. 範囲の**幾何平均**を選びます: `sqrt(100 * 1000) ≈ 316 Hz`
2. `ext0` を求めます: `316 * 2^(ext0 * 10) = 1000` が必要なので、`ext0 = log2(1000/316) / 10 ≈ 0.166`

実際には `const=300, ext0=0.15` のようなきりのよい数値でも十分近くなります（おおよそ 106 Hz〜849 Hz）。

```python
import amy

# CV で制御するローパスフィルタ付きのノコギリ波を設定
amy.send(synth=1, wave=amy.SAW_DOWN)
amy.send(synth=1, filter_freq={'const': 300, 'ext0': 0.15}, filter_type=amy.FILTER_LPF24)

# ノートを鳴らします -- フィルタのカットオフが CV in 1 に追随します
amy.send(synth=1, vel=1, note=48)
```

CV in 1 に何もパッチしていない（0V の）状態では、フィルタは 300 Hz にとどまります。モジュラーの LFO やエンベロープをパッチすれば、1V/oct の CV 入力を持つハードウェアフィルタと同じように、カットオフが指数的に掃引されます。

ちょうど 100〜1000 Hz の範囲にするには `'const': 316, 'ext0': 0.166` を使います。より広く掃引したい場合（たとえば 50〜5000 Hz）は `ext0` を大きくします。

```python
# より広い掃引: 幾何中心は約 500 Hz、±10V で 50〜5000 Hz をカバー
amy.send(synth=1, filter_freq={'const': 500, 'ext0': 0.33}, filter_type=amy.FILTER_LPF24)
```


[はじめかたに戻る](README.md)
