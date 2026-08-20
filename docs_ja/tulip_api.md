# Tulip Creative Computer API

ここでは、現在の [Tulip](../README.md) が備えている API を確認できます。

# 現在の API

**注意**: このページは、_main_ ブランチの最新コミット時点の API を表しています。Tulip ハードウェア向けのビルド（`tulip.upgrade()`）や macOS 版 Tulip Desktop のビルドは、これらの変更に追いついていない場合があります。[Tulip Web](https://tulip.computer/run) は常に _main_ ブランチと同期しているはずです。

![Tulip](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/tulip4.png)

## 全般

[Tulip](../README.md) は起動するとすぐ Python のプロンプトに入り、システムとのやり取りはすべてそこで行われます。コードやファイルを置く自分専用の場所として `/user` があり、システムのサンプルやプログラムは `/sys` に置かれています。（Tulip Desktop や Web では、`sys` フォルダは起動位置から見た `../sys` です。）

Tulip 内蔵のエディタで自分の Python プログラムを作って実行することも、Tulip の REPL プロンプトでリアルタイムに実験することもできます。

```python
# ファイルシステムを操作します。
# 使えるもの: ls, head, cat, newfile, cp, mv, rm, pwd, cd, mkdir, rmdir
ls
mkdir('directory')
cd('directory')

# REPL の画面と書式をクリアします
clear

# Tulip の起動時に何かを実行したい場合は boot.py に追加します
edit("boot.py")

# ファームウェアは wifi 経由で無線アップグレードできます
tulip.upgrade()

# スクリーンショットを撮ってディスクに保存します。画面が一瞬暗くなります
# ファイル名を指定しない場合は Tulip World にアップロードします（wifi が必要）
tulip.screenshot("screenshot.png")
tulip.screenshot()

# screenshot には x,y,w,h を渡して画面の一部だけを撮ることもできます
tulip.screenshot("middle.png", x=400,y=200,w=200,h=200)

# 現在の CPU 使用率を返します（Python コード、サウンド、一部の表示など CPU タスクに費やした時間の割合）
usage = tulip.cpu() # tulip.cpu(1) にすると、接続中の UART により詳しい内容を表示します

ms = tulip.ticks_ms() # 起点からの経過ミリ秒を返します。Arduino の millis() 相当

ms = tulip.amy_ticks_ms() # オーディオエンジンの起動からのミリ秒を返します

board = tulip.board() # ボードの種類を返します。例: "TDECK"、"N16R8" など
```

## スクリプト・パッケージ・スクリーンを使う／作る

Tulip では、自分で作ったプログラムや [Tulip World](#tulip-world) からダウンロードしたプログラムなど、いろいろな種類のプログラムを実行できます。単純な Python スクリプトやモジュールから、マルチタスクと UI を備えた全画面「アプリ」までさまざまです。これらのアプリは Tulip 上のエディタで編集・作成でき、他の人が使えるように Tulip World にアップロードできます。

カレントディレクトリの Python スクリプトは `execfile` で実行できます。

```python
>>> execfile("hello.py")
Hello world
```

Python のライブラリを作り、カレントディレクトリから `import` することもできます。

```python
>>> import my_library
>>> my_library.do_something()
Doing it
```

## Tulip のパッケージ

複数のファイル（グラフィックス、サウンド、複数の Python ファイル）に依存するプログラムを作る場合は、Tulip のパッケージを作るとよいでしょう。パッケージとは、単にファイルを入れたフォルダです。

```
rabbit_game/
... rabbit_game.py # メインのスクリプトはパッケージと同じ名前にします
... extra.py # 他の Python ファイルはここに入れられます
... rabbit_pic.png
... rabbit_pic1.png
... rabbit_sample.wav
```

メインの Python スクリプトはパッケージ名と同じでなければなりません。このスクリプトでは、使うものに応じて `tulip` や `amy` などを明示的に `import` する必要があります。あとは、そのフォルダがあるディレクトリから `run('rabbit_game')` すれば、あなたやユーザーがパッケージを起動できます。終了時にはパッケージの後始末が行われます。

デフォルトでは、パッケージはインポートされます（たとえば `import rabbit_game`）。`rabbit_game.py` にインポート時に走るコードがあれば、それが実行されます。`def run(app):` メソッドがあれば、ユーザーが切り替えたり終了したりできる `UIScreen` の全画面ウィンドウが作られます。

ゲームのようなサンプルをいくつか同梱しています。ぜひ見てみてください。
 * [`bunny_bounce`](https://github.com/shorepine/tulipcc/blob/main/tulip/fs/tulip/ex/bunny_bounce/bunny_bounce.py)
 * [`planet_boing`](https://github.com/shorepine/tulipcc/blob/main/tulip/fs/tulip/ex/planet_boing/planet_boing.py)
 * [`parallax`](https://github.com/shorepine/tulipcc/blob/main/tulip/fs/tulip/ex/parallax.py)
 * [`starfall`](https://github.com/shorepine/tulipcc/blob/main/tulip/fs/tulip/ex/starfall.py) - 70 年代後半風の固定画面シューティング。すべて BG プレーンに描画されています

Tulip World BBS は、パッケージを tar ファイルとしてアップロード／ダウンロードできます。`world.upload('package', username)` や `world.download('package')` を使ってください。

`/sys/ex` にもいくつか例を置いてあります。`run('app')` すると、カレントフォルダと `/sys/ex` フォルダの両方を探します。


### アプリ

パッケージを他のアプリと並行して動かし、終了ボタンとアプリ切り替えボタンのあるタスクバーを表示したい場合は、`UIScreen` を実装したパッケージにする必要があります。`UIScreen` の API は[後述](#uiscreen)しますが、いちばん簡単な例は次のとおりです。

```python
# 切り替え可能な自分のプログラム、program.py
def run(app):
    # アプリのセットアップ
    app.present() # 準備できたのでアプリを表示
```

これを `program` というパッケージに入れておけば、`run('program')` を呼んだときにアプリが起動し、タスクバーが表示されます。マルチタスクのアプリはセットアップ（`run` 関数）の後すぐに戻る必要があり、データやユーザー入力の処理はコールバックに任せます。必要になりそうなコールバックはひととおり用意しています。キーボード入力、MIDI 入力、音楽シーケンサのティック、タッチ入力などです。`UIScreen` は「アクティブ化」（アプリへの切り替えや初回実行）、「非アクティブ化」（アプリからの切り替え）、終了時のコールバックも設定します。

`UIScreen` を `game` として設定すると（`def run(app):` の中で `app.present()` の前に `app.game = True` を設定）、スプライトと BG のクリアや、キー入力を全画面ウィンドウだけに届けることなどを自動で処理します。

`game` ではタスクバーを隠すこともできます（`app.hide_task_bar=True`）。その場合ユーザーは、切り替えと終了に `control-Tab` と `control-Q` を使うことを知っておく必要があります。

`UIScreen` アプリの UI には LVGL / `tulip.UIX` のクラスを使ってください。そうすれば切り替え時に UI が自動的に表示・非表示されます。これは特に Tulip CC ハードウェアで重要で、UI 切り替えの描画が音楽やその他の時間にシビアなコールバックを妨げないようにしています。UI に他の Tulip 描画コマンドを使うこともできますが、アプリから切り替わるときに BG（多くの場合 TFB も）がクリアされる点に注意してください。その場合は activate コールバックで再描画する必要があります。`game` モードが有効なら、`deactivate` コールバックが BG とスプライトレイヤをクリアしてくれます。

REPL 自身も（特別な）マルチタスクアプリとして扱われ、常にリストの先頭にあり、終了できません。

アプリの切り替えはキーボードの `control-tab`、終了は `control-Q` で行えます。

マルチタスクアプリの例をいくつか同梱しています。ぜひご覧ください。
 * [`juno6`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/juno6.py)
 * [`wordpad`](https://github.com/shorepine/tulipcc/blob/main/tulip/fs/tulip/ex/wordpad.py)
 * [`worldui`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/worldui.py)
 * [`drums`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/drums.py)
 * [`voices`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/voices.py)

Tulip 上では、これらは編集可能な形で `my_X` という名前で見つかります（たとえば `/sys/ex/my_drums.py`）。これでドラムマシンを編集できます。オリジナルは読み取り専用で常に Tulip に焼き込まれているので、壊れる心配はありません。

`UIScreen` のチュートリアルは[音楽チュートリアル](music.md)を参照してください。

## Tulip World

まだ本当に初期段階ですが、Tulip には **TULIP ~ WORLD** というネイティブのチャット＆ファイル共有 BBS があり、他の Tulip オーナーと交流できます。最新のメッセージやファイルを取得したり、自分でメッセージやファイルを送ったりできます。

`run('worldui')` で試せます。まず `world.username="my_name"` を実行してユーザー名を決めるとよいでしょう。

下層の Tulip World API を直接呼ぶこともできます。


```python
# Tulip Web では world_web を使ってください
if(tulip.board()=="WEB"):
    import world_web as world
else:
    import world

messages = world.messages(n=500, mtype='files') # 最新のファイル一覧を返します（重複除去はしません）
messages = world.messages(n=100, mtype='text') # 最新のチャットメッセージ一覧を返します

# Tulip web では messages の出力を代入できません。
# 表示以外のことをしたい場合は、自分の done コールバックを使ってください:
world.messages(n=25, done=do_something)

# メッセージやファイルを投稿するときはユーザー名を設定します。最短 1 文字、最長 10 文字
world.post_message("hello!!") # Tulip World にメッセージを送ります。ユーザー名が必要で、未設定なら入力を求められます。

world.upload(filename,  description) # Tulip World にファイルをアップロードします。ユーザー名が必要。description は任意（25 文字）
world.upload(folder, description) # フォルダをパッケージ化して Tulip World にアップロードします
world.download(filename) # Tulip World に filename という名前の最新ファイルがあればダウンロードします
world.download(filename, username) # 指定ユーザーの filename という最新ファイルがあればダウンロードします
world.download(package_name) # パッケージをダウンロードして展開します

world.ls() # 最近のユニークなファイル名／ユーザー名を一覧表示します
world.ls(100) # 件数（最新順）は任意指定

# AMYboard World: amyboard.com で共有されたスケッチは Tulip でも動きます。
# download() は最新の sketch.py を user/current/ に取得し、AMYboard 流に開始します。
# つまりシンセをリセットし、スケッチの保存済みノブ状態を適用し、その loop() を
# シーケンサにスケジュールします。CV の入出力呼び出しは Tulip では何もしません。
# I2C アクセサリ（OLED ディスプレイ、ロータリーエンコーダ）は動作します。
world.amyboard.download(sketch_name) # 例: world.amyboard.download("eno_ambient")
world.amyboard.download(sketch_name, username) # 特定ユーザーの最新版
world.amyboard.download(sketch_name, username, start=False) # ダウンロードのみで実行しない
world.amyboard.ls() # 最近の AMYboard World スケッチを一覧表示します

import amyboard
amyboard.stop_sketch() # 実行中スケッチの loop() を停止します
```

重要な注意: Tulip World は [Tulip/AMY/Alles の Discord](https://discord.gg/TzBFkUb8pG) 上で動くボットがホストしています。システムの悪用があった場合はキーを失効させます。Tulip World をより安定した楽しい場にするための協力をぜひお願いします。


## Tulip のエディタ

Tulip には pico/nano をベースにしたテキストエディタが付属しています。シンタックスハイライト、検索、保存／名前を付けて保存に対応しています。

```python
# 指定したファイル名で Tulip のエディタを開きます。
# Control-X で保存。ファイル名がなければ入力を求められます。
# Control-O は名前を付けて保存。新しいファイル名に書き出します
# Control-W で検索
# Control-R は現在のバッファに読み込むファイル名を尋ねます
edit("game.py")
edit() # ファイル名なし
```


## ユーザーインターフェース

自分のユーザーインターフェースを作れるように [LVGL 9](https://lvgl.io) を同梱しています。LVGL は Tulip のような制約の多いハードウェア向けに最適化されています。シンプルな Python コマンドで見栄えのよい UI を作れます。単に `import lvgl` してウィジェットを自分で組めば、LVGL を直接使うこともできます。着想を得るには [LVGL のサンプルページ](https://docs.lvgl.io/8.3/examples.html)を参照してください。（本稿執筆時点で、LVGL の Python サンプルは私たちが使うバージョン（9.0.0）には移植されていませんが、ほとんどは動くはずです。）

ユーザーインターフェースは、マルチタスク対応の Tulip パッケージである `UIScreen` の中で組むのが最良です。`UIScreen` が、アプリ上への要素の配置とマルチタスクの扱いを引き受けてくれます。

ボタン、スライダー、チェックボックス、1 行テキスト入力といった LVGL のより単純な用途には、`UICheckbox`、`UIButton`、`UISlider`、`UIText`、`UILabel` といったラッパークラスを用意しています。これらは完全に Python で実装されているので、自作 UI のヒントとして [`ui.py`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/ui.py) を参照してください。[`buttons.py`](https://github.com/shorepine/tulipcc/blob/main/tulip/fs/tulip/ex/buttons.py) の例や、`/sys/ex` にある [`drums`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/drums.py)、[`juno6`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/juno6.py)、[`wordpad`](https://github.com/shorepine/tulipcc/blob/main/tulip/fs/tulip/ex/wordpad.py) といった、より完成度の高い例も参考になります。

<a id="uiscreen"></a>
### UIScreen

マルチタスクに対応した Tulip のアプリは `UIScreen` と呼ばれ、UI 要素の追加やアプリ間の切り替えの機能をまとめています。Tulip のパッケージは、メインの Python ファイルにある `run(screen)` を呼ぼうとします。それが存在する場合、`run(screen)` 関数はすぐに終了し、各種のコールバックに制御を渡すことが期待されます。これにより複数のアプリを同時に動かせます。シーケンサや MIDI のコールバックを共有する音楽アプリでは特に有用です。

パッケージ内の `app.py` に `def run(screen):` 関数があれば、`run(app)` したときにデフォルトで `UIScreen` が作られます。その `UIScreen` オブジェクトが `screen` に渡されます。このオブジェクトをアプリのグローバルな状態として扱えるほか、アプリの各種パラメータを読み書きできます。

```python
def run(screen):
    # 以下はすべてデフォルト値です:
    screen.bg_color = 0 # 画面 BG の Tulip カラー
    screen.keep_tfb = False # アプリ実行中に TFB を隠すかどうか
    screen.offset_y = 100 # デフォルトでは、タスクバーの余地を残すため画面は 0,100 から「始まり」ます
    screen.activate_callback = None # アプリ起動時と、アプリに切り替わったときに呼ばれます
    screen.deactivate_callback = None # アプリから切り替わったときに呼ばれます
    screen.quit_callback = None # 終了ボタンが押されたときに呼ばれます。なお終了時は deactivate_callback が先に呼ばれます
    screen.handle_keyboard = False # キーボード入力を受け付ける UI 部品を設置する場合

    screen.group.set_style_text_font(lv.font_tulip_11,0) # 必要ならアプリ全体のデフォルトフォントを設定します

    # screen.add() で UIElement オブジェクトを追加して UI を組み立てます
    screen.add(tulip.UILabel("hello there"), x=500,y=100)
    # LVGL のアラインメントを使って、直前に追加したオブジェクトを基準に配置できます
    # アラインメントの一覧は https://docs.lvgl.io/master/widgets/obj.html を参照
    screen.add(tulip.UILabel("under that one"), direction=lv.ALIGN.BOTTOM_MID)
    # 準備ができたら次を実行します
    screen.present()

def quit(screen):
    # quit コールバックには screen オブジェクトが渡されます。これを使って後始末します
def activate(screen):
    # 明示的に再描画したいものがあればここで行います。add() で追加した LVGL 部品は自動的に表示されます
```

Tulip の `UIScreen` アプリは、ループで待ったり `sleep` を呼んだりしてはいけません。処理はすべてコールバックに任せます。たとえばドラムマシンは、次の音を鳴らすためにシーケンサのコールバックを待ちます。エディタアプリは次のキー入力をキーボードのコールバックに頼ります。こうすることで Tulip は複数のプログラムを同時に実行できます。

`UIScreen` を使ったより複雑な UI の例をいくつか挙げます。
 * [`juno6`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/juno6.py)
 * [`drums`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/drums.py)
 * [`voices`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/voices.py)

これらを Tulip 上で編集したい場合は、`/sys/ex/my_drums.py` のように `/sys/ex/my_X.py` にある編集可能版を使ってください。

実行中のマルチタスクアプリは `tulip.running_apps` で確認できます。これはアプリ名をキーとする dict です。実行中アプリのパラメータを設定・参照できます。`tulip.repl_screen` は常に REPL の UIScreen を返します。`tulip.app('drums')` のようにしてプログラムからアプリを切り替えられます。現在実行中の UIScreen は `tulip.current_uiscreen()` です。

```python
>>> tulip.running_apps['voices'].piano_y
320

>>> tulip.repl_screen.bg_color
9
```



### その他のインターフェース要素

`tulip.keyboard()` でタッチキーボードを呼び出せます。キーボードアイコンをタップすると閉じますし、もう一度 `tulip.keyboard()` を実行しても消せます。

入力先はキーボードフォーカスのある LVGL のテキストフィールドです。フィールドをタップして移ればフォーカスに追随するので、キーボードをつながずにタッチだけでフォームを埋められます。フォーカスのあるフィールドがないときは従来どおりコンソールに送ります（REPL がこの場合です）。引数にフィールドを渡すと最初からそこへ入力できます。キーボードを開くボタンを押した時点でフォーカスはそのボタンに移っているので、自前のキーボードボタンを持つアプリは対象のフィールドを明示してください。

よく使う操作のためのランチャーを起動時に用意しています。右下の小さな灰色のアイコンから開けます。

LVGL のフォントには、デフォルトの [LVGL `montserrat` フォント](https://docs.lvgl.io/master/details/main-modules/font.html)（例: `font=lv.font_montserrat_12`）か、内蔵の Tulip BG フォント（例: `font=lv.tulip_font_13`）が使えます。

```python
tulip.keyboard() # ソフトキーボードの開閉
tulip.keyboard(field) # LVGL のテキストエリアに入力する状態で開く
tulip.launcher() # ランチャーの開閉

# LVGL の呼び出しを直接使ってもかまいません。強力なライブラリで、多くの機能とカスタマイズ性があり、すべて Python から使えます。
import lvgl as lv

# tulip.lv_scr は起動時のベース画面で、LVGL のベース画面として使えます。
calendar = lv.calendar(lv.current_screen())
calendar.set_pos(500,100)

# tulip.UIX クラスを使って、単純な UI 要素をアプリに追加できます。

# UISlider: スライダーを描画
# bar_color - バー全体の色。2 色使う場合は設定済み部分の色
# unset_bar_color - バーの未設定側の色。None なら全体が 1 色になります
# handle_v_pad, h_pad -- バーの上下 / 左右に何ピクセルはみ出すか
# handle_radius - 0 で四角
screen.add(tulip.UISlider(val=0, w=None, h=None, bar_color=None, unset_bar_color=None, 
    handle_color=None, handle_radius=None, handle_v_pad=None, handle_h_pad=None, callback=None))

# UIButton: テキスト付きの押しボタン
screen.add(tulip.UIbutton(text=None, w=None, h=None, bg_color=None, fg_color=None, 
    font=None, radius=None, callback=None))

# UILabel: テキスト
screen.add(tulip.UILabel(text="", fg_color=None, w=None, font=None))

# UIText: テキスト入力
screen.add(tulip.UIText(text=None, placeholder=None, w=None, h=None, 
    bg_color=None, fg_color=None, font=None, one_line=True, callback=None))

# UICheckbox
# 任意でチェックボックスの隣にラベルを描画できます
screen.add(tulip.UICheckbox(text=None, val=False, bg_color=None, fg_color=None, callback=None))
```

`UIX` クラスの使い方は [`buttons.py`](https://github.com/shorepine/tulipcc/blob/main/tulip/fs/tulip/ex/buttons.py) の例を参照してください。

### タブ付き UI

`TabView` クラスを使えば、`UIScreen` の中にタブ付き UI を組めます。要素を追加できる小さな `UIScreen` のように振る舞います。

```python
def run(screen):
    # UIScreen の左側に、3 つのタブを持つ TabView を作ります
    tabview = ui.TabView(screen, ["tab1", "tab2", "tab3"], size=80,  position = lv.DIR.LEFT)
    # 任意の UIElement を作ります
    bpm_slider = tulip.UISlider(tulip.seq_bpm()/2.4, w=300, h=25, 
        callback=bpm_change, bar_color=123, handle_color=23)
    # 目的のタブに追加します。API は UIScreen.add() と同じです
    tabview.add("tab2", bpm_slider, x=300,y=200)
    screen.present()
```

## 入力

Tulip は USB キーボード入力、USB マウス入力、タッチ入力に対応しています。また、ソフトウェアのオンスクリーンキーボードや、Tulip CC では I2C 接続のキーボードやジョイスティックにも対応します。Tulip Desktop と Tulip Web では、マウスクリックがタッチ点として扱われ、コンピュータのキーボードが使えます。

（おそらくハブ経由で）USB マウスを Tulip に接続している場合、デフォルトでマウスポインタが表示され、クリックはタッチダウンとして扱われます。

```python
# 矢印キー、Z、X、A、S、Q、W、Enter、' から、ジョイスティック風の押下状態のマスクを返します
tulip.joyk()

# ジョイスティックの押下を判定します。UP, DOWN, LEFT, RIGHT, X, Y, A, B, SELECT, START, R1, L1 を試してください
if(tulip.joyk() & tulip.Joy.UP):
    print("up")

# 現在押されているキーボードのスキャンコード（最大 6 個）と、修飾キーのマスク（ctrl、shift など）を返します
(modifiers, scan0, scan1... scan5) = tulip.keys()

# キーの ASCII コードを取得します
(char, scan, modifier) = tulip.key_wait() # キー入力を待ち、スキャンコードと修飾キーも返します
ch = tulip.key() # 即座に戻ります。何も押されていなければ -1 を返します

# プログラム内でキーコードをスキャンする場合、キーが下位の python プロセスに送られないよう
# 「キースキャン」モードをオンにしたくなるかもしれません
tulip.key_scan(1)
tulip.key_scan(0) # 戻すのを忘れないでください。さもないと REPL に入力できなくなります

# キーボードのキーをリマップする必要がある場合（デフォルトは US 配列）
tulip.remap()  # 対話式。boot.py に書き出すこともできます
tulip.key_remap(scan_code, modifier, target_cp437_code)

# キーボードのコールバックを登録することもできます。他と共存する全画面アプリで便利です
# キーボードのコールバックは同時に 1 つしか動かせません
tulip.keyboard_callback(key)
def key(k):
    print("got key: %d" % (key))

tulip.keyboard_callback() # コールバックを解除します

# Tab5 内蔵キーボードのインジケータ LED の明るさ、0-100（Tab5 のみ）
tulip.keyboard_brightness(5)
tulip.keyboard_brightness()  # 現在の設定を返します

# 直近のタッチパネル座標を、最大 3 本指まで返します
(x0, y0, x1, y1, x2, y2) = tulip.touch()

# 必要ならタッチスクリーンのキャリブレーションを変更します（Tulip CC のみ）
# 値の決定には ex/calibrate.py を実行してください
tulip.touch_delta(-20, 0, 0.8) # x に -20、y に 0、y スケール 0.8
tulip.touch_delta() # 現在の delta を返します

# 生のタッチイベントを受け取るコールバックを設定します。指／マウスが離れたとき up == 1 になります
def touch_callback(up):
    t = tulip.touch()
    print("up %d points x1 %d y1 %d" % (up, t[0], t[1]))

tulip.touch_callback(cb)
```

### 日本語入力（IME）

`ime` は日本語入力です。ローマ字を入れて、かな・漢字が出ます。日本語コンソール
フォントを必要とするので、それを持つボード（Tab5・Tulip Desktop・Tulip Web）にあり、
ESP32-S3 のボードにはありません。`boot.py` に次を書きます。

```python
import ime
ime.start()
```

キーボードを IME に渡す/戻すキーは 4 つあります。どのキーボードでも実際に押せるものが
1 つはあるようにするためです: **変換**、**かな**、**`Ctrl-Space`**、**`Ctrl-\`**。

`Ctrl-Space` は他の日本語入力でも使われている定番で、どのキーボードでも独立した
2 キーです。`Ctrl-\` はそうではありません。Tab5 純正の 70 キーキーボードでは
バックスラッシュが Sym レイヤにあり、Ctrl を同時に押せません。変換・かなは JIS
キーボードで空いており（以前は何も返していませんでした）、入力切り替えこそが
そのキーの意味です。

半角/全角キーは US キーボードのバッククォートと同じ位置なので既定では触りません。
使いたい場合は自分で割り当ててください。

```python
tulip.key_remap(0x35, 0, 0x1c)   # 半角/全角キー（JIS の位置）
```

4 つのどれも押せないキーボードでは、実際に何が送られているかを調べて割り当てます。

```python
ime.keytest()        # キーを押すと コード / モディファイア / スキャンコード が出ます
ime.toggle(<code>)   # そのキーをトグルにします
ime.keytest(False)   # 終了
```

`ime.start()` は軽い処理で、トグルキーを有効にするだけです。辞書は IME を最初に
オンにしたときに読み込まれます（約 2 秒、1 回だけ）。

IME がキーボードを持っている間はカーソルがオレンジになるので、次のキーがどちらの
言語になるか打つ前に分かります。コンソール・エディタ・フォーカスされた LVGL の
テキストエリアのいずれでも同じです。打った文字は行に直接入らず、コンソール
最下行の変換ストリップに出ます。確定した文字列だけがエディタ・LVGL のテキストエリア・
REPL に届きます。ストリップは入力中だけ出ます。単語と単語の間は最下行がコンソールに
戻り、IME がキーボードを持ったままであることはカーソルの色が示します。

| キー | 読みの入力中 | 文節が変換された状態 |
|---|---|---|
| `a`-`z` | ローマ字。確定した分がかなになります | 確定して新しい読みを開始 |
| space | 辞書にある最長の読みを変換 | 次の候補 |
| ↑ / ↓ | — | 前 / 次の候補 |
| → | — | この文節を確定し、残りを変換 |
| ← | — | 文節を 1 かな分縮める |
| return | 読みをそのまま確定 | 候補を確定 |
| backspace | かなを 1 文字削除 | 未変換の読みに戻す |
| escape | 入力中のものを破棄 | 未変換の読みに戻す |
| `Ctrl-I`（= tab） | 読み全体をカタカナに | この文節をカタカナに |
| `Ctrl-U` | 読み全体をひらがなに | この文節をひらがなに |
| `,` `.` `-` `[` `]` `/` | 、 。 ー 「 」 ・ になります | |
| `A`-`Z`、数字 | 確定してそのまま通します | 確定してそのまま通します |

`nn` は ん ではありません。な行を続けられない文字の前の `n` が ん になります。
つまり `kanji` は かんじ、`konnichiwa` は こんにちわ です。`n'` が明示的な 1 つの ん で、
ほんや（`hon'ya`。`honya` は ほにゃ）もこれで打ちます。

変換は文全体ではなく文節単位です。形態素解析器は載っていないので、space は
現在位置から辞書にある最長の読みを変換し、→ で残りに進みます。
`にほんごにゅうりょく` + space + → + return で 日本語入力 になります。

`Ctrl-I` と `Ctrl-U` は MS-IME・ATOK・mozc の F7 / F6 で、`Ctrl-` の綴りもそこから
来ています（Tab5 のキーボードにはファンクション行がありません）。辞書を見ずに読み
全体を変換するのが要点です。space は辞書に「ある」最長の読みを変換するので
`aisukuri-mu` + space は 愛すくりーむ ですが、`aisukuri-mu` + `Ctrl-I` は
アイスクリーム になります。tab がこの意味になるのは入力中だけで、何も入力して
いなければ通常の tab です。半角カナはありません（コンソールフォントに半角カタカナが
無いため）。英数キーも無く、大文字と IME オフがその役目です。

カタカナとひらがなは space で送れる最後の 2 候補でもありますが、そちらは辞書が
一致した文節の分だけです。単語全体は上のキーを使ってください。

```python
ime.start()                  # トグルを有効化。辞書は初回オン時まで読みません
ime.start(True)              # 先に辞書を読み込む（約 2 秒）
ime.stop()                   # 無効化。キーボードは通常に戻ります
ime.target(my_textarea)      # 確定文字列の宛先を推測させず明示します
ime.target(None)             # 推測に戻す（LVGL のフォーカス → エディタ → REPL）
ime.toggle()                 # オン/オフを切り替えるキーコード
ime.toggle(code)             # 別のキーに変更
ime.keytest()                # 各キーが送るコードを表示（上のコードを調べる用）
tulip.ime()                  # いま IME がキーボードを持っているか
```

辞書は `/sys/ime/jdic.z` で、見出し語 59073 語。Google mozc の OSS 辞書
（BSD-3-Clause、語彙は IPAdic）から `tulip/shared/gen_jdict.py` が生成します。
定番の SKK-JISYO は GPL なので使っていません。辞書が無くてもかな入力は動きます。

<a id="i2c--grove--mabee"></a>
## I2C / Grove / Mabee

Tulip のハードウェアには側面に I2C ポートがあり、さまざまな入出力デバイスを接続できます。現在サポートしているのは次のとおりです。

 - [Mabee DAC（最大 10V）](https://www.makerfabs.com/mabee-dac-gp8413.html) - `import mabeedac; mabeedac.set(volts, channel)` を使います。サウンドのドキュメントにある CV 制御の節も参照してください
 - [ADC（最大 12V）](https://shop.m5stack.com/products/adc-i2c-unit-v1-1-ads1100?variant=44321440399617) - `import m5adc; m5adc.get()` を使います
 - [DAC（1 チャンネル、最大 3.3V）](https://shop.m5stack.com/products/dac-unit) - `import m5dac; m5dac.set(volts)` を使います
 - [DAC2（2 チャンネル、最大 10V）](https://shop.m5stack.com/products/dac-2-i2c-unit-gp8413) - `import m5dac2; m5dac.set2(volts, channel)` を使います
 - [CardKB キーボード](https://shop.m5stack.com/products/cardkb-mini-keyboard-programmable-unit-v1-1-mega8a) - `import m5cardkb` を使うと、cardKB が自動的に Tulip のキーボードになります。起動時から使うには `boot.py` に入れてください。
 - [8 エンコーダのノブ](https://shop.m5stack.com/products/8-encoder-unit-stm32f030) - `import m5_8encoder` を使います。詳しくは [m5_8encoder.py](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/m5_8encoder.py) を参照してください
 - [8 アングルのノブ](https://shop.m5stack.com/products/8-angle-unit-with-potentiometer) - `import m58angle; m58angle.get(ch)` を使います
 - [Digiclock 7 セグメント時計](https://shop.m5stack.com/products/red-7-segment-digit-clock-unit) - `import m5digiclock; m5digiclock.set('ABCD')` を使います
 - [ジョイスティック](https://shop.m5stack.com/products/i2c-joystick-unit-v1-1-mega8a) - `import m5joy; m5joy.get()` を使います
 - [Extend GPIO](https://shop.m5stack.com/products/official-extend-serial-i-o-unit) - `import m5extend; m5extend.write_pin(pin, val); m5extend.read_pin(pin)` を使います

## ネットワーク

Tulip CC は Wi-Fi ネットワークに接続でき、Python 標準の requests ライブラリで TCP と UDP にアクセスできます。URL からデータを取得するための便利な関数もいくつか用意しています。

```python
# wifi ネットワークに接続します（Tulip Desktop と Web では不要）
tulip.wifi("ssid", "password")

# 接続と同時に Wi-Fi の規制ドメインを設定します。デフォルトの "01"（world safe
# mode）ではチャンネル 12〜14 が閉じたままなので、そこにいる AP は（日本のルータ
# ではよくあります）国コードを指定するまで見えません。（現状は Tab5 のみ）
tulip.wifi("ssid", "password", country="JP")

# 規制ドメインだけを読み書きします。Wi-Fi が起動している必要があるので、
# tulip.wifi() を呼んだ後で使います。第 2 引数に False を渡すと、AP のビーコンに
# 上書きさせずに国コードを固定します。
tulip.wifi_country()      # -> "JP"
tulip.wifi_country("JP")

# IP アドレスを取得、または接続状態を確認します
ip_address = tulip.ip() # 未接続なら None を返します

# URL の内容をディスクに保存します（wifi が必要）
bytes_read = tulip.url_save("https://url", "filename.ext")

# URL の内容をメモリに取得します（wifi が必要。RAM 使用量に注意）
content = tulip.url_get("https://url")

# PUT の API に URL をアップロードします。file_server.py で使っています
tulip.url_put(url, "filename.ext")

# NTP サーバーから時刻を設定します（wifi が必要）
tulip.set_time() 
```

### SSH

`ssh` は Python で書かれた SSH-2 クライアントです。ネットワーク上の別のマシンに
ログインし、そのシェルを Tulip のテキストコンソールに表示します。

```python
import ssh

# コマンドを 1 つ実行して、その出力を受け取ります
print(ssh.run("192.168.1.10", "me", "uname -a", password="secret"))

# 対話シェル。リモートのシェルが終了すると戻るので、抜けるときは `exit` と入力します
ssh.shell("192.168.1.10", "me", password="secret")

# パスワードの代わりに鍵を使います。パスフレーズなしの OpenSSH RSA 鍵に限ります。
# Tulip 上にコピーしてそのパスを渡してください（`ssh-keygen -p` でパスフレーズを外せます）
ssh.shell("192.168.1.10", "me", key="/user/id_rsa")

# チャネルを自分で操作したい場合の部品
c = ssh.connect("192.168.1.10", "me", key="/user/id_rsa")
c.exec_command("ls /tmp")
while not c.closed: print(c.read())
c.close()
```

暗号方式は 1 通りだけで、交渉の余地はありません。鍵交換 `curve25519-sha256`、
ホスト鍵 `rsa-sha2-256`、暗号 `aes128-ctr`、MAC `hmac-sha2-256` です。標準的な
OpenSSH サーバーはいまも Ed25519 と並べて RSA ホスト鍵を持っているので、これで
接続できます。ただし Ed25519 ホスト鍵**しか**持たないサーバーはここでは検証でき
ないため、無検証で信用するのではなく接続を拒否します（mbedTLS に EdDSA がなく、
このビルドの `hashlib` に SHA-512 もないため、Ed25519 の検証は両方を Python で
書き起こすことになります）。

ホスト鍵は初回接続時に `/user/known_hosts` に記録され、次回以降食い違った場合は
接続せずに `ssh.HostKeyError` を送出します。未知のホストも拒否したい場合は
`accept_new=False` を渡してください。

時間がかかるのは 1 回だけ実行される部分です。ハンドシェイクに約 0.7 秒（X25519 の
スカラー倍 2 回と RSA 署名検証 1 回）、パスワードではなく鍵で認証する場合はさらに
約 1.6 秒かかります。

セッション中はコンソールが本物の端末に切り替わります（後述の `tulip.term_start()`）。
そのため `vi`、`top`、`less`、`tmux` のような全画面プログラムも動きます。xterm の
サブセットで、カーソル位置指定、スクロール領域、行・文字の挿入と削除、代替画面、
自動折り返し、タブストップ、DEC 罫線素片、そしてプログラムが端末に問い合わせたときに
返す応答を実装しています。pty はコンソール自身のサイズで開くので、`stty size` は
画面で見えている通りの値を返します。

解釈できないシーケンスは印字せずに読み捨てます。シェルがプロンプトのたびに送る
ウィンドウタイトルもここに含まれます。書き込みの境界をまたいだシーケンスも継続して
解釈します。ssh はネットワークから届いたぶんをそのまま渡すので、シーケンスは
どこで分断されるか分からないためです。

矢印・Delete・Home・End・Insert・ファンクションキーは端末のシーケンスとして送ります。
形式はプログラムが要求したものに従います。アプリケーションカーソルキーを有効にした
プログラムには `ESC O A` を、シェルには `ESC [ A` を送ります。Page Up と Page Down は
Ctrl-Y と Ctrl-V としてリモートに届きます。このキーボードが以前からそう解釈して
きたためです。

律速は回線ではなく描画です。回線が 77 KB/s なのに対し画面は約 30 KB/s で、全画面の
描き直しに 0.1 秒ほどかかります。大きなファイルを `cat` すると、ネットワークが
出せるはずの速度よりは遅くなります。

Ctrl-C は Python を中断せずリモートのシェルに送られます。セッション中はそれが期待
される動作で、セッションが終わればキーボードは REPL に戻ります。

セッションは画面を借りて返します。コンソールに出ていた内容はセッション開始時に退避され、
終了時に戻ります。そのためアプリを終了してもリモートシェルの表示が REPL 画面に残りません。
セッション中に別アプリへ切り替えたときも 2 つの画面が入れ替わるので、REPL がセッションの
画面を見ることはなく、戻ればセッションの画面もそのまま残っています。

以上をアプリにしたものがランチャーの `SSH`（`run('sshterm')` でも起動します）です。
ホスト・ユーザー・パスワードまたは鍵ファイル・ポートの入力フォームを表示し、パスワード
以外を `/user/sshterm.conf` に記憶して、接続するとコンソールをセッションに渡します。
通常の切り替え可能アプリなので、接続中もタスクバーは生きています。別のアプリに切り替えて
戻ってきてもセッションは続いており、タスクバーから終了すれば切断します。行頭で `~.` と
入力しても切断できます（OpenSSH のエスケープと同じです）。

フォームにはキーボードボタンがあります。何もつないでいない Tab5 のためのもので、
オンスクリーンキーボードは最後にタップしたフィールドへ入力します。キーボードは画面の
下半分を占めるため、フィールドや Connect ボタンが隠れないよう、フォームは 2 列に
配置してあります。

## 非同期処理

`asyncio` を同梱しているほか、将来の実行を予約するための、より簡単な `tulip.defer()` コールバックも用意しています。

```python
import asyncio
async def sleep(sec):
    await asnycio.sleep(sec)
    print("done")
asyncio.run(sleep(5))


def hello(t):
    print("hello called with arg %d" % (t))

tulip.defer(hello, 123, 1500) # 1500ms 後に呼ばれます
```


## 音楽 / サウンド

Tulip には AMY シンセサイザーが付属しています。FM、PCM、減算合成、加算合成、部分音合成、フィルタなどをサポートする、非常に多機能な 250 オシレータのシンセです。詳しくは [AMY のドキュメント](https://github.com/shorepine/amy/blob/main/README.md)を参照してください。Tulip 版の AMY はステレオサウンド、コーラス、リバーブを備えています。Juno-6 と DX7 の全パッチに加えて、PCM パッチセットの「小さい」版（29 パッチ）を含みます。Tulip 上で WAVE ファイルをサンプルとして読み込むこともできます。

Wi-Fi に接続すれば、Tulip は [Alles のメッシュ](https://github.com/shorepine/alles/blob/main/README.md)も制御できます。Alles は AMY のラッパーで、Wi-Fi 経由でリモートのスピーカーや他のコンピュータ、他の Tulip 上のシンセを制御できます。何台でも Alles スピーカーを wifi に接続すれば、すぐにサラウンドサウンドになります。詳細と音楽のサンプルは Alles の[使い始めのチュートリアル](https://github.com/shorepine/alles/blob/main/getting-started.md)を参照してください。

Tulip は、Tulip CC の I2C ポートに接続した CV 出力に AMY の信号をルーティングすることもできます。[Mabee DAC](https://www.makerfabs.com/mabee-dac-gp8413.html) 1〜2 台、あるいは同等の GP8413 構成が必要です。これにより、正確な LFO を CV 経由でモジュラーや古いアナログシンセに送れます。

**Tulip での音楽について、さらに多くの情報は[音楽チュートリアル](music.md)を参照してください。**

![With Alles](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/nicoboard-alles.jpg)


### synth

確保できるシンセサイザーを管理する AMY のラッパーを提供しています。これらはボイススティールや、下層のシンセパッチ用のオシレータ確保を面倒見てくれます。ほとんどの用途ではこちらの利用をおすすめします。より直接的な制御が必要なら AMY を使えます。

`synth.PatchSynth` を使うと、内蔵パッチをベースにしたシンセサイザーを作れます。0〜127 は Juno-6 のパッチ、128〜255 は DX-7 のパッチ、256 はピアノです。自分でパッチを作ることもできます。

```python
syn = synth.PatchSynth(num_voices=2, patch=143) # 2 音ポリフォニー、パッチ 143 は DX7 BASS 2
```

Juno-6 のベースと DX7 のパッドのように、マルチティンバーで鳴らしたい場合は次のようにします。

```python
synth1 = synth.PatchSynth(num_voices=1, patch=0)  # Juno
synth2 = synth.PatchSynth(num_voices=1, patch=128)  # DX7
synth1.note_on(50, 1)
synth2.note_on(50, 0.5)
synth1.note_off(50)
```

`OscSynth` は、管理されたシンセとして AMY オシレータのパラメータを直接制御できます。

```python
syn = synth.OscSynth(wave=amy.PCM, preset=10) # PCM 波形タイプ、preset=10（808 のカウベル）
```

`OscSynth` と `amy.load_sample` を使えば、Tulip のストレージ上の WAV ファイルからサンプルを読み込めます。

```python
amy.load_sample('sample.wav', preset=50)
s = synth.OscSynth(wave=amy.PCM, preset=50)
s.note_on(60, 1.0)
```

シンセのリソースを解放するには `syn.release()` を使います。

好みの構成にシンセを設定できたら、その状態を保存して次回起動時に復元できます。`tulip.save_synth_state()` は、すべての AMY シンセの現在の構成を読み取り、それを復元するコマンドを `boot.py`（または指定したファイル）の末尾に追記します。再度保存すると、以前保存した状態は置き換えられます。

```python
tulip.save_synth_state()  # 現在のシンセ状態を boot.py に追加します
tulip.save_synth_state('my_setup.py')  # 後で execfile() するために別ファイルに保存することもできます
```


### AMY の低レベル制御

`amy.py` を使えば AMY シンセサイザーを直接制御できます。

```python

amy.drums() # テスト曲を鳴らします
amy.volume(4) # 音量を変更
amy.reset() # 鳴っている音楽／音をすべて停止
amy.send(synth=1, patch=129, num_voices=1) # シンセ 1 に DX7 のパッチを設定
amy.send(synth=1, note=45, vel=1) # 音を鳴らします
amy.send(synth=1, pan=0) # 左チャンネルに設定
amy.send(synth=1, pan=1) # 右チャンネルに設定

# メッシュモードを開始（wifi 経由で複数のスピーカーを制御）
# 一度メッシュモードにすると、Tulip を再起動するまでローカルモードには戻せません。
alles.mesh() # wifi をオンにした後で実行。tulip 自身は AMY メッセージの再生を停止します。
alles.mesh(local_ip='192.168.50.4') # Tulip Desktop でネットワークを指定するときに便利

alles.map() # メッシュ上で起動している Alles シンセを返します

amy.send(synth=1, patch=101, num_voices=1) # メッシュ内のすべての Alles スピーカーにパッチを読み込む
amy.send(synth=1, note=50, vel=1) # メッシュ内のすべての Alles スピーカーが反応します
amy.send(synth=1, note=50, vel=1, client=2) # 特定のクライアントだけ
```

自分の WAVE ファイルを楽器のように鳴らせるサンプルとして読み込むには、`amy.load_sample` を使います。

```python
# 容量と RAM を節約するため、WAVE ファイルは 11025 か 22050Hz にダウンサンプルしておくとよいでしょう。SR は自動検出します。
amy.load_sample("flutea4.wav", preset=50) # ステレオの場合はモノラルに変換されます。preset 番号は任意です

# 任意で、ループの開始点と終了点（サンプル単位）、およびサンプルの基準 MIDI ノートを指定できます。
# WAVE ファイルのメタデータに含まれていれば自動検出します（多くのサンプルパックに含まれています）。
amy.load_sample("flutea4.wav", midinote=81, loopstart=1020, loopend=1500, preset=50)

# このプリセット番号は AMY の PCM サンプルプレーヤーで使えるようになります。
amy.send(osc=20, wave=amy.PCM, preset=50, vel=1, note=50)

# 確保済みのプリセットを解放できます:
amy.unload_sample(50) # RAM とプリセットスロットを解放します
amy.reset() # 確保済みの PCM プリセットをすべて解放します
```

Tulip Desktop や Web、あるいは AMYboard / AMYchip を I2C でハードウェア Tulip に接続している場合は、オーディオ入力も使えます。これはできたばかりの機能で、よい API を検討中です。今のところ、任意のオシレータにオーディオ入力の L または R チャンネルを流し込めます。

```python
amy.send(osc=0, wave=amy.AUDIO_IN0, vel=1)
amy.echo(1, 250, 500, 0.8) # オーディオ入力にエコーをかけます
```


Tulip CC で CV 経由で信号を送るには（ハードウェアのみ）:

```python
amy.send(osc=100, wave=amy.SAW_DOWN, freq=2.5, vel=1)
tulip.amy_set_external_channel(100, 1) # osc, channel
# external_channel = 0 - CV 出力なし。音声にルーティングされます（デフォルト）
# external_channel = 1 - 1 台目の GP8413 / dac の 1 チャンネル目
# external_channel = 2 - 1 台目の GP8413 の 2 チャンネル目
# external_channel = 3 - 2 台目の GP8413 の 1 チャンネル目
# external_channel = 4 - 2 台目の GP8413 の 2 チャンネル目

# あるいは mabeedac ライブラリで CV 信号を直接送ることもできます:
import mabeedac
mabeedac.send(volts, channel=0)
```

Tulip には独自の [`music.py`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/music.py) も同梱されており、コードから和音、進行、スケールを作れます。

```python
import music
chord = music.Chord("F:min7")
for i,note in enumerate(chord.midinotes()):
    amy.send(wave=amy.SINE,osc=i*9,note=note,vel=0.25)
```

## 音楽シーケンサ

Tulip は常に AMY のライブシーケンサを走らせており、複数の音楽プログラムが共通のクロックを共有しながら動けます。`seq = sequence.AMYSequence(length, divider)` としてから `seq.add(offset, function, args)` で AMY のシーケンスを制御できます。

AMY のシーケンスは `length` と `divider` で定義されます。`divider` は音符の長さの分母として設定します。このシーケンスをイベントのパターンにしたい場合は `length` で指定します。これはループ内でそのイベントがいくつ起きるかを表します。16 ステップで 8 分音符のドラムマシンなら、`length` が 16、`divider` が 8 です。4 分音符 8 個分のパターンなら、`length` が 8、`divider` が 4 です。

繰り返しのイベントは欲しいがパターンは不要という場合は、`length` を 1 にできます。シーケンスは指定した `divider` の音符長で単に繰り返されます。たとえば 32 分音符ごとに何かを起こしたいなら、`length` を 1、`divider` を 32 にします。

`length` を 0 にすることもでき、その場合はティックを絶対時刻で指定できます。MIDI イベントレコーダーのように、繰り返さないシーケンスに便利です。`divider` を好きな音符長にし、`length` を 0 にするだけです: `AMYSequence(0,8)`。あとは開始位置からの絶対的な音符長でイベントを追加できます。

`divider` は 1 から 192 まで設定でき、`length` は任意の数にできます。異なる divider と length を持つ複数のシーケンスを同時に走らせられます。

**AMY のシーケンサでシーケンスできるのは AMY の音楽イベント（MIDI、ノートオン、`synth`、`amy.send`、パラメータ変更）だけです。**

音楽シーケンサを使うには `seq = sequencer.AMYSequence(length, divider)` とします。その後 `seq.add(position, function, [args])` で新しいイベントを追加します。`position` はパターン内の位置（`length` が 0 なら任意の未来の位置）で、そこに `function` をスケジュールします。ドラムマシンの例では 8 分音符 16 個のパターンを組んでいるので、インデックス 0 が最初の打点、15 が最後です。最後に、その関数に渡したい引数を指定します。`synth.note_on` は 2 つ取ります（ノート番号とベロシティ）。`pan=0.1` のような他のパラメータをキーワード引数として渡すこともできます。`seq.add()` は追加されたイベントを返します。このイベントを保持しておけば、後で個々のイベントを更新・削除できます。`e = seq.add(0, func)` としておけば、`e.update(0, new_func)` で新しい関数に更新したり、`e.remove()` で削除したりできます。

### Python のシーケンサ

**任意の Python 関数を音楽シーケンサに合わせてスケジュールする**には（たとえばドラムパターンの再生に合わせて LED アニメーションを表示するよう画面を更新したい場合など）、`sequence.TulipSequence(divider)` を使えます。Tulip 全体で `TulipSequence` は最大 8 個までなので、アプリは 1 つだけ使うようにしてください。任意の Python をシーケンスしたいなら、望みの divider で `sequence_callback` を 1 つ設定します。クロックは `TulipSequence` と `AMYSequence` で共有されます。たとえばドラムマシンが `AMYSequence(16, 8)` なら、描画更新のコードには `TulipSequence(8)` を使います。8 分音符ごとに、ドラムパターンと同期して呼ばれます。

実際の使い方は [`drums`](https://github.com/shorepine/tulipcc/blob/main/tulip/shared/py/drums.py) アプリを参照してください。

Tulip のシーケンサを使うには `seq = sequence.TulipSequence(divider, func)` とします。`func` は AMY のシーケンサと同期して `divider` ごとに呼ばれます。`seq.clear()` で停止できます。

### シーケンサの例

両方のシーケンサを使う例を示します。

```python
import sequencer
syn = synth.PatchSynth(num_voices=1, patch=0) # 制御するシンセサイザーを作ります

arp_notes = [48,50,52,49,56,58,60,57]

def print_every_other_note(x):
    print("hit! %d" %(x))

music_seq= sequencer.AMYSequence(16, 8) # 8 分音符を 16 個

# 8 分音符ごとに現在のティックを表示します
print_seq= sequencer.TulipSequence(8, print_every_other_note) # 8 分音符ごと
for i in range(16):
    # インデックス i に、シンセのノートオンをパラメータ (arp_notes[i%8], 1) でスケジュールします
    music_seq.add(i, syn.note_on, [arp_notes[i%8], 1])


def stop():
    music_seq.clear() # このシーケンスからスケジュール済みのノートをすべて削除
    print_seq.clear() # このシーケンスからスケジュール済みのイベントをすべて削除
    syn.release() # シンセを停止
```

システム全体の BPM（1 分あたりの拍数、つまり 4 分音符数）は、AMY の `sequencer.tempo(120)` で設定・取得できます。

**Tulip での音楽について、さらに多くの情報は[音楽チュートリアル](music.md)を参照してください。**

## MIDI

[AMY](https://github.com/shorepine/amy) を通じて、Tulip は外部の音楽機材と接続するための MIDI 入出力をサポートしています。入ってくる MIDI メッセージに即座に反応する Python のコールバックを設定できます。MIDI 出力へメッセージを送ることもできます。

MIDI はシリアル（Tulip CC の 3.5mm コネクタ）でも、`USB-KB` コネクタを使った USB でも利用できます。なお、この USB は**ホスト**用コネクタです。USB MIDI キーボードや USB MIDI インターフェースを Tulip に接続できますが、Tulip を「USB MIDI ガジェット」としてコンピュータに直接つなぐことはできません。Tulip でコンピュータを制御したい場合は、コンピュータ側の MIDI インターフェースに Tulip の MIDI 出力を配線してください。

USB MIDI アダプタを接続している場合、Tulip からの MIDI 出力は USB と TRS の両方のコネクタに出ます。MIDI 入力は TRS と USB のどちらからでも入ってきます。

デフォルトでは、Tulip は AMY のライブ MIDI シンセサイザーモードで起動します。ノートオン、ノートオフ、プログラムチェンジ、ピッチベンドの各メッセージはポリフォニーとボイススティール付きで自動的に処理され、ユーザーが何もしなくても Tulip が音を鳴らします。

デフォルトでは、チャンネル 1 の MIDI ノートは Juno-6 のパッチ 0 に対応し、チャンネル 10 の MIDI ノートは（ドラムマシンのように）PCM サンプルを鳴らします。

どのボイスに送るかは `midi.config.add_synth(channel=channel, synth=synth)` で調整できます。たとえばチャンネル 2 で DX7 のパッチ 129 を鳴らすには、`midi.config.add_synth(channel=2, synth=synth.PatchSynth(patch=129, num_voices=1))` とします。`channel=2` は MIDI チャンネル（1〜16 のインデックスを使います）、`patch=129` は AMY のパッチ番号、`num_voices=1` はそのチャンネルとパッチでサポートしたいボイス数（ポリフォニー）です。

（目安として、Tulip CC は Juno-6 なら合計 6 ボイス程度、DX7 なら 8〜10 ボイス、PCM なら合計 20〜30 ボイス、より単純なオシレータのパッチならさらに多くを同時に扱えます。）

これらのマッピングは起動時にデフォルトへ戻ります。保存したい場合は `add_synth` のコマンドを boot.py に入れてください。

自分のプログラムで独自の MIDI コールバックを設定できます。`midi.add_callback(function)` を呼ぶと、（2 バイトまたは 3 バイトの）MIDI メッセージのリストを引数として `function` が呼ばれます。これらのコールバックは、デフォルトの MIDI コールバック（MIDI 入力でシンセの音を鳴らすもの）と並行して呼ばれます。

Tulip Desktop では、MIDI は macOS 11.0（Big Sur、2020 年リリース）以降で「IAC」MIDI バスを使って動作します（Linux と Windows ではまだまったく動きません）。これにより、同じコンピュータ上で動く任意のプログラムと Tulip の間で MIDI を送受信できます。MIDI プログラムのポート一覧に「IAC」が見当たらない場合は、Audio MIDI 設定を開き、MIDI スタジオを表示し、「IAC ドライバ」アイコンをダブルクリックして「装置はオンライン」になっているか確認してください。

macOS 版 Tulip Desktop の SYSEX 処理は、macOS 14.0（Sonoma、2023 年リリース）以降でのみ動作します。

Tulip Web では、MIDI（SYSEX を含む）は多くのブラウザで「そのまま動き」ますが、Safari では動きません。

`tulip.midi_local()` を使えば、MIDI メッセージを「ローカルに」、たとえばハードウェアの MIDI 入力を待っている実行中の Tulip プログラムに送ることもできます。

```python
def callback(m):
    if(m[0]==144):
        print("Note on, note # %d velocity # %d" % (m[1], m[2]))

midi.add_callback(callback)
midi.remove_callback(callback) # コールバックを解除します

def callback(message):
    print(message[0]) # MIDI 入力メッセージの先頭バイト

tulip.midi_out((144,60,127)) # ノートオンメッセージを送ります
# tulip.midi_out(bytes) # bytes でもリストでも送れます

tulip.midi_local((144, 60, 127)) # ローカルバスにノートオンを送ります
```

### MIDI リアルタイムクロック同期（`F8`/`FA`/`FC`）

MIDI のリアルタイム同期は**デフォルトでオフ**です。Tulip のシーケンサは独自のテンポを保ち、リアルタイムメッセージも送りません。

これは `tulip.external_midi_sync(x)` で制御します。

```python
tulip.external_midi_sync(False)     # デフォルト: 内部クロック。リアルタイムメッセージを無視し、送信もしません
tulip.external_midi_sync(True)      # 外部の MIDI リアルタイム同期に追従（Tulip がクロックスレーブ）
tulip.external_midi_sync(send=True) # MIDI リアルタイム同期を送信（Tulip がクロックマスター）
```

追従する場合（`True`、モード `1`）:
- MIDI `F8`（タイミングクロック）がシーケンサの外部テンポ同期を駆動します。
- MIDI `FA`（スタート）がシーケンサを開始します。
- MIDI `FC`（ストップ）がシーケンサを停止します。

送信する場合（`send=True`、モード `2`）:
- Tulip は自身の `tulip.seq_bpm()` のテンポから導いた 24 PPQ で `F8`（タイミングクロック）を、設定された MIDI インターフェースへ送出します。シーケンサのトランスポートが停止していてもクロックは流れ続けるので、下流の機材はテンポにロックされたままになります。
- `FA`（スタート）／`FC`（ストップ）は、シーケンサのトランスポートが開始・停止したとき（`sequencer.start()` / `sequencer.stop()` など）に送られます。

### MIDI SYSEX

Tulip は MIDI の sysex メッセージを特別に扱います。Tulip のメモリ制約のため、`tulip.midi_in()` は SYSEX メッセージを返しません。ただし AMY-over-SYSEX のために SYSEX メッセージは常に解析しており、これにより AMY のワイヤメッセージを MIDI 経由で送れます。

Tulip で MIDI の sysex メッセージを受け取って解析したい場合は、`midi.sysex_callback` を次のように設定します。

```python
def scb(message):
    print("Received sysex message of %d bytes" % (len(message)))

midi.sysex_callback = scb
```

こうすると、MIDI SYSEX メッセージが届くたびに `message` を引数としてこの関数が呼ばれます。SYSEX メッセージは一度に 16KB までに制限しています。

`sysex_callback` を設定していない場合、AMY-over-SYSEX 以外の SYSEX メッセージは解析されません。`midi_in` 関数が SYSEX メッセージを受け取ることはありません。

SYSEX メッセージを送るには、通常どおり `midi_out` を使うだけです: `tulip.midi_out([0xf0, 0x01, 0x02, 0x03, 0xf7])`。

**Tulip での音楽について、さらに多くの情報は[音楽チュートリアル](music.md)を参照してください。**

### AMY-over-MIDI SYSEX

Tulip では AMY のメッセージを MIDI 経由で送れます。これにより、MIDI 接続（USB でも UART でも）で別の AMY デバイスを制御できます。`midi.sysex_amy` を使えば、任意の AMY メッセージを MIDI SYSEX 経由に簡単にルーティングできます。

```python
amy.override_send = midi.sysex_amy
amy.reset()
amy.send(osc=0, vel=1, freq=440) # このメッセージが SYSEX 経由で送られます
```

接続されている AMY デバイス（AMYboard、Tulip、コンピュータ上の Python）はこのメッセージに反応します。



## グラフィックスシステム

Tulip の GPU は 3 つのサブシステムで構成されており、描画順は次のとおりです。
 * ビットマップグラフィックス面（BG）。画面より一回り大きく（Tulip CC では 1024+128 x 600+100、Tab5 では 1280+160 x 720+120）、x および y のスクロール速度レジスタを持ちます。図形プリミティブや UI 要素の描画は BG に対して行われます。
 * テキストフレームバッファ（TFB）。BG の上に 8x12 の固定幅テキストを 256 色で描画します。
 * TFB（さらにその下の BG）の上にあるスプライトレイヤ。スプライトレイヤは高速で、画面のクリアが不要で、スキャンラインごとに描画され、ビットマップのカラースプライトを描けます。

Tulip の GPU は、解像度とディスプレイクロックに応じた固定 FPS で動きます。ディスプレイクロックは変更できますが、1 ラインあたりのスプライトやテキストタイルの余裕は減ります。Tulip CC のデフォルトは 28MHz で、34FPS です。これはエディタや REPL といったテキスト用途において、速度と安定性のバランスが取れた値です。

ゲームやアニメーション用に、フレーム完了割り込みの Python コールバックを設定できます。


```python
# 直近 100 フレームで計算した現在の GPU 使用率を、最大に対する割合で返します
usage = tulip.gpu()

# ディスプレイクロックに基づく現在の FPS を返します
fps = tulip.fps() 

# 3 つの GPU サブシステムをすべて初期状態に戻し、BG とスプライト RAM をクリアし、TFB もクリアします。
tulip.gpu_reset()

# ディスプレイクロックを MHz 単位で取得・設定します。現在のデフォルトは 18 です。
# クロックが高いほどアニメーションは滑らかになりますが、CPU が描画準備に使える時間は減ります
clock = tulip.display_clock() 
tulip.display_clock(mhz)

# 画面の幅と高さを取得する便利関数です。
# これは tulip.timing() が返す最初の 2 値そのものです
(WIDTH, HEIGHT) = tulip.screen_size()

# ディスプレイクロックが変な状態になったら、次のようにして再起動できます
tulip.display_restart() # gpu_reset() のようにデータをクリアすることはありません

# 手動でディスプレイを停止・開始することもできます。CPU に加えて GPU のリソースも必要な
# 重い処理をしたい場合や、ディスクアクセスを速くしたい場合に便利です
tulip.display_stop() # Tulip 自体は動き続けます
tulip.display_start()

# 毎フレーム実行するフレームコールバックの Python 関数を設定します
# ゲームをより簡単に作る方法としては UIScreen の game モードを参照してください
game_data = {"frame_count": 0, "score": 0}
def game_loop(data):
    update_inputs(data)
    check_collisions(data)
    do_animation(data)
    update_score(data) # など
    data["frame_count"] += 1

tulip.frame_callback(game_loop, game_data) # 毎フレームのコールバック呼び出しを開始します
tulip.frame_callback() # コールバックを無効化します

# 画面の明るさを 1-9 で設定します（9 が最大）。デフォルトは 5 です。
tulip.brightness(5)

# 次の GPU エポック（100 フレーム）時点の GPU 使用状況（FPS、GPU に費やした時間）を stderr に表示します
tulip.gpu_log()
```

## グラフィックスの背景面

背景面（BG）は、画面に対して幅の 1/8、高さの 1/6 の余白（最低でも 128 x 100）を加えたサイズです。Tulip CC では、可視部分 1024x600 に対して 1024 + 128 x 600 + 100 になります。Tab5 では可視部分 1280x720 に対して 1280 + 160 x 720 + 120 です。（可視部分は `tulip.timing()` で変更できます。）この余白は、ダブルバッファリング、ハードウェアスクロール、後で blit するためにビットマップデータを「画面外」に置いておく用途（固定のビットマップ RAM として扱えます）に使えます。BG が最初に描画され、その上に TFB とスプライトレイヤが描かれます。

水平スクロールが届くのはこの余白までです。余白幅を超える `x_offset` を指定すると、あるラインの読み出しが面内の行末を越えて次の行に入り込み、絵が 1 行ぶんずれて見えます。背景を継ぎ目なくループさせるには、いちばん左の余白幅ぶんの列を面のいちばん右にコピーしておき、オフセットが余白幅に達したら 0 にリセットします。垂直スクロールにはこの制限はありません。`y_offset` は行全体を選ぶので、面の高さ全体できれいに折り返します。

UI の操作（LVGL や `tulip.UI` 配下のもの）も BG に描画します。BG の描画操作と LVGL を併用する場合は、互いに上書きし合う可能性があるので注意してください。

Tulip は 256 色の RGB332 を使います。パレットは次のとおりです。

![tulip_pal](https://github.com/shorepine/tulipcc/blob/main/docs/pics/rgb332.png?raw=true)


```python
# BG のピクセルを設定・取得します
pal_idx = tulip.bg_pixel(x,y)
tulip.bg_pixel(x,y,pal_idx)  # 8 ビット RGB332 モードでは pal_idx は 0-255 です

# パックされたパレット色と r,g,b を相互変換します
pal_idx = tulip.color(r,g,b)
(r,g,b) = tulip.rgb(pal_idx)

# PNG ファイルの内容を背景に設定します。
# RAM とディスク容量を節約するため、Tulip に移す前に PNG を 255 色に変換することをおすすめします
# Imagemagick なら: convert input.png -colors 255 output.png
# ディザリングありなら: convert input.png -dither FloydSteinberg -colors 255 output.png
png_file_contents = open("file.png", "rb").read()
tulip.bg_png(png_file_contents, x, y)
# PNG のファイル名を直接渡すこともできます
tulip.bg_png(png_filename, x, y)

# x,y から width,height 分のビットマップ領域を x1,y1 にコピーします
tulip.bg_blit(x,y,w,h,x1, y1)

# blit に追加のパラメータを渡すと、アルファ色（0x55）をコピーしません。BG 画像の合成に便利です
tulip.bg_blit(x,y,w,h,x1, y1, 1)

# BG の矩形をビットマップデータ（RGB332 の pal_idx）で設定・取得します
tulip.bg_bitmap(x, y, w, h, bitmap) 
bitmap = tulip.bg_bitmap(x, y, w, h)

# BG を指定色、またはデフォルトでクリアします
tulip.bg_clear(pal_idx)
tulip.bg_clear() # デフォルトを使用

# 描画プリミティブ。いずれも BG に書き込みます。
# スプライトに使いたい場合は、画面外に描いてから bg_bitmap で取り出せます。
# 図形を塗りつぶしたい場合は filled に 1 を、そうでなければ 0 を指定するか省略します
tulip.bg_line(x0,y0, x1,y1, pal_idx, [width])
tulip.bg_bezier(x0,y0, x1,y1, x2,y2, pal_idx)
tulip.bg_circle(x,y,r, pal_idx, filled) # x と y は中心
tulip.bg_roundrect(x,y, w,h, r, pal_idx, filled)
tulip.bg_rect(x,y, w,h, pal_idx, filled)
tulip.bg_triangle(x0,y0, x1,y1, x2,y2, pal_idx, filled)
tulip.bg_fill(x,y,pal_idx) # x,y から塗りつぶし（フラッドフィル）
tulip.bg_str(string, x, y, pal_idx, font) # 文字と同様ですが文字列を描きます。x と y は左下。font は 0-18 の番号
tulip.bg_str(string, x, y, pal_idx, font, w, h) # w,h の中でテキストを中央揃えにします

"""
  BG のスクロールレジスタを設定します。
  line は可視ライン番号（0-599）。
  x_offset はそのラインの x 方向オフセットのピクセル数（デフォルトは 0）
  y_offset はそのラインの y 方向オフセットのピクセル数（デフォルトはライン番号）
  x_speed は 1 フレームあたり x_offset に加算するピクセル数（デフォルトは 0）
  y_speed は 1 フレームあたり y_offset に加算するピクセル数（デフォルトは 0）

  たとえば BG を 1 フレームあたり 2 ピクセル上にスクロールするには
  for i in range(600):
    tulip.bg_scroll(i, 0, i, 0, -2) 

"""
tulip.bg_scroll(line, x_offset, y_offset, x_speed, y_speed)
tulip.bg_scroll() # デフォルトに戻します

# 個別のレジスタを変更します
tulip.bg_scroll_x_speed(line, x_speed)
tulip.bg_scroll_y_speed(line, y_speed)
tulip.bg_scroll_x_offset(line, x_offset)
tulip.bg_scroll_y_offset(line, y_offset)

# スクロールレジスタを使って、表示中の BG をその右隣のものと「入れ替え」ます
# 1 回目の swap で 1024,0 が BG の左上ピクセルになり、2 回目の swap で 0,0 に戻ります
tulip.bg_swap()
```

### フォント

Tulip には 3 種類のフォントが内蔵されています。

 - TFB フォント: TFB 用の固定サイズフォントを 5 つ同梱しています（後述）。`tulip.tfb_font()` で実行時に切り替えられます。
 - LVGL フォント: [LVGL には `lv.font_montserrat_12` などのフォントが付属しています](https://docs.lvgl.io/master/details/main-modules/font.html)。これらはグリフが多く、一部の Unicode 文字を扱え、（Tulip のランチャーで表示しているような）シンボルも含みます。
 - Tulip フォント: `bg_str` などで使うフォントを 20 種類同梱しています。`lv.tulip_font_13` のように参照すれば LVGL のウィジェットでも使えます。

フォント 19 が日本語フォントで、Tulip のフォントの中で唯一 ASCII 以外を持ちます。日本の
東雲（Shinonome）フォントを源流に持つパブリックドメインのビットマップフォント
efont Biwidth 16 で、8x16 の半角欧文と 16x16 の全角日本語が同一デザインとして
1 つのフォントに入っています。そのため和欧混在の行でも、ボックス・ベースライン・
線の太さが揃い、別々のフォントを継ぎ足したような不揃いになりません。ASCII、
ひらがな、カタカナ、そして漢字 3449 字（常用漢字と人名用漢字に少し足したもの。
JIS X 0208 の全体ではありません）を収録しています。フォントに無い文字は 〓 で
描かれます（組めなかった文字を示す日本語の慣習）ので、桁がずれません。
搭載はボードごとのコンパイル時の選択で、Tab5・Tulip Desktop・Tulip Web には
入っていますが ESP32-S3 のボードには入っておらず、そこでは `tulip.tfb_font(3)`
はエラーになります。

![IMG_3339](https://user-images.githubusercontent.com/76612/229381546-46ec4c50-4c4a-4f3a-9aec-c77d439081b2.jpeg)


## テキストフレームバッファ（TFB）

TFB は 5 つの内蔵固定幅フォントに対応しており、`tulip.tfb_font(x)` で実行時に切り替えられます。

 - `0`: デフォルトの 8x12 フォント
 - `1`: 小さい 6x8 フォント
 - `2`: 大きい 12x16 フォント
 - `3`: 日本語 16 ドット — 半角セルは 8x16、全角文字はその 2 セル分
 - `4`: 同じフォントをピクセル 2 倍化したもの — 半角セル 16x32、全角 32x32

日本語を表示できるのは `3` と `4` です。全角文字は TFB の 2 セルを占めるので、
`tulip.tfb_str(x,y)` はどちらのセルからでも同じ文字を返します。それ以外
（スクロール、ANSI コード、エディタ）は均一なセルのグリッドの上でそのまま動きます。
`4` は 32 ドットの別フォントではなく `3` を 2 倍化したものです。これにより半角と
全角の 1:2 の比率が正確に保たれます。

日本語を見るために手動でフォントを切り替える必要はありません。CP437 のフォントでは
どれも描けない文字を初めて表示しようとしたとき、コンソールは自動でフォント `3` に
切り替わります。自分で `tulip.tfb_font()` を呼ぶと、そのセッションでは以降この自動
切り替えは行われません（明示的に選んだフォントを勝手に変えることはしません）。

TFB は高速なテキスト描画のための文字プレーンです。可視の行数・列数は選択したフォントサイズによって変わります。前景・背景それぞれ 256 の ANSI カラーに対応し、書式指定もできます。TFB はテキストエディタと Python の REPL が使っています。

```python
# テキストフレームバッファ（TFB）に文字列を設定 / 文字や書式を取得します
# （フォント 0 でのデフォルトのジオメトリは 128x50。他の TFB フォントサイズでは変わります）
# format には反転（0x80）、下線（0x40）、点滅（0x20）、太字（0x10）の ANSI コードを指定します
# fg 色はパレットインデックス 0-255。bg 色も同様です
# なお REPL とエディタは TFB を使っています
tulip.tfb_str(x,y, "string", [format], [fg], [bg])
(char, format, fg, bg) = tulip.tfb_str(x,y)

# ANSI の色と書式のコードには便利関数があります
print(tulip.Colors.LIGHT_RED + "this is red " + tulip.Colors.GREEN + tulip.Colors.INVERSE + " and then green inverse")
# ANSI の書式をリセットするには
print(tulip.Colors.DEFAULT)

# Tulip の REPL は ANSI の 256 色モードにも対応しています
print(tulip.ansi_fg(56))

# TFB を停止・開始することもできます。画面の内容はメモリ上に保持され、読み書きも可能です
tulip.tfb_stop()
tulip.tfb_start()

# 既存の TFB を残しておきたい場合は、一時バッファに保存して呼び戻せます
tulip.tfb_save()
tulip.tfb_restore()

# コンソールはプリンタではなく端末としても駆動できます。ssh アプリがリモートシェルに
# 対して行うのがこれです。カーソル位置指定、スクロール領域、挿入と削除、代替画面、
# DEC 罫線素片が使えます。セッションは画面を「借りる」だけで、コンソールに出ていた
# 内容は開始時に退避され、終了時に戻ってきます。
tulip.term_start()
tulip.term_stop()

# セッションの開始・終了ではなく、タスクバーでの切り替えのように一時的に
# コンソールを明け渡すだけのときは False を渡します。2 つの画面が入れ替わり、
# 端末側はスクロール領域・各モード・代替画面を保ったまま待機します。
tulip.term_stop(False)      # コンソールを他に渡す
tulip.term_start(False)     # セッションに返す

# キーをどう符号化すべきか端末が伝えられた内容をビットマスクで返します。
# 1 アプリケーションカーソルキー、2 アプリケーションキーパッド、4 括弧付き貼り付け、
# 8 マウス報告。ssh.key_bytes(key, flags) はこれを見て Tulip のキーコードを
# 送信すべきバイト列に変換します。
flags = tulip.term_flags()

# 端末が相手に返すべき応答です（「お前は何か」「カーソルはどこか」への答え）。
# 送り先は端末には分からないので、セッションを回している側が回収して送り返します。
tulip.term_reply()

# 端末モードでは `\n` は列を保ったまま 1 行下がります。端末のラインフィードの
# 動作そのままで、前に `\r` を付けるのは相手側の pty の仕事です。端末モードでない
# ときのコンソールの挙動は従来どおりです。

# TFB のフォント番号を設定・取得します
# 0=8x12、1=6x8、2=12x16、3=日本語 16 ドット、4=日本語 16 ドットの 2 倍
# 日本語フォントを含まないビルドのボードでは 3 と 4 は ValueError になります。
tulip.tfb_font(x)
font_num = tulip.tfb_font()

# 日本語は準備不要です。表示しようとした時点でコンソールが自動でフォント 3 になります。
print("日本語と English が混在する行。ABCdefg 0123")
tulip.tfb_font(4)   # 同じフォントで 2 倍のサイズ。Tab5 では 80x22

```


## スプライト
画面上に同時に最大 32 個のビットマップスプライトを表示でき、それらを格納するビットマップデータ用に 32KB を使えます。スプライトには衝突判定が組み込まれています。
スプライトはスプライトインデックス順に描画されるので、同じピクセル領域を共有する場合、スプライトインデックス 5 はインデックス 3 の上に描かれます。

```python
# PNG ファイルのデータを、スプライト RAM のメモリ位置（0-32767）に読み込みます。
# w、h、使用バイト数を返します
# アルファがあれば使用します
(w, h, bytes) = tulip.sprite_png(png_data, mem_pos)
(w, h, bytes) = tulip.sprite_png("filename.png", mem_pos)

# メモリ上のビットマップ（RGB332 のパックされたパレットインデックス）からスプライトを読み込むこともできます
# ビットマップは自分で書いたコードから作っても、bg_bitmap で背景をサンプリングしても構いません
# 自分でスプライトを生成する場合は、アルファを表すのに pal idx 0x55 を使ってください
bytes = tulip.sprite_bitmap(bitmap, mem_pos)

# スプライトを変更したり BG にコピーしたりする必要がある場合は、スプライト RAM からビットマップデータを読めます
bitmap = tulip.sprite_bitmap(mem_pos, length)

# スプライト RAM の一部を、後で使うためのスプライトハンドルのインデックスに「登録」します。
# mem_pos から始まる w,h ピクセルのスプライトデータを参照する、スプライトハンドル #12 を作ります
tulip.sprite_register(12, mem_pos, w, h)  

# スプライトを画面に描画するようオンにします
tulip.sprite_on(12)

# オフにします
tulip.sprite_off(12)

# スプライトの x,y 位置を設定します
tulip.sprite_move(12, x, y)

# 毎フレーム、そのフレームで衝突したものの一覧を更新しています
# 衝突はスキャンラインごとに（左から右、上から下へ）評価され、
# 画面に書き込まれたピクセル（ALPHA でなく、可視である必要があります）についてのみ判定されます
# 例としては world.download("collide") を参照してください
# collisions() を呼ぶと、それまでに蓄えた衝突の記録はクリアされます。
for c in tulip.collisions():
    (a,b) = c # a と b は衝突したスプライト番号です。常に a < b になります。
    # スプライト #31 を探すことで、タッチやマウスクリックがスプライトに当たったか判定できます
    if(b==31): 
        print("Touch/click on sprite %d" % (a))

# すべてのスプライト RAM をクリアし、すべてのスプライトハンドルをリセットします
tulip.sprite_clear()
```

## スプライト用の便利クラス

スプライトは `tulip_sprite_X` 系のコマンドで扱えますが、メモリと ID の管理を任せられる便利な `Sprite` クラスも用意しています。

```python
class Bullet(tulip.Sprite):
    def __init__(self, copy_from=None):
        super().__init__(copy_from=copy_from)
        self.load("bullet.png", 32, 32)
        b.on()
        b.move_to(20,20)

b = Bullet()
b.off() # オフにする
b.on() # オンにする
b.x = 1025
b.clamp() # 画面範囲内に収める
b.move() # 最新の位置に移動する
b.move_to(x,y) # x と y を設定して移動する

b2 = Bullet(copy_from=b) # b の画像データを使いつつ、新しいスプライトハンドルを作ります
b2.move_to(25,25) # こうすれば同じ画像データのスプライトを画面上に何個も置けます
```

`Player` クラスには、キーボードでスプライトを手早く動かす方法が備わっています。

```python
p = tulip.Player(speed=5) # 1 回の移動で 5px
p.load("me.png", 32, 32)
p.joy_move() # ジョイスティックの入力に応じて位置を更新します
```

`Game` クラスと `Sprite` クラスを使った本格的な例としては、`/sys/ex/` の `planet_boing` を参照してください。

# 手伝ってもらえませんか？

私たちが考えている、協力していただけると嬉しいことです。

 * Tulip 上のスプライトエディタ
 * Tulip 上のタイル / マップエディタ

 [![shore pine sound systems discord](https://raw.githubusercontent.com/shorepine/tulipcc/main/docs/pics/shorepine100.png) **Discord で Tulip について語り合いましょう。**](https://discord.gg/TzBFkUb8pG)
