/* amy_file_hooks.inc -- AMY's external file-I/O hooks, backed by MicroPython's VFS.
 *
 * AMY calls these for `zL` (load a PCM preset from a file) and for `zT` file
 * transfers; without them pcm.c bails with "fopen hook not enabled on platform".
 * They are the reason a board can load samples off its own filesystem.
 *
 * Textually included rather than compiled on its own, for the same reason as
 * amy_message.inc: TAB5 builds modtulip_tab5.c and not amy_connector.c, and an
 * .inc costs no build-system change on any target. Exactly one translation unit
 * per build includes it.
 *
 * THREADING: every one of these reaches mp_vfs_open()/mp_stream_rw() and
 * allocates on the MicroPython heap, so they may only run on the MP thread.
 * That is a constraint on the CALLER: a board must not let AMY parse a wire
 * message off-thread once these are installed. TAB5's sysex arrives on the USB
 * host task, so it defers dispatch through parse_sysex()'s copy-slot ring (see
 * amy_midi.c, and tulip_amy_send_sysex() in modtulip_tab5.c) exactly so this
 * holds.
 */
#include "../../amy/src/transfer.h"  /* MAX_OPEN_FILES, HANDLE_INVALID */

#include "tulip_helpers.h"
// map the mp_obj_t to a file handle


static mp_obj_t *g_files[MAX_OPEN_FILES]; // index 1..MAX_OPEN_FILES-1 used

static uint32_t alloc_handle(mp_obj_t f) {
    for (uint32_t i = 1; i < MAX_OPEN_FILES; i++) {
        if (g_files[i] == NULL) {
            g_files[i] = f;
            return i;
        }
    }
    return HANDLE_INVALID; // table full
}

static mp_obj_t lookup_handle(uint32_t h) {
    if (h == 0 || h >= MAX_OPEN_FILES) return NULL;
    return g_files[h];
}

static void free_handle(uint32_t h) {
    if (h == 0 || h >= MAX_OPEN_FILES) return;
    g_files[h] = NULL;
}


#ifdef AMYBOARD_VCV
// Control-API wire paths are board-absolute (/user/current/sketch.py); the
// VCV board root is ~/Documents/AMYboard (see tulip.py root_dir()).
static const char *vcv_relocate_wire_path(const char *filename, char *buf, size_t buflen) {
    if (filename && filename[0] == '/') {
        const char *home = getenv("HOME");
        if (!home) home = getenv("USERPROFILE");  // Windows
        if (home) {
            snprintf(buf, buflen, "%s/Documents/AMYboard%s", home, filename);
            return buf;
        }
    }
    return filename;
}
#endif

uint32_t mp_fopen_hook(char * filename, const char * mode) {
#ifdef AMYBOARD_VCV
    char vcv_path[512];
    filename = (char *)vcv_relocate_wire_path(filename, vcv_path, sizeof(vcv_path));
#endif
    mp_obj_t f = tulip_fopen(filename, mode);
    if (!f) {
        return HANDLE_INVALID;
    }
    uint32_t h = alloc_handle(f);
    if (h == HANDLE_INVALID) {
        tulip_fclose(f);
        return HANDLE_INVALID;
    }
    return h;
}

uint32_t mp_fwrite_hook(uint32_t fptr, uint8_t * bytes, uint32_t len) {

    mp_obj_t f = lookup_handle(fptr);
    if (!f) {
        return 0;
    }
    uint32_t w = tulip_fwrite(f, bytes, len);
    return w;
}
#define MAX_MP_FREAD_SIZE 64
uint32_t mp_fread_hook(uint32_t fptr, uint8_t * bytes, uint32_t len) {
    mp_obj_t f = lookup_handle(fptr);
    if (!f) {
        return 0;
    }
    uint32_t total = 0;
    while (total < len) {
        uint32_t chunk = len - total;
        if (chunk > MAX_MP_FREAD_SIZE) {
            chunk = MAX_MP_FREAD_SIZE;
        }
        uint32_t r = tulip_fread(f, bytes + total, chunk);
        total += r;
        if (r < chunk) {
            break;
        }
    }
    return total;
}
void mp_fseek_hook(uint32_t fptr, uint32_t pos) {
    mp_obj_t f = lookup_handle(fptr);
    if (!f) {
        return;
    }
    (void)tulip_fseek(f, pos);
}

void mp_fclose_hook(uint32_t fptr) {
    mp_obj_t f = lookup_handle(fptr);
    if (f) {
        tulip_fclose(f);
        free_handle(fptr);
    }
}
