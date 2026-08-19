#!/bin/bash

# grab_submodules.sh

# Do the first run submodule init if needed
echo `pwd`
pushd ../../ > /dev/null
if test -f ".submodules_ok"; then
    echo "Submodules already loaded";
else
    echo "Syncing submodules for first build..."
    git submodule update --init amy
    git submodule update --init micropython
    git submodule update --init ulab
    cd micropython
    git submodule update --init lib/axtls
    git submodule update --init lib/libffi
    git submodule update --init lib/mbedtls
    git submodule update --init lib/berkeley-db-1.xx
    git submodule update --init lib/micropython-lib
    git submodule update --init lib/tinyusb
    cd ..
    touch .submodules_ok
fi

# ulab (numpy/scipy for MicroPython) arrived after .submodules_ok became a thing,
# so checkouts that were already set up would never pick it up from the block
# above. Init it whenever it is missing instead -- cheap, and idempotent.
if [ ! -f "ulab/code/micropython.cmake" ]; then
    echo "Fetching ulab submodule..."
    git submodule update --init ulab
fi

# Patch micropython/mpy-cross/Makefile for newer Apple clang tightenings.
# Run every invocation (even when .submodules_ok exists) and gate each
# individual sed on a grep so it stays idempotent. This way users whose
# submodules are already set up pick up fresh patches when they update
# macOS to a clang version that added new default warnings, without
# having to rm .submodules_ok.
#
# -Wno-gnu-folding-constant               — Apple clang tightening, earlier round
# -Wno-unknown-warning-option             — stop older clangs from erroring on
#                                           flag names they don't recognize yet
# -Wno-unterminated-string-initialization — macOS Tahoe / clang >= 19 turned the
#                                           {10,"r10"} style init into an error;
#                                           py/emitinlinethumb.c uses this
#                                           pattern intentionally (3-char array,
#                                           null byte deliberately dropped).
if [ -f micropython/mpy-cross/Makefile ]; then
    if ! grep -q -- "-Wno-gnu-folding-constant" micropython/mpy-cross/Makefile; then
        sed -i.bak 's/^CWARN += /CWARN += -Wno-gnu-folding-constant /' micropython/mpy-cross/Makefile
    fi
    if ! grep -q -- "-Wno-unterminated-string-initialization" micropython/mpy-cross/Makefile; then
        sed -i.bak 's/^CWARN += /CWARN += -Wno-unknown-warning-option -Wno-unterminated-string-initialization /' micropython/mpy-cross/Makefile
    fi
    # The seds above prepend the suppressions, but newer Apple clang (macOS Tahoe
    # / clang >= 19) re-enables -Wunterminated-string-initialization through
    # -Wextra, which the upstream CWARN line applies *after* them — so -Werror
    # still trips on py/emitinlinethumb.c. Append the suppressions to the end of
    # the Makefile so they are the last CFLAGS entries and win. Idempotent.
    if ! grep -q -- "amyboard-clang-overrides" micropython/mpy-cross/Makefile; then
        cat >> micropython/mpy-cross/Makefile <<'MK'

# amyboard-clang-overrides: keep last so they win over -Wextra (see grab_submodules.sh)
CFLAGS += -Wno-unknown-warning-option -Wno-unterminated-string-initialization -Wno-gnu-folding-constant
MK
    fi
fi

# Teach micropython's readline about UTF-8, so the REPL can accept Japanese.
#
# Upstream readline is bytes throughout: it takes only 32..126 as printable and
# hands byte counts to mp_hal_move_cursor_back() as though a byte were a column.
# Neither holds for a language where a character is three bytes and two columns,
# and the first of them is why the REPL silently dropped every Japanese character
# typed at it -- see tulip/shared/py/ime.py. The patch makes a cursor step a whole
# character and redraws a line containing non-ASCII whole rather than by byte
# arithmetic; a pure ASCII line takes the original path untouched.
#
# Gated on the TULIP_READLINE_UTF8 marker the patch introduces, so it is
# idempotent and picks itself up when the micropython pin moves.
if [ -f micropython/shared/readline/readline.c ]; then
    if ! grep -q "TULIP_READLINE_UTF8" micropython/shared/readline/readline.c; then
        echo "Patching micropython readline for UTF-8..."
        if ! (cd micropython && git apply ../tulip/shared/patches/micropython-readline-utf8.patch); then
            echo "WARNING: readline UTF-8 patch did not apply. The build will work," >&2
            echo "         but the REPL will drop Japanese input. Regenerate it with:" >&2
            echo "         cd micropython && git diff -- shared/readline/readline.c \\" >&2
            echo "             > ../tulip/shared/patches/micropython-readline-utf8.patch" >&2
        fi
    fi
fi
popd > /dev/null
