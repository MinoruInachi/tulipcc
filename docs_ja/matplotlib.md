# Tulip の matplotlib

Tulip には小さな `matplotlib` パッケージ（`matplotlib.pyplot` と
`matplotlib.colors`）が同梱されています。描画は `tulip.bg_*` プリミティブを通じて
背景レイヤに対して行われます。描き上がったプロットは画面上のただのピクセルなので、
スクリーンショットを撮る、blit する、スクロールする、上にスプライトを重ねるといった
ことが、他の描画物とまったく同じようにできます。

ファームウェアにフリーズされているため、インストール作業は不要です。

```python
from ulab import numpy as np
import matplotlib.pyplot as plt

x = np.linspace(0, 4 * np.pi, 300)
plt.plot(x, np.sin(x), label='sin')
plt.plot(x, np.cos(x), 'r--', label='cos')
plt.title('trig')
plt.xlabel('radians'); plt.ylabel('amplitude')
plt.grid(True); plt.legend()
plt.show()
```

`plt.full_screen(True)` は Tulip のテキストレイヤを隠すので、全画面のプロットが
REPL に覆われなくなります。`plt.full_screen(False)` は画面を返します。テキスト
レイヤが復帰し、REPL 自身の背景が塗り直されるため、プロットは破棄されます。
この後半部分は Tab5 で重要です。Tab5 では REPL 画面が透過で、プロットが描いた背景面
そのものが REPL の背景になっているため、テキストレイヤだけを戻すと、図の上で REPL が
読めない状態になってしまいます。他のアプリが画面を持っている場合、この呼び出しは
何もしません。テキストレイヤがオフの間もブラインドタイプは可能です。

プロットを残したまま、その上に REPL のテキストを表示したい場合は、`full_screen` を
まったく呼ばなければよいだけです。これがデフォルトの動作です。

`tulip.run('plotdemo')` を実行すると、以下で説明する内容を 4 ページで一巡できます。

このライブラリは [ulab](https://github.com/v923z/micropython-ulab) と組み合わせて
使えます。リストを受け取るすべての呼び出しは ulab の `ndarray` も受け取れるので、
配列は ulab に、描画はこちらに任せられます。

## サポートされているもの

| | |
| --- | --- |
| figure | `figure` `subplot` `subplots` `axes` `gca` `gcf` `sca` `cla` `clf` `close` |
| プロット | `plot` `step` `scatter` `bar` `barh` `hist` `boxplot` `pie` `errorbar` `fill_between` `fill_betweenx` `axhline` `axvline` `axhspan` `axvspan` `imshow` `arrow` |
| 装飾 | `title` `suptitle` `xlabel` `ylabel` `xlim` `ylim` `xticks` `yticks` `grid` `legend` `text` `annotate` `axis` `tight_layout` |
| スケール | `xscale` `yscale` `semilogx` `semilogy` `loglog` `invert_xaxis` `invert_yaxis` |
| 出力 | `show` `draw` `savefig` `pause` |
| 3D | `add_subplot(projection='3d')` のあと `scatter` `plot` `plot_wireframe` `plot_surface` `set_zlabel` `set_zlim` `view_init` `set_box_aspect` |

同じ名前は `Axes` のメソッドとしても存在するので（`ax.set_title`、`ax.plot` など）、
ステートフルなスタイルとオブジェクト指向スタイルの両方が使えます。

フォーマット文字列は本家と同じように動作します。`plot(x, y, 'r--o')` は丸マーカー付きの
赤い破線で、マーカーだけを指定してラインスタイルを指定しない場合はマーカーのみになるので、
`'ro'` は散布図になります。ラインスタイルは `-`、`--`、`:`、`-.`、マーカーは
`. , o v ^ < > s D d | _ + x *` です。

## 3D グラフ

本家と同じく `projection='3d'` で 3D の Axes になります。使える場所は
`fig.add_subplot`、`plt.subplot`、`plt.axes`、
`plt.subplots(subplot_kw={'projection': '3d'})` です。デスクトップの mplot3d の
スクリプトは、numpy の import を ulab に変えるだけで動きます。

```python
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D   # 本家と同じく、なくてもよい
from ulab import numpy as np

fig = plt.figure()
ax = fig.add_subplot(projection='3d')
ax.set_title("３D散布図", size=20)
ax.set_xlabel("x軸"); ax.set_ylabel("y軸"); ax.set_zlabel("z軸")

data = np.linspace(-3 * np.pi, 3 * np.pi, 50)
x, y = np.meshgrid(data, data)
z = np.cos(x / np.pi) * np.sin(y / np.pi)
ax.scatter(x, y, z, s=1, c="red")
plt.show()
```

この 2,500 点の散布図は Tab5 で約 1.2 秒で描けます。50×50 の `plot_surface`（四角形 2,401 個）は約 1.7 秒です。

* 使えるプロットは次の 4 つです。2D 専用のもの（`bar`、`hist`、`imshow` など）を
  3D の Axes で呼ぶと `NotImplementedError` になります。
  * `scatter(xs, ys, zs, s=, c=, cmap=, marker=, depthshade=True)`
  * `plot(xs, ys, zs, fmt)`
  * `plot_wireframe(X, Y, Z, rstride=, cstride=, rcount=50, ccount=50, color=,
    linewidth=)`
  * `plot_surface(X, Y, Z, rstride=, cstride=, rcount=50, ccount=50, color=,
    cmap=, vmin=, vmax=, shade=, facecolors=, edgecolor=)`
* `plot_surface` の四角形の色は、次の 3 通りのどれかで決まります。
  * `cmap=` を指定すると、その四角形の z の平均値からカラーマップで色を決めます。
  * `facecolors=` を指定すると、格子の 1 マスごとにその色を使います。
  * どちらもなければ 1 色（色のサイクルの次の色、または `color=`）で塗ります。
    このとき、四角形が mplot3d の既定の光源にどれだけ向いているかで陰影を
    付けます。陰影は `shade=False` で消せます。本家と同じく、カラーマップには
    陰影を付けません。
* 四角形は mplot3d と同じく奥から手前の順に塗ります。`X` と `Y` には、
  `meshgrid` に渡すはずだった 1 次元の配列をそのまま渡すこともできます。
* `meshgrid` の結果のような 2 次元の入力は、行の順に 1 次元へ平らにして扱います。
  2D の `plot` と `scatter` も同じです。
* 見た目は mplot3d の既定に合わせています。視点は `view_init(elev=30, azim=-60)`、
  箱の比率は 4:4:3（`set_box_aspect`）です。奥の 3 面には色が付いてグリッドが
  引かれ（グリッドは `ax.grid(False)` で消せます）、散布図の点は奥ほど薄く
  なります。点は奥から手前の順に描きます。
* 本家と違い、箱は Axes いっぱいに拡大して描きます。
* Tab5 では奥の面と奥行きの濃淡にフルカラーを使います。ほかの機種では他の色と
  同じくパレットを通るので、面は白になり、濃淡も数段階になります。

コードは `tulip/shared/py/mpl_toolkits/mplot3d/axes3d.py` にあります。

## 色

Tulip の背景レイヤは 1 ピクセル 1 バイトの RGB332 なので、あらゆる色は 256 個の
パレットエントリのいずれかに落とし込まれます。`matplotlib.colors.to_pal()` は
次のものを受け付けます。

```python
to_pal('r')            # 1 文字指定: b g r c m y k w
to_pal('darkgreen')    # CSS 名のうち実用的なサブセット
to_pal('C3')           # tab10 のプロパティサイクル、C0..C9
to_pal('#ff8800')      # '#rgb' と '#rrggbb'
to_pal('0.5')          # グレーレベル、0 が黒で 1 が白
to_pal((1.0, 0.5, 0))  # 0-1 の float、または 0-255 の int
to_pal(200)            # Tulip の生のパレットインデックス -- 唯一の Tulip 独自拡張
```

デフォルトサイクルの 10 色はすべて、量子化後も別々のパレットインデックスとして
残ります。プロットにとって実際に重要なのはこの性質です。青は 2 ビットしか割り当てが
ないため、赤や緑に比べて 4 倍粗く量子化されます。

`imshow` と `scatter(c=...)` で使えるカラーマップは、`viridis`、`plasma`、
`inferno`、`magma`、`gray`、`hot`、`cool`、`jet`、`coolwarm`、`spring`、
`autumn`、`winter` で、それぞれ反転版の `_r` サフィックス付きも使えます。

## rcParams

`matplotlib.rcParams` は描画時に読まれる単なる dict で、
`matplotlib.rcdefaults()` で元に戻せます。本家がポイントサイズを取るところでは、
こちらは **Tulip のフォント番号**（0〜19、`tulip.bg_str` と同じ番号体系）を取ります。
フォントはスケーラブルなアウトラインではなく、固定サイズのビットマップだからです。
キー名は本家のものを維持しているので、デスクトップのスクリプトからコピーした
`rcParams.update()` の呼び出しもそれなりに意味のある場所に着地します。

```python
matplotlib.rcParams['lines.linewidth'] = 3
matplotlib.rcParams['axes.titlesize'] = 17     # フォント 17 は logisoso24
matplotlib.rcParams['figure.facecolor'] = 'k'
matplotlib.rcParams['axes.facecolor'] = 'k'
matplotlib.rcParams['axes.edgecolor'] = 'w'
```

## 本家との違い

* **`show()` は描画してすぐ戻ります。** ブロックすべきイベントループがなく、figure を
  捨てることもしないので、`plot(); show(); plot(); show()` は積み重なっていきます。
  新しいプロットを始めるには `clf()`（または `figure()`）を呼んでください。
* **`alpha=` は受け付けますが無視されます。** 1 ピクセル 1 バイトでブレンドはありません。
* **フォントサイズは Tulip のフォント番号です**（上記のとおり）。`set_title`、
  `set_xlabel`、`set_ylabel`、`set_zlabel` は `fontsize=` / `size=` と
  `color=` を受け付けます。デスクトップのスクリプトにあるポイント指定の 20 の
  ように、フォント番号ではない `size=` は無視され、rcParams のフォントで描きます。
* **日本語を含む文字列はフォント 19 で描きます。** フォント 19 は efont の
  16 ドット日本語フォントで、指定されたフォントには関係なくこれを使います。
  フォント 0〜18 は ASCII しか持っていないためです。そのため
  `plt.title('温度')` はそのまま表示できます。ESP32-S3 のビルドにはフォント 19 が
  ないので、そこでは従来どおり指定されたフォントで描きます。
  `rcParams['font.family']` は受け付けますが無視されます。
* **`ylabel` は回転したテキストではなく、正立した文字を縦に並べた列になります。**
  Tulip のテキストレンダラは左から右にしか描けません。
* **`imshow` のデフォルトは本家の `'equal'` ではなく `aspect='auto'`**（Axes の枠を
  埋める）です。こうすることで枠と画像の位置が揃い、目盛りが表示どおりの意味を
  保ちます。本家と同じ見た目にするには `aspect='equal'` を渡してください。
* **`scatter(s=...)` はピクセル単位のマーカー直径**であり、ポイントの 2 乗による
  面積ではありません。
* **`boxplot` はアーティストの dict ではなく、統計値の dict のリストを返します**
  （`q1`、`med`、`q3`、`whislo`、`whishi`、`fliers`、`position`）。
* Agg バックエンドも、変換スタックも、内省できるアーティストツリーもありません。
  `matplotlib.use()` は受け付けますが無視されます。

## 速度

描画は Python がライン 1 セグメントごとに C プリミティブを呼ぶ形なので、
1280x720 の ESP32-P4 ではプロット 1 枚におよそ 1 秒かかります。

| | |
| --- | --- |
| 300 点のライン 3 本、グリッドと凡例 | 約 1.5 秒 |
| bar、hist、imshow、pie の 2x2 グリッド | 約 1.5 秒 |
| 512 点 FFT を波形 + 対数スペクトルとして描画 | 約 1.2 秒 |
| 48x32 のフィールドを全画面 `imshow` | 約 2.1 秒 |

`imshow` は出力先の 1 行につき `bg_bitmap` を 1 回呼び、連続する行が同じ元行から
来ている場合は行バッファを再利用します。そのため、小さな配列を拡大する処理は
ピクセル数から想像するよりずっと安く済みます。もう一方のコスト源は長いポリラインです。
再描画をインタラクティブに感じさせたい場合は、プロット前に間引いてください。

## レイアウト

マージンは推測ではなく実測です。Tulip にはテキスト幅を測る呼び出しはありませんが、
`bg_str` は描いた内容の送り幅を返します。そこでこのライブラリは、パネル右端より外側の
オフスクリーン列（スキャンアウトが決して読まないフレームバッファメモリ）に 1 文字ずつ
描画し、その幅をキャッシュしています。おかげで、メガバイト表記のプロットもパーセント
表記のプロットも、内蔵 19 フォントのどれを使っても余白が詰まった仕上がりになります。

サブプロットのグリッドは、figure を小さな隙間を空けて均等に分割します。各 `Axes` が
その実測値をもとに自分の装飾用スペースを確保するからです。`tight_layout()` は
存在しますが何もしません。直すべきものが残っていないためです。

## 保存

`savefig(name)` は figure を再描画し、figure の矩形で切り取った PNG を書き出します。

```python
plt.savefig('/user/plot.png')
```

## コードの場所

`tulip/shared/py/matplotlib/` — `__init__.py`（rcParams）、`colors.py`
（色とカラーマップの解決）、`pyplot.py`（状態機械、アーティスト、レンダラ）です。
3D の Axes は `tulip/shared/py/mpl_toolkits/mplot3d/` にあります。
ほとんどのポートは `tulip/shared/py` 全体を再帰的にフリーズするので、どちらも
自動的に取り込まれます。ESP32-P4 ボードはフリーズするファイルを個別に列挙して
いるため、`tulip/esp32p4/boards/manifest.py` の中で両方のパッケージを明示的に
指定しています。
