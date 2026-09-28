# Just not _boot, we have our own
freeze("$(MPY_DIR)/../tulip/amyboard/modules", "apa106.py")
freeze("$(MPY_DIR)/../tulip/amyboard/modules", "inisetup.py")
freeze("$(MPY_DIR)/../tulip/amyboard/modules", "espnow.py")
freeze("$(MPY_DIR)/../tulip/amyboard/modules", "flashbdev.py")

include("$(MPY_DIR)/extmod/asyncio")

# Useful networking-related packages.
#require("mip")
require("ntptime")
require("webrepl")  # WebREPL server (REPL over WiFi); _webrepl C module is enabled in mpconfigport.h

# Require some micropython-lib modules.
# require("aioespnow")
require("dht")
require("ds18x20")
require("onewire")
require("umqtt.robust")
require("umqtt.simple")

freeze("$(MPY_DIR)/../tulip/shared/py")
freeze("$(MPY_DIR)/../tulip/shared/amyboard-py")
package("amy", base_path="$(MPY_DIR)/../amy")

#freeze("$(MPY_DIR)/lib/micropython-lib/micropython/utarfile", "utarfile.py")
