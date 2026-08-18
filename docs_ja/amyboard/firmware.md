# ファームウェアのアップグレード

AMYboard のファームウェアをアップグレードする方法は 3 つあります。いちばん簡単なのはウェブベースのアップグレーダです。

---

## 方法 1: AMYboard Online のファームウェアアップグレーダ（最も簡単）

[AMYboard Online エディタ](https://amyboard.com/editor)には、WebSerial を使ってブラウザからそのまま実行できるファームウェアアップグレーダが組み込まれています。Google Chrome の利用を推奨します。

**始める前に、シリアルポートを使っている他のものをすべて閉じてください。** ボードと通信できるプログラムは同時に 1 つだけです。**Arduino IDE**（シリアルモニタを含む）、`mpremote` や `screen` のセッション、Python の REPL、DAW やシリアルターミナルを先に終了してください。そうしないと、**Search for AMYboard** をクリックしてもブラウザが接続できません。

1. AMYboard を USB でコンピュータに接続します。
2. AMYboard の側面にある両方のボタンを押し続け、先に RST を、次に BOOT を離します。これでボードがブートローダモードに入ります。
3. ファームウェアアップグレードのページを開き、**Search for AMYboard** をクリックします。

<img src="../../docs/amyboard/img/upgrade_firmware.png" width=600>

4. AMYboard に対応するシリアルポートを選びます（"USB JTAG/serial debug unit" として表示されます）

<img src="../../docs/amyboard/img/serial_ports.png" width=400>

5. **Upgrade AMYboard firmware**（ファイルを保持）か **Fully erase and re-flash AMYboard**（まっさらな状態にする）のどちらかを選びます。
6. 処理が終わるまで待ちます。その後 RST を押して、アップグレード後のファームウェアで AMYboard を再起動する必要があります。

---

## 方法 2: シリアル経由での無線アップグレード

AMYboard がすでに動作していて、シリアルで接続できる場合は、Wi-Fi 経由でアップグレードできます。

次のいずれかで AMYboard のシリアルコンソールに接続します。

```bash
mpremote connect /dev/YOUR_SERIAL_PORT
```

または:

```bash
screen /dev/YOUR_SERIAL_PORT 115200
```

MicroPython のプロンプトで、Wi-Fi に接続してアップグレードを実行します。

```python
>>> import amyboard
>>> amyboard.wifi('your_ssid', 'your_password')
>>> amyboard.upgrade()
```

アップグレードでは、最新のファームウェアとシステムファイルを Wi-Fi 経由でダウンロードします。保存したファイルは保持されます。完了するとボードが再起動します。

---

## 方法 3: ダウンロードしたファームウェアを esptool で書き込む

AMYboard が起動しない場合や、完全に新しく書き込みたい場合は、`esptool` でフルファームウェアイメージを直接書き込めます。

1. [`amyboard` リリース](https://github.com/shorepine/tulipcc/releases/tag/amyboard)（main への push ごとに更新されるローリング AMYboard リリース）から、最新の `amyboard-full-AMYBOARD.bin` をダウンロードします。

2. AMYboard を USB で接続し、ブートローダモードにします（両方のボタンを押し、先に RST、次に BOOT を離す）。

   先に、シリアルポートを使っているものが他にないことを確認してください。Arduino IDE、`mpremote` や `screen` のセッション、シリアルモニタを閉じないと、`esptool` がポートを開けません。

3. `esptool` をまだ入れていなければインストールし、イメージを書き込みます。

```bash
pip install esptool
esptool.py write_flash 0x0 amyboard-full-AMYBOARD.bin
```

**注意: これを実行すると、保存したファイルを含め、ボード上のすべてが消去されます。**

書き込みが完了すると AMYboard が再起動するはずです（USB ケーブルの抜き差しが必要な場合があります）。この初回書き込み以降は、今後の更新に `amyboard.upgrade()` やウェブのファームウェアアップグレーダを使えます。

---

## 方法 4: ローカルでコンパイルして書き込む

AMY や AMYboard のソフトウェアを編集し、自分で変更した版を書き込みたい場合は、手元のマシンで再コンパイルできます。

1. `esp-idf` が正しくインストールされていることを確認してください。手順は [TulipCC の再書き込み](https://github.com/shorepine/tulipcc/blob/main/docs/tulip_flashing.md#compile-and-flash-tulipcc-for-esp32-s3)の説明を参照してください。

2. AMYboard を USB で接続し、ブートローダモードにします（両方のボタンを押し、先に RST、次に BOOT を離す）。

3. `tulip/amyboard` ディレクトリに移動して `idf.py flash` を実行します。AMYboard のシリアル接続を自動で見つけ、ファームウェアを再コンパイルし、AMYboard に書き込むはずです。（AMYboard がうまく見つからない場合は、`-p /dev/cu.usbmodemXXXX` のように明示的にシリアル接続を指定してみてください。）

4. AMYboard の RST を押して再起動します。これで通常どおり動作するはずです。

5. AMYboard のシリアルポートに接続すれば、MicroPython の REPL を直接操作できます（エラーメッセージの確認にも使えます）: `screen /dev/cy.usbmodemXXXX 115200`。`mpremote connect /dev/cu.usbmodemXXXX` も使えます。

6. AMYboard のファイルシステムを書き直す必要がある場合（まれで時間がかかり、保存したファイルは消えます）は、`idf.py erase-flash` の後に `python amyboard_fs_create.py full` を実行します。


[はじめかたに戻る](README.md)
