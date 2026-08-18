# 動作中の ESP32-S3 をデバッグする HOWTO

動作中の ESP32S3 に対して GDB を使う方法、そして動作中の ESP32S3 をプロファイリングする方法が、最近わかりました。

これらを一つにまとめた HOWTO がどこにも見つからず、動かすまでの苦労と検索結果から察するに、Espressif の外でこれをやったことのある人はごく少数のようです。そこで簡単なガイドを書くことにしました。

私たちはこれを [Tulip](https://github.com/shorepine/tulipcc) で動くコードのデバッグとプロファイリングに使っています。とても役に立ちます。

## 必要なもの

 - USB HOST のピン（19 番と 20 番）が引き出されている ESP32S3。ほとんどの開発ボードは引き出しています。Tulip では、キーボード用に使っている USB HOST の USB-C コネクタがこれにあたります。
 - できれば、ESP の UART を見るための別経路も欲しいところです。シリアルモニタを見たり、必要ならコードを操作したりするためです。Tulip では充電／シリアル用の USB ポートがこれにあたります。
 - ESP-IDF ツールチェーンがインストールされていて、使う各ターミナルで環境が設定されている（`export.sh`）こと
 - USB ケーブル 2 本。なぜか、私たちの開発ボードでは JTAG 用のケーブルは USB-C → A である必要がありました。C→C は JTAG では動きませんでしたが、シリアルでは動きます。これは私たち固有の問題かもしれませんが、頭の片隅に置いておいてください。

## セットアップ

まず、いつもどおり UART／1 本目の USB ケーブルを使ってプログラムをボードに書き込みます。デバッグ用に menuconfig の Compiler Options で `-Og` を使うと役立ちますが、必須ではありません。

1 つ目のターミナルウィンドウでシリアルモニタ `idf.py monitor` を開き、すべてが動作し操作できることを確認します。Tulip の場合は、モニタから Python コマンドを入力できる状態を指します。（デバッグ中は USB キーボードを接続できないためです。）

プログラムが動いている状態で、2 本目の USB ケーブルをコンピュータに接続します。2 つ目のターミナルウィンドウで export.sh を実行したうえで、次を実行します。

```c
% openocd -f board/esp32s3-builtin.cfg

Open On-Chip Debugger v0.12.0-esp32-20230921 (2023-09-21-13:27)
Licensed under GNU GPL v2
For bug reports, read
    http://openocd.org/doc/doxygen/bugs.html
Info : only one transport option; autoselecting 'jtag'
Info : esp_usb_jtag: VID set to 0x303a and PID to 0x1001
Info : esp_usb_jtag: capabilities descriptor set to 0x2000
Info : Listening on port 6666 for tcl connections
Info : Listening on port 4444 for telnet connections
Info : esp_usb_jtag: serial (84:FC:E6:6D:B0:D4)
Info : esp_usb_jtag: Device found. Base speed 40000KHz, div range 1 to 255
Info : clock speed 40000 kHz
Info : JTAG tap: esp32s3.cpu0 tap/device found: 0x120034e5 (mfg: 0x272 (Tensilica), part: 0x2003, ver: 0x1)
Info : JTAG tap: esp32s3.cpu1 tap/device found: 0x120034e5 (mfg: 0x272 (Tensilica), part: 0x2003, ver: 0x1)
Info : starting gdb server for esp32s3.cpu0 on 3333
Info : Listening on port 3333 for gdb connections
....
```

このターミナルウィンドウは開いたままにしておきます。

## GDB を使う

これで GDB を実行できます。クラッシュが起きたとき、ESP のデフォルトのスタックトレースより詳しい情報でトレースを調べたい場合に便利です。

次の内容の `gdbinit` というファイルを作成します。

```
target remote :3333
set remote hardware-watchpoint-limit 2
mon reset halt
maintenance flush register-cache
thb app_main
c
```

そして gdb を起動し、クラッシュをデバッグします。gdb は起動時にプログラムを一時停止するので、`continue` と入力してから手動でクラッシュを発生させます。ここではシンセのパッチ変更時に起きる AMY のクラッシュをデバッグしていたので、そのための Python コードを実行して待ちました。クラッシュが起きたらフレームを調べられます。

```c
% xtensa-esp32s3-elf-gdb -x gdbinit build/micropython.elf
...
Reading symbols from build/micropython.elf...
...
[esp32s3.cpu1] Target halted, PC=0x40382BFA, debug_reason=00000000
Thread 2 "main" hit Temporary breakpoint 1, app_main () at /Users/bwhitman/outside/tulipcc/tulip/tulipcc_r10/main.c:321
321 void app_main(void) {
(gdb) continue
Continuing.

...

Thread 9 "alles_fb_task" received signal SIGTRAP, Trace/breakpoint trap.
[Switching to Thread 1070397072]
0x42124e93 in render_lut (buf=0x3fcca67c, phase=0, step=6370381, incoming_amp=0, ending_amp=2338, lut=0x0) at /Users/bwhitman/outside/tulipcc/amy/src/oscillators.c:178
178     RENDER_LUT_PREAMBLE
(gdb) bt
#0  0x42124e93 in render_lut (buf=0x3fcca67c, phase=0, step=6370381, incoming_amp=0, ending_amp=2338, lut=0x0)
    at /Users/bwhitman/outside/tulipcc/amy/src/oscillators.c:178
#1  0x42039020 in render_sine (buf=0x3fcca67c, osc=<optimized out>) at /Users/bwhitman/outside/tulipcc/amy/src/oscillators.c:415
#2  0x4203568a in render_osc_wave (osc=3, core=0 '\000', buf=0x3fcca67c) at /Users/bwhitman/outside/tulipcc/amy/src/amy.c:979
#3  0x42035761 in amy_render (start=<optimized out>, end=<optimized out>, core=<optimized out>)
    at /Users/bwhitman/outside/tulipcc/amy/src/amy.c:998
#4  0x42032539 in esp_fill_audio_buffer_task () at /Users/bwhitman/outside/tulipcc/tulip/shared/alles.c:60
(gdb) frame 2
#2  0x4203568a in render_osc_wave (osc=3, core=0 '\000', buf=0x3fcca67c) at /Users/bwhitman/outside/tulipcc/amy/src/amy.c:979
979     if(synth[osc].wave == SINE) render_sine(buf, osc);
(gdb) print osc
$3 = 3
```

## 動作中のコードをライブでプロファイリングする

動作中のコードについて、費やされた時間の割合を示す `gprof` プロファイルを取得できます。最適化やチューニングにとても役立ちます。行うには、`openocd` を動かしたままにしておき、チップ上でプロファイル対象の処理を実行しながら、2 つ目のターミナルウィンドウで次のようにします。

```c
% telnet localhost 4444
...
Connected to localhost.
Escape character is '^]'.
Open On-Chip Debugger
> profile 10 gmon.out
Starting profiling. Halting and resuming the target as often as we can...
[esp32s3.cpu1] Target halted, PC=0x40387E09, debug_reason=00000000
Set GDB target to 'esp32s3.cpu1'
...
Profiling completed. 127 samples.
Wrote gmon.out
```

（私の Mac では、これを動かすために telnet をインストールする必要がありました。`brew install telnet` で入りました。）

`gmon.out` は、`openocd` を実行しているローカルのホストコンピュータ上に書き出されます。

次に、この `gmon.out` ファイルを `gprof` で処理する必要があります。

そのためには、`gmon.out` に加えてコンパイル済みバイナリも必要です。ESP-IDF ではコンパイル済みバイナリは `elf` ファイルで、おそらく `build/X.elf` です。この `elf` ファイルは、`gprof` に渡す前に少し加工する必要があります。コピーしたうえで、gprof が解釈できるようにセクション名 `.flash.text` を `.text` にリネームします。これは `xtensa-esp32s3-elf-objcopy` の呼び出しで行えます。

そのうえで、ESP-IDF に同梱の `gprof` を実行できます。`gprof` には大量のオプションがあり、PDF に見栄えのよい図を描くこともできますが、私はデフォルトのフラットプロファイルが気に入っています。

まとめると、`elf` ファイルを加工して Tulip をプロファイリングする手順は次のとおりです。

```bash
% cp build/micropython.elf micropython_gprof.elf # 加工が必要なので .elf をコピー
% xtensa-esp32s3-elf-objcopy -I elf32-xtensa-le --rename-section .flash.text=.text micropython_gprof.elf
% xtensa-esp32s3-elf-gprof micropython_gprof.elf gmon.out

Flat profile:

Each sample counts as 0.0833333 seconds.
  %   cumulative   self              self     total           
 time   seconds   seconds    calls  Ts/call  Ts/call  name    
 54.26      5.74     5.74                             spi_bus_lock_bg_check_dev_acq
 22.38      8.11     2.37                             spi_bus_lock_bg_clear_req
 13.39      9.53     1.42                             editor_down
  1.58      9.70     0.17                             prvAddNewTaskToReadyList
  1.58      9.86     0.17                             xt_ints_off
  1.56     10.03     0.17                             esp_restart_noos
  1.36     10.17     0.14                             spi_bus_lock_bg_check_dev_req
...
```

## USB の抜き差しと再書き込み

JTAG を動かしていると、UART のフラッシャがおかしくなることがあるようです。書き込み中に `A fatal error occurred: Serial data stream stopped: Possible serial noise or corruption.` というメッセージが出たら、JTAG ケーブルを抜いてボードをリセットし、やり直してください。おそらく、USB HOST ピンに 1 本の USB ケーブルをつなぐだけで、CDC 経由の書き込みと起動後の JTAG の両方を行えるはずです。私はこれをうまく動かせなかったので、書き込みとモニタ用に UART 接続の USB ケーブルを別に用意することをおすすめします。
