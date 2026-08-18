# Tulip で音楽を作るチュートリアル

Tulip へようこそ。

このチュートリアルでは、Tulip で音楽を作る方法と、音楽を作るためのコードを書く方法をひととおり紹介します。新しい例やテクニック、デモが出てきたら更新していきます。

まずは最初の[使い始めのチュートリアル](getting_started.md)を済ませておいてください。特に `tulip.upgrade()` は必ず実行しておきましょう。

## Tulip とは何か

Tulip は、下層のシンセエンジンやシーケンサの挙動を、Tulip 本体の上でライブにプログラムできる**音楽コンピュータ**です。コードエディタが用意されており、プログラムを Tulip 上で直接実行できます。互いに連携するマルチタスクのアプリも動きます。パッチエディタ、ボイスを割り当てる場所、ドラムマシンなど、いくつかのプログラムを同梱していますが、Tulip の本当の価値は、想像したものを何でも作れる力と、他の人が提供してくれたプログラムにあります。

## Tulip が音楽でできること

 - 非常に忠実な Juno-6 のパッチや、それに類するアナログシンセを、最大 6 音のポリフォニー／マルチティンバーで鳴らせます。すべてのパラメータを自由に制御できます。
 - 非常に忠実な DX7 のパッチを鳴らせるほか、自分の FM シンセシス構成をコードで作れます。
 - 内蔵の 808 系ドラムパッチを、ピッチ制御付きで鳴らせます。
 - サンプルを使わずに、とても良質なピアノ音源を鳴らせます。
 - 自分の .WAV サンプルを読み込んでサンプラーとして使えます。
 - モジュラーシンセやアナログシンセ向けの CV 出力を、内蔵波形やサンプル&ホールドを使って制御できます。
 - ADC から CV 入力をサンプリングして、Tulip 上の他のイベントを制御できます。
 - コードから MIDI の送受信ができます。MIDI メッセージに反応して好きな処理をするコードを書けます。
 - Tulip の USB-KB ポートに接続したキーボード、シンセ、アダプタなどの USB MIDI デバイスを使えます（ハブを使えばタイピング用キーボードも併用できます）。
 - ドラムマシンとアルペジエータのように、複数のアプリで共通のシーケンサクロックを共有できます。
 - 音声出力に、グローバルな EQ、コーラス、エコー、リバーブを追加できます。
 - `music.Chord("F:min")` のように、コードから音楽的な音を定義するためのスケール／コードライブラリがあります。
 - フィルタ、波形、変調ソース、ADSR を指定して、すべてのオシレータを低レベルから完全に制御できます。
 - 自作のオーディオエフェクトやオシレータを **C で**書き、Python から実行時にコンパイルしてホットスワップできます。[C でオーディオエフェクトとオシレータを書く](user_c_dsp.md)を参照してください。

## Tulip Desktop と Tulip Web についての小さな注意

実機の Tulip ではなく [Tulip Desktop](tulip_desktop.md) や [Tulip Web](https://tulip.computer/run) を使っている場合も、ほとんどは同じです。ただし Tulip Desktop では CV の送出は動きません。また、`/sys/`（たとえばサンプルを開くときなど）が出てきたら `../sys/` に読み替えてください。


## 内蔵の Tulip シンセサイザー

Tulip を起動すると、MIDI 入力ポート（接続されていれば USB MIDI ポート）から MIDI メッセージを受け取る構成になっています。MIDI キーボードや、シーケンサを動かしているコンピュータなど、MIDI を送出する機器なら何でも接続できます。

Tulip の電源を入れたら、まずは音を鳴らしてみてください。デフォルトでは MIDI チャンネル 1 が Juno-6 のパッチを鳴らします。チャンネル 10 のノートは、おおむね General MIDI のドラムに沿った PCM パッチを鳴らします。（チャンネル 10 は GM ドラムキット、AMY の `patch=384`（TR-808 キット）として設定済みなので、ノート 36 がキック、38 がスネア、42 がクローズドハイハット、といった具合です。パッチ 385〜390 にはさらに 6 つのキットがあります: TR-909、Linn 9000、Univox MR-12、Tokyo Synthetics、80s Power Kit、Percussion。キットの切り替えは `amy.send(synth=10, patch=38x)` か、MIDI ならチャンネル 10 でバンクセレクト MSB 3（CC0=3）＋プログラムチェンジ 0〜6 で行えます。ドラム用シンセを手で構成する場合は、素の `wave=amy.PCM` の osc ではなく、キットのパッチを読み込んでください（`amy.send(synth=10, patch=384)`）。ドラムキットはシングルボイスで、1 つのボイスがドラム音ごとに専用の osc を保持するため、`num_voices` はデフォルトの 1 のままにしておきます。）

チャンネルごとのパッチ割り当ての調整やパッチ変更は、内蔵の `voices` アプリで行えます。`run('voices')` と入力するか、右下のランチャーメニューをタップして `Voices` をタップしてください。

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/voices.png" width=600>


右上の赤いボタンでアプリを終了、青いボタンで（コマンドを入力できる REPL を含む）他のアプリに切り替えられます。

チャンネル 1 のパッチを別の Juno-6 パッチや DX-7 パッチに変えてみてください。また、"Misc" を選んで "dpwe Piano" を選択し、[新しいピアノパッチ](https://shorepine.github.io/amy/piano.html)もぜひ試してみてください。

画面上のキーボードは、複数の指でもタップできます。アルペジエータでも遊んでみてください。

## 内蔵の Juno-6 パッチエディタ

Juno-6 シンセサイザーのパラメータを変更したい場合は、REPL かランチャーメニューから `juno6` を開きます。MIDI チャンネルに Juno-6 のパッチが割り当てられていれば、本物の Juno-6 と同じようにあらゆるパラメータを変更できます。パラメータに MIDI のコントロールチェンジ値を割り当てて、MIDI キーボードのスライダーやノブでパラメータを操作することもできます。

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/juno6.png" width=600>


## Tulip のシーケンサと内蔵ドラムマシン

Tulip には 808 系のドラムマシンが付属しており、`run('drums')` でアクセスできます。Tulip に同梱されているどの PCM サンプルも鳴らせますし、自分のサンプルを読み込むこともできます（後述）。Tulip のドラムマシンは、シーケンサを使う他の Tulip プログラムと共通のクロックを共有します。つまり、たとえば `voices` アプリのアルペジエータはドラムマシンと同期し続けます。自分のプログラムでシーケンサを制御・利用する方法は後述します。

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/drums.png" width=600>


しばらく良い感じのドラムシーケンスを組んでみてください。ピッチのパラメータやパンでも遊んでみましょう。そのうえで「switch」アイコンか `control-Tab` で REPL に戻ります。ドラムマシンはまだ鳴り続けているはずです。

では、それに合わせて演奏する自分のコードを書いてみましょう。エディタでプログラムを作ってもよいですし、REPL 上で小さな関数を書くだけでもかまいません。まずは REPL から始めます。やりたいのは、すでに鳴っているドラムマシンに合わせて、あるコードの構成音をランダムに鳴らすことです。

まず、演奏に使うシンセを用意します。パッチ 0 を鳴らす 4 音ポリフォニーの Juno-6 シンセを起動します。これは MIDI チャンネル 1 が起動時に使うのと同じパッチです。ついでに、鳴らしたいコードも決めておきます。次のように入力してください。

```python
import music, random
chord = music.Chord("F:min7").midinotes()
syn = synth.PatchSynth(num_voices=4, patch=0)
```

最初の `import music, random` は、これらのライブラリを使うことを Tulip に伝えるものです。一部（`tulip, amy, midi, synth, sequencer` など）は起動時にすでに読み込まれていますが、プログラムを書くときは明示する習慣をつけておくとよいでしょう。

`chord` とだけ入力して Enter すると、中身が見えます。F マイナー 7 に対応する MIDI ノートのリストです。`syn` はメッセージを送って音を出せるオブジェクトです。`syn.note_on(50, 0.5)` を試してみてください。MIDI ノート 50 を半分の音量で鳴らします。

これをシーケンサで演奏するには、**シーケンス**を追加します。シーケンスは、1 ステップの長さ（4 分音符、32 分音符など）とパターン全体の長さを持ちます。先ほどの F:min7 のランダムな音で埋めた、4 分音符 8 個分のシーケンスを作ってみましょう。

まずは 4 分音符 8 個の `Sequence` を作ります。`4` が `1/4` 音符を、`8` が長さを表します。

（入力し終えたら、`...` ではなく `>>>` プロンプトが再び出るまで、最後に Enter を数回押してください。）

```python
seq = sequencer.AMYSequence(8, 4) 
for i in range(8):
    seq.add(i, syn.note_on, [random.choice(chord), 0.6])
```

少し噛み砕くと、4 分音符 8 個分のパターン `(8,4)` を作り、そこに先ほど作った F:min7 のランダムな音を 8 個追加しています。`seq.add` はまずスケジュール先の要素番号（ここでは 0, 1, 2, 3 …… 7）を取り、次に呼び出す関数（`syn.note_on`）、そしてその関数の引数を取ります（`random.choice(chord)` がランダムな MIDI ノートを選び、0.6 はベロシティです）。

これでドラムに合わせて 4 分音符ごとにシンセのパターンが鳴っているはずです。止めるまで続きます。`sequencer.AMYSequence` の 2 番目の引数は `divider` です。これはシーケンスの各ステップの長さを Tulip に伝えます。`4` なら `1/4`、つまり 4 分音符です。`8` にすれば各ステップは 8 分音符になります。

```python
seq.clear() # シーケンスが止まるはず
seq = sequencer.AMYSequence(8, 8)  
for i in range(8):
    seq.add(i, syn.note_on, [random.choice(chord), 0.6])
```

これで倍の速さになるはずです。

（均等な拍でないもっと複雑なことをしたい場合は、divider を最大 `192` まで設定できます。そうすると 1 ティックごと（ティック間はおよそ 10ms）に呼ばれるので、いつ音を出すかを自分で決められます。ポリリズムのために複数の `AMYSequence` を同時に走らせることもできます。）

シーケンサでは音符だけでなく、任意の Python 関数もスケジュールできます。これには `AMYSequence` ではなく `TulipSequence` を使います。画面を更新する、外部機器に何かを送る、など想像したことは何でもできます。2 つのシーケンサは同期を保ちます。関数として `syn.note_on` の代わりに、自分の関数を渡すだけです。

```python
def p(x):
    print("hey!")

print_seq = sequencer.TulipSequence(2, p)
```

2 分音符ごとの出力を止めるには `print_seq.clear()` と入力します。プログラムは `seq.clear()` で終わるようにして、後始末をしましょう。

これで遊んでいる間、シーケンスを止めずにシンセのパッチを変えられます。

```python
syn.program_change(143) # パッチ #143（DX-7 の BASS 2）に変更
```

Juno のパッチは 0〜127、DX7 は 128〜255、ピアノは 256、General MIDI のドラムキットは 384〜390、自分で保存したパッチ（後述）は 1024 から始まります。

シーケンサの BPM も簡単に変えられます。これはドラムマシンを含め、シーケンサを使うすべてに影響します。

```python
sequencer.tempo(120)
```

[（シーケンサ API については API ドキュメントもぜひ読んでください。）](tulip_api.md)

## 新しいシンセを作る

作成した `synth.PatchSynth` はそれぞれ自分のボイスを持つので、Tulip が MIDI 用に起動しているシンセ（や Tulip 上で動いている他のもの）と競合しません。好きなだけ作れます。

```python
syn = synth.PatchSynth(num_voices=2, patch=143) # 2 音ポリフォニー、パッチ 143 は DX7 BASS 2
```

Juno-6 のベースと DX7 のパッドのように、マルチティンバーで鳴らしたい場合は次のようにします。

```python
synth1 = synth.PatchSynth(num_voices=1, patch=0)  # Juno
synth2 = synth.PatchSynth(num_voices=1, patch=128)  # DX7
synth1.note_on(50, 1)
synth2.note_on(50, 0.5)
```

音の「予約」もできます。これは高速なパラメータ変化をシーケンスするときに便利です。`synth` は `ticks` パラメータを受け取り、これはノートを発音する AMY シーケンサの絶対ティックです。たとえば、次を REPL に入力してみてください。

```python
# コードを一度に鳴らす
import music, midi, tulip
synth4 = synth.PatchSynth(num_voices=4, patch=1)
chord = music.Chord("F:min7").midinotes()
TICKS_PER_MS = 108 * 48 / 60000.0    # デフォルトテンポ（108 BPM、48 PPQ）でのミリ秒あたりティック
base_ticks = tulip.seq_ticks()
for i,note in enumerate(chord):
    synth4.note_on(note, 0.5, ticks=round(base_ticks + i * 1000 * TICKS_PER_MS))   # 今から i 秒後
    # 各ノートオンは、前のものからちょうど 1 秒後に鳴ります
```

シンセに `all_notes_off()` を送れば発音を止められます。

```python
synth4.all_notes_off()
```

プログラムの中で新しいシンセを起動した場合は、使い終わったら `release` するのを忘れないでください。

```python
synth1.release() # すべてノートオフしてからボイス割り当てをクリアします
synth2.release()
synth4.release()
```

AMY（下層のシンセエンジン）に詳しくなってくると、Python で自分の `synth` を作りたくなるかもしれません。例としては `synth.py` の `OscSynth` を参照してください。

## デフォルトのシンセや MIDI チャンネル割り当てをコードから変更する

MIDI とシンセの対応をプログラムから変更したくなることがあります。たとえば、チャンネル 1 でデフォルト起動する 6 音シンセのポリフォニーを下げて、MIDI から入ってくるノートが自分のアプリの性能やポリフォニーに影響しないようにする、といった用途です。あるいは、MIDI チャンネルごとに異なるパッチを受けるように音楽アプリを構成したい場合もあるでしょう。

チャンネルのシンセのパラメータは次のように変更できます。

```python
midi.config.add_synth(channel=c, synth=synth.PatchSynth(patch=p, num_voices=n))
```

なお `add_synth` は、そのチャンネルで動いているシンセを停止し、代わりに新しいものを起動します。

## エディタ

Tulip の REPL でいろいろ試すだけでも多くのことができます。ですがいずれ、作ったものを保存して他のものと一緒に走らせたくなります。Tulip のエディタを起動して、最初のプログラムを保存してみましょう。ドラムマシンは終了してかまいません。REPL のシーケンサコールバックを止めるために、

```
seq.clear() # シンセが止まるはず
```

を実行するのを忘れないでください。

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/jampy.png" width=600>

`edit('jam.py')`（名前は何でもかまいません）と入力します。黒い画面が開きます。これがエディタです。コンピュータのエディタと同じようにコードを保存できます。さっそく入力して、プログラムを後世に残しましょう。

```python
import tulip, midi, music, random, sequencer, synth

chord = music.Chord("F:min7").midinotes()
syn = synth.PatchSynth(num_voices=1, patch=143)  # DX7 BASS 2
seq = None

def note(t):
    syn.note_on(random.choice(chord), 0.6, ticks=t)

def start():
    global seq
    seq = sequencer.TulipSequence(8, note)

def stop():
    global seq
    seq.clear()
    syn.release()

```

`control-X` で保存し（ステータスバーの小さなアスタリスク `*` が消えます）、`control-Q` でエディタを終了するか、`control-Tab` で REPL に戻ります。あとは次のようにします。

```
import jam
jam.start() # 鳴り始めるはず
jam.stop() # 止まります
```

こうしておくと、作ったものを編集したりいろいろ試したりするのが楽になります。コードやパッチ、シーケンスを変えて試してみてください。ドラムマシンを止めていた場合は、いつでも再開できますし、あなたの `jam` とタイミングも合ったままです。

ドラムマシン自体も、実は Python で書かれたシンプルな Tulip のプログラムです。[ドラムマシンのコード](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/drums.py)を見てみてください。ほとんどは UI のセットアップで、そのあとにシーケンサのコールバックを設定し、拍ごとにドラムのメッセージを送っているのが分かります。自分版を作りたい場合は、別の名前（`my_drums.py` など）で Tulip にコピーし、編集して `run('my_drums')` してください。

## UIScreen

エディタや `voices`、`drums` アプリには右上のボタンと専用の画面があるのに、`jam.py` は REPL の中で動いているだけ、ということに気づいたかもしれません。それで済む用途も多いのですが、UI を作ったり、アプリの状態を専用画面に表示したりしたい場合もあるでしょう。そのためには、シンプルなプログラムを、兄貴分たちと同じ Tulip の `UIScreen` アプリに簡単に作り替えられます。テンポ用スライダー付きの UIScreen として `jam2.py` を作ってみましょう。`edit('jam2.py')` を開き（他のファイルを編集中なら、いったんエディタを終了してから開き直してください）、次のようにします。

```python
import tulip, midi, music, random, sequencer, synth

def note(t):
    global app
    app.syn.note_on(random.choice(app.chord), 0.6, ticks=t)

def start(app):
    app.seq = sequencer.TulipSequence(8, note)

def stop(app):
    app.seq.clear()
    app.syn.release()

def run(screen):
    global app
    app = screen
    app.seq = None
    app.chord = music.Chord("F:min7").midinotes()
    app.syn = synth.PatchSynth(num_voices=1, patch=143)  # DX7 BASS 2
    app.present()
    app.quit_callback = stop
    start(app)
```

保存したら、REPL から `run('jam2')` します（`.py` は省略できます）。右上にボタンのある空白の画面が表示され、アプリの起動と同時に始まったシーケンスが聞こえるはずです。「switch」や `control-Tab` で切り替えても鳴り続けます。アプリを終了するか `control-Q` すると止まります。それがここで設定している `quit_callback` の働きです。

UI 部品を追加してみましょう。`run` に数行足して、次のようにします。

```python
def run(screen):
    global app
    app = screen
    app.seq = None
    app.chord = music.Chord("F:min7").midinotes()
    app.syn = synth.PatchSynth(num_voices=1, patch=143)  # DX7 BASS 2
    bpm_slider = tulip.UISlider(sequencer.tempo()/2.4, w=300, h=25,
        callback=bpm_change, bar_color=123, handle_color=23)
    app.add(bpm_slider, x=300,y=200)
    app.present()
    app.quit_callback = stop
    start(app)
```

そして `run(screen)` の上に新しい関数を追加します。これは BPM スライダーが動かされたときのコールバックです。この関数の中では、システムの BPM を変更します。

```python
def bpm_change(event):
    sequencer.tempo(event.get_target_obj().get_value()*2.4)
```

`jam2` アプリがすでに動いていたら終了し、もう一度 `run` してください。スライダーが表示され、動かすとシステムの BPM が変わるはずです。コードにある `2.4` は、スライダーが 0〜100 の値を返すので、それを 0〜240 の BPM に変換するためのものです。UI 要素はいろいろ追加できます。`UICheckbox` や `UIButton` といった定番のものをいくつか用意しています。`drums` や `voices` のソースを見て作り方を確かめたり、[API ドキュメントで詳細を確認したり](tulip_api.md)してみてください。演奏したいコードを入力してもらう `UIText` の入力欄なんかはどうでしょう？

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/jam2.png" width=600>


## サンプラーと OscSynth

Tulip はいくつかの `synth` クラスを定義しており、その 1 つが `OscSynth` です。これはポリフォニーのボイスごとにオシレータを 1 つ直接使うもので、次のような単純なサイン波シンセになります。

```python
s = synth.OscSynth(wave=amy.SINE)
s.note_on(60,1)
s.note_off(60)
```

これをサンプラーとして使ってみましょう。Tulip には TR-808 のドラムサンプルバンク一式が PCM の `preset` 0〜18 として組み込まれており、さらに 136 個のドラム／パーカッションのサンプル（TR-909、Linn 9000、Univox MR-12、Tokyo Synthetics、80s Power Kit、Percussion）がプリセット 256〜391 にあります。サンプラーはそれぞれのピッチとパンを調整できます。次のように試せます。

```python
# OscSynth のセットアップには任意の AMY 引数を渡せます
s = synth.OscSynth(wave=amy.PCM, preset=8) # PCM 波形タイプ、preset=8（808 のカウベル）

s.note_on(50, 1.0)
s.note_on(40, 1.0) # 違うピッチ

s.update_oscs(pan=0) # 違うパン
s.note_on(40, 1.0)

s.update_oscs(preset=18) # プリセット 18 は 808 のシンバル
s.note_on(40, 1.0)
```

自分のサンプルを Tulip に読み込むこともできます。任意の .wav ファイルを用意し、[Tulip に転送してください。](getting_started.md#transfer-files-between-tulip-and-your-computer) そのうえで PCM プリセットとして読み込みます。

```python
amy.load_sample('sample.wav', preset=50)
s = synth.OscSynth(wave=amy.PCM, preset=50)
s.note_on(60, 1.0)
```

loopstart と loopend のパラメータがあれば、ループ区間付きの PCM プリセットも読み込めます（これらは WAVE のメタデータに格納されていることが多く、.wav ファイルにメタデータがあれば Tulip が解析します。サンプルファイル `/sys/ex/vlng3.wav` にはこの情報が入っています。メタデータを直接指定することもできます）。ループを指定するには `mode=amy.PCM_LOOP` を使います（ADSR がフェードアウトしている間もループを続けたい場合は `PCM_LOOP_FOREVER`）。

```python
amy.load_sample("/sys/ex/vlng3.wav", preset=50)  # 波形のループメタデータを読み込む
s = synth.OscSynth(wave=amy.PCM, preset=50, mode=amy.PCM_LOOP, num_voices=1)
s.note_on(60, 1.0) # ループします
s.note_on(55, 1.0) # ループします
s.note_off(55) # 止まります
```

`amy.unload_sample(preset_number)` でサンプルを RAM から解放できます。

## Juno-6 のパッチをプログラムから変更する

`run('juno6')` で Juno-6 のエディタを開く方法は先に紹介しました。コードからパッチを変更したい場合は、次のようにできます。

```python
run("juno6")
# REPL に戻る
juno6.vcf_res.set(64) # 0-127
```

Juno-6 のエディタに戻ると、レゾナンスのスライダーが実際に動いているのが見えます。

`juno6.` と入力して `TAB` キーを押すと、呼び出せる関数の一覧が見られます。


## Tulip での AMY の低レベル制御

Tulip のシンセは [AMY](https://github.com/shorepine/amy) が動かしています。これは非常に多機能な、マルチオシレータの加算・減算方式シンセです。機能が山ほどあります。ここまでの例はすべて「高レベル」な Tulip の Python モードでしたが、`amy.send()` を呼ぶだけで AMY のコマンドを直接送れます。

```python
import amy
amy.send(osc=30, wave=amy.SINE, freq=440, vel=1) # 440Hz のサイン波
amy.send(osc=30, vel = 0) # ノートオフ
amy.reverb(1) # グローバルリバーブをオン
amy.echo(level=1, delay_ms=400, feedback=0.8) # グローバルエコー
amy.reset() # すべての AMY オシレータをリセット
```

純粋な AMY のオシレータで 808 系のバスドラムの音を作る例です。

```python
amy.send(osc=31, wave=amy.SINE, amp=0.5, freq=0.25, phase=0.5)
amy.send(osc=32, wave=amy.SINE, bp0="0,1,500,0,0,0", freq="150,1,0,0,0,1", mod_source=31, vel=1)
```

AMY にできることをさらに深く知りたい場合は、[AMY の README](https://github.com/shorepine/amy/blob/main/README.md) を参照してください。


## コードでの MIDI 送受信

Tulip では、MIDI 入力に反応する関数を簡単に書けます。たとえば、チャンネル 1 の MIDI メッセージに反応する、ごく単純なサイン波オシレータを作りたいとします。必要なのは、先ほどのシーケンサ用と同じように MIDI のコールバック関数を書くことだけです。

```python
import midi, amy
def sine(m):
    if m[0] == 144: # MIDI メッセージ チャンネル 1 のバイト 0、ノートオン
        # osc 30 にサイン波を送る。MIDI のノートとベロシティ付き
        amy.send(osc=30, wave=amy.SINE, note=m[1], vel=m[2] / 127.0)

# 自作シンセの音を聴けるように、Tulip 標準の MIDI 処理をオフにする
midi.config.reset()

# コールバックを追加
midi.add_callback(sine)

# ここで Tulip に MIDI ノートを弾いてみます。キーボードがなければ midi_local でメッセージを送れます:
tulip.midi_local((144, 60, 100))

# サイン波が聞こえるはずです

# midi コールバックをオフに
midi.remove_callback(sine)

# デフォルトのシンセに戻す
midi.add_default_synths()

```

MIDI を送出するには `tulip.midi_out(message)` を使います。たとえば `tulip.midi_out([0x90, 0x40, 0x7F])` は、最初の MIDI チャンネルにノート 64・ベロシティ 127 のノートオンを送ります。これを使えば、たとえば Tulip のシーケンサのティックごとに MIDI メッセージを送出する、といったこともできます。

## モジュラーやアナログシンセへの CV 信号出力

Tulip は、側面の "i2c" ポートに接続した対応 DAC チップから、音声信号の代わりに CV 信号を出力できます。[DAC を入手すれば](https://github.com/shorepine/tulipcc/blob/main/docs/getting_started.md#dacs-or-adcs-for-modular-synths)、そのポートから任意の波形を送出でき、テンポに同期させたり、サンプル&ホールドを使ったりもできます。まずは、これらを GUI でまとめて扱えるユーザー提供のアプリ `waves.py` を試すのがおすすめです。

`waves.py` を入手するには、まず Wi-Fi に接続して Tulip World から取得します。詳しくは[使い始めのチュートリアル](getting_started.md)を参照してください。手順は次のとおりです。

```python
tulip.wifi(ssid, password)
world.download('waves.py')
run('waves')
```

すると次のような画面になります。

<img src="https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/waves.png" width=600>

エディタで `waves.py` のソースを見ると、UI のセットアップを除けばかなり単純なことが分かります。

```python
amy.send(osc=30, external_channel=1, wave=amy.SAW_DOWN, vel=1, freq=0.5, amp=1)
```
これは DAC の 1 チャンネル目に 0.5Hz、振幅 1 のノコギリ波を送り出すので、ピークトゥピークで 0〜5V になります。

止めるには次を送ります。

```python
amy.send(osc=30, amp=0)
```

個別の電圧を送ることもできます。

```python
import mabeedac
mabeedac.set(2.5, channel=0) # 最初の CV チャンネルに 2.5V を送ります
```

## カスタム FM 音色と AMY のパッチ（WOOD PIANO）

AMY のコマンドで自分の FM シンセシスを作る方法を見てみましょう。4 オペレータの "WOOD PIANO" という FM 音色は定番で、しかもかなり単純です。

まず、カスタムパッチを作ります。これは、パッチ番号（1024〜1055）に割り当てられる AMY コマンドの集合です。

そのためには、AMY に「今からやることを記憶し、内部シンセには送らない」よう指示します。次のようにします。

```python
amy.start_store_patch()
```

これ以降 AMY に送るものはすべて、内部シンセではなくカスタムパッチに記録されます。

続いて、パッチのセットアップコマンドを送ります。パッチは連続したオシレータで構成し、「ルートオシレータ」としてオシレータ 0 から始めてください。

WOOD PIANO のパッチは 4 オペレータで、それぞれにエンベロープと異なる変調振幅を持ちます。

```python
amy.send(osc=1, bp0="0,1,5300,0,0,0", phase=0.25, ratio=1, amp="0.3,0,0,1,0,0")
amy.send(osc=2, bp0="0,1,3400,0,0,0", phase=0.25, ratio=0.5, amp="1.68,0,0,1,0,0")
amy.send(osc=3, bp0="0,1,6700,0,0,0", phase=0.25, ratio=1, amp="0.23,0,0,1,0,0")
amy.send(osc=4, bp0="0,1,3400,0,0,0", phase=0.25, ratio=0.5, amp="1.68,0,0,1,0,0")
```

次に「ルート」オシレータの設定を送ります。これがノートオンを送る対象になります。ルートオシレータには「アルゴリズム」を与えます。これはオペレータをどう変調するかを示すものです。詳細は [AMY のドキュメント](https://github.com/shorepine/amy)を参照してください。ルートオシレータは自身の振幅エンベロープとピッチエンベロープ（`bp0` と `bp1`）も持ちます。


```python
amy.send(osc=0, wave=amy.ALGO, algorithm=5, algo_source="1,2,3,4", bp0="0,1,147,0", bp1="0,1,179,1", freq="0,1,0,0,1,1")
```

最後に、パッチの記録を止めてカスタムパッチ番号に保存するよう AMY に伝えます。

```python
amy.stop_store_patch(1024)
```

これで、このパッチ番号を Juno や DX7 のものと同じように使えます。ポリフォニックな wood piano を鳴らすには次のようにします。

```python
s = synth.PatchSynth(num_voices=5, patch=1024)
s.note_on(50, 1)
s.note_on(50, 1)
s.note_on(55, 1)
```

これらのセットアップコマンド（電源オフや再起動でクリアされる `store_patch` を含む）を `woodpiano.py` のような Python ファイルに保存しておき、再起動時に `execfile("woodpiano.py")` で設定し直す、という使い方も試してみてください。
