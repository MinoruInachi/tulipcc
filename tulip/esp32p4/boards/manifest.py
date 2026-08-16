# Just not _boot, we have our own
freeze("$(PORT_DIR)/modules", "apa106.py")
freeze("$(MPY_DIR)/../tulip/esp32p4/boards/TAB5", "_boot.py")
freeze("$(PORT_DIR)/modules", "inisetup.py")
freeze("$(PORT_DIR)/modules", "flashbdev.py")
require("bundle-networking")

include("$(MPY_DIR)/extmod/asyncio")

# Useful networking-related packages.
#require("mip")
require("ntptime")
#require("webrepl")

# Require some micropython-lib modules.
# require("aioespnow")
require("dht")
require("ds18x20")
require("onewire")
require("umqtt.robust")
require("umqtt.simple")

freeze("$(MPY_DIR)/../tulip/shared/py", (
	"_sx126x.py",
	"ads1115.py",
	"arpegg.py",
	"chunk.py",
	"drums.py",
	"editor.py",
	"juno6.py",
	"learn_midi_codes.py",
	"lvgl_compat.py",
	"lvgl_stub.py",
	"m58angle.py",
	"m5_8encoder.py",
	"m5adc.py",
	"m5cardkb.py",
	"m5dac.py",
	"m5dac2.py",
	"m5digiclock.py",
	"m5extend.py",
	"m5joy.py",
	"mabeedac.py",
	"midi.py",
	"music.py",
	"patches.py",
	"sequencer.py",
	"sh1107.py",
	"ssd1327.py",
	"sx1262.py",
	"sx126x.py",
	"synth.py",
	"tulip.py",
	"tulip_graphics.py",
	"tulip_queue.py",
	"tuliprequests.py",
	"ui.py",
	"umidiparser.py",
	"upysh.py",
	"utarfile.py",
	"voices.py",
	"world.py",
	"world_web.py",
	"worldui.py",
))
# AMYboard's python module, so Tulip can run AMYboard World sketches
# (world.amyboard.download). CV helpers no-op on Tulip; I2C accessories work.
freeze("$(MPY_DIR)/../tulip/shared/amyboard-py", "amyboard.py")
freeze("$(MPY_DIR)/../tulip/fs/tulip/ex", "wordpad.py")
package("amy", base_path="$(MPY_DIR)/../amy")

#freeze("$(MPY_DIR)/lib/micropython-lib/micropython/utarfile", "utarfile.py")