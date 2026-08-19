// editor.c
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>
#include "tulip_helpers.h"
#include "display.h"
#include "polyfills.h"

#define EDITOR_COLOR_FG 255
#define EDITOR_COLOR_COMMENT 229
#define EDITOR_COLOR_STRING 249
#define EDITOR_COLOR_NUMBER 175
#define EDITOR_COLOR_OPERATOR 240
#define EDITOR_COLOR_SELECTION_BG 72
#define EDITOR_COLOR_FUNCTION 188
#define EDITOR_COLOR_BG 36

/* palette from tulip editor v1 with tulip4 pal idxes
255    {248, 248, 242}, //0 foregound, selection FG, class name, code
229    {249, 38, 114},  //1 findscope, comments
249    {230, 220, 109}, //2 monokai sampled string text
175    {175, 125, 250}, //3 monokai sampled number text
240    {253, 151, 31},  //4 deletion indication, operator
72    {73, 72, 62},    //5 selection BG
188    {166, 226, 46},  //6 function name, strings
36    {39, 40, 34},    //7 BG
*/

// shared vars for editor
char ** text_lines;
char * yank;
uint16_t lines = 0; 
uint8_t dirty = 0;
uint16_t *saved_tfb;
uint8_t *saved_tfbf;
uint8_t *saved_tfbfg;
uint8_t *saved_tfbbg;
uint16_t saved_tfb_y;
uint16_t saved_tfb_x;
uint8_t quit_flag = 0;
uint16_t y_offset = 0;
uint16_t cursor_x = 0;
// The screen column cursor_x lands on. The same number until a line holds a
// fullwidth character, at which point the byte offset and the column diverge.
uint16_t cursor_col = 0;
uint16_t cursor_y = 0;
#define EDITOR_NORMAL 0
#define EDITOR_PROMPT_CHAR 1
#define EDITOR_PROMPT_SEARCH 2
#define EDITOR_PROMPT_SAVE 3
#define EDITOR_PROMPT_READ 3
#define MAX_STRING_LEN 50 // for search string, filenames
#define EDITOR_TAB_SPACES 4
#define V_SCROLL_MARGIN 6

// The editor lays out against the console the panel is actually showing, not
// the largest one the buffer could hold. TFB_ROWS/TFB_COLS size the buffer for
// the smallest TFB font, but tfb_font selects a bigger one at runtime -- the
// Tab5 boots on 12x16, so 45 rows and 106 columns of its 60x160 buffer are on
// screen. Laying out against the buffer put the status bar and, worse, the
// scroll-down trigger fifteen rows below the bottom of the panel: the cursor
// walked off the last visible line and nothing scrolled until it had gone a
// third of a screen further. display.c and modtulip.c already ask for the
// visible geometry; this was the last place that did not.
//
// Buffer strides and allocations stay on TFB_ROWS/TFB_COLS -- only layout
// moves here.
#define EDITOR_ROWS (display_tfb_visible_rows())
#define EDITOR_COLS (display_tfb_visible_cols())
#define EDITOR_STATUS_ROW ((int16_t)EDITOR_ROWS - 1)

char prompted_string[MAX_STRING_LEN];
char current_prompt[MAX_STRING_LEN];
uint8_t prompted_count = 0;
uint8_t editor_mode = EDITOR_NORMAL;
char fn[MAX_STRING_LEN]; 
int mc=0;
int fc=0;

void dbg(const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    vfprintf(stderr, fmt, args);
    va_end(args);
}


void * editor_malloc(uint32_t size) {
    mc++;
	return malloc_caps(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
}


void editor_free(void* ptr) {
    fc++;
    free_caps(ptr);
}


// cursor_x is a byte offset into the line, because that is what every insert,
// delete and line split in this file works in. Once a line can hold Japanese
// that is no longer the same number as the screen column: a fullwidth character
// is up to three bytes and two cells. These four convert between the two, all of
// them going through display_tfb_char_cells() so the editor and the console can
// never disagree about how wide a character is.

// Screen column of the byte at index `bytes` on line s.
static uint16_t ed_col_of_byte(const char *s, uint16_t bytes) {
    uint16_t col = 0, at = 0;
    while(s[at] && at < bytes) {
        uint8_t n = 0;
        uint16_t cp = 0;
        col += display_tfb_char_cells(s + at, &n, &cp);
        at += n ? n : 1;
    }
    return col;
}

// Byte index of the character start at or after byte index `bytes`. Snapping to a
// boundary matters after a vertical move, which carries a byte offset from a line
// that may have split its characters up differently.
static uint16_t ed_snap_byte(const char *s, uint16_t bytes) {
    uint16_t at = 0;
    uint16_t len = strlen(s);
    if(bytes >= len) return len;
    while(at < len) {
        uint8_t n = 0;
        uint16_t cp = 0;
        display_tfb_char_cells(s + at, &n, &cp);
        if(n == 0) n = 1;
        if(at + n > bytes) return at;
        at += n;
    }
    return len;
}

// Byte index one character forward / back from `bytes`.
static uint16_t ed_next_byte(const char *s, uint16_t bytes) {
    uint16_t len = strlen(s);
    if(bytes >= len) return len;
    uint8_t n = 0;
    uint16_t cp = 0;
    display_tfb_char_cells(s + bytes, &n, &cp);
    if(n == 0) n = 1;
    return (bytes + n > len) ? len : bytes + n;
}

static uint16_t ed_prev_byte(const char *s, uint16_t bytes) {
    if(bytes == 0) return 0;
    uint16_t at = 0, prev = 0;
    while(at < bytes) {
        prev = at;
        uint8_t n = 0;
        uint16_t cp = 0;
        display_tfb_char_cells(s + at, &n, &cp);
        at += n ? n : 1;
    }
    return prev;
}

// Colour a character's cells. A fullwidth one is two, and colouring only the
// left half leaves the right half in whatever colour the previous line left it.
static void ed_paint_fg(uint16_t y, uint16_t col, uint8_t cells, uint8_t color) {
    for(uint8_t c=0;c<cells;c++) {
        if(col + c < TFB_COLS) TFBfg[y*TFB_COLS+col+c] = color;
    }
}

void editor_highlight_at_row(uint16_t y) {
    // {"False", "None", "True", "and", "as", "assert", "break", "class", "continue", "def", 
    //  "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", 
    //  "not", "or", "pass", "raise", "return", "try", "while", "with", "yield"};
    const char operators[]= ":;-/=+-()[]{}\'\".,\\|!@#$%^&*<>?"; // this + space is delims

    uint8_t state = 0;
    // Walk bytes but colour cells: `i` indexes the line, `col` the screen, and a
    // fullwidth character advances them by different amounts.
    const char *line = text_lines[y+y_offset];
    uint16_t col = 0;
    // TODO: keywords, function calls a = dog(), etc, kwargs?
    for(uint16_t i=0;i<strlen(line);) {
        uint8_t operator_hit = 0;
        char c = line[i];
        uint8_t char_bytes = 0;
        uint16_t char_cp = 0;
        uint8_t char_cells = display_tfb_char_cells(line + i, &char_bytes, &char_cp);
        if(char_bytes == 0) char_bytes = 1;
        if(col + char_cells > TFB_COLS) break;
        // Everything below writes TFBfg at `i`; point that at the cell instead.
        const uint16_t cell = col;
        //dbg("char %c state %d row %d i %d\n", c, state, y, i);
        if(!state) { 
            for(uint8_t j=0;j<strlen(operators);j++) {
                if(c==operators[j]) {
                    operator_hit = 1;
                }
            }
            if(c==34 || c==39) {
                state = 1;
                ed_paint_fg(y, cell, char_cells, EDITOR_COLOR_STRING);
            } else if (c==39) {
                state = 2;
                ed_paint_fg(y, cell, char_cells, EDITOR_COLOR_STRING);
            } else if(c==35) {
                state = 3;
                ed_paint_fg(y, cell, char_cells, EDITOR_COLOR_COMMENT);
            } else if(c>='0' && c<='9') {
                ed_paint_fg(y, cell, char_cells, EDITOR_COLOR_NUMBER);
            } else if(operator_hit) {
                ed_paint_fg(y, cell, char_cells, EDITOR_COLOR_OPERATOR);
            } else {
                ed_paint_fg(y, cell, char_cells, EDITOR_COLOR_FG);
            }
        } else {
            // We are in a state
            if(state == 1) {
                if(c==34) state = 0;
                ed_paint_fg(y, cell, char_cells, EDITOR_COLOR_STRING);
            } else if(state ==2) {
                if(c==39) state = 0;
                ed_paint_fg(y, cell, char_cells, EDITOR_COLOR_STRING);
            } else if(state == 3) {
                ed_paint_fg(y, cell, char_cells, EDITOR_COLOR_COMMENT);
            } 
        }
        i += char_bytes;
        col += char_cells;
    }
}

void clear_row(uint16_t y) {
	if(y < TFB_ROWS) {
		for(uint16_t i=0;i<TFB_COLS;i++) {
			TFB[y*TFB_COLS+i] = 0;
		}	
	}
}
void format_at_row(uint8_t format, int16_t len, uint16_t y) {
	if(y<TFB_ROWS) {
		if(len <0) len = TFB_COLS;
		for(uint16_t i=0;i<len;i++) {
			TFBf[y*TFB_COLS+i] = format;
			TFBfg[y*TFB_COLS+i] = EDITOR_COLOR_FG;
			TFBbg[y*TFB_COLS+i] = EDITOR_COLOR_BG;
            // Fill in spaces if not given, for screen-wide banners
            if(TFB[y*TFB_COLS+i]==0) TFB[y*TFB_COLS+i] = 32;
		}
	}
}
void string_at_row(char * s, int16_t len, uint16_t y) {
    //dbg("string at row ###%s### len %d y %d\n", s, len, y);
    if(s!=NULL) {
    	if(len < 0) len=strlen(s);
    	if(y<TFB_ROWS) {
            // len is a byte count from every caller (strlen, or a status line's
            // own length), and the cells it fills is a different number once the
            // line holds Japanese. Place characters, then clear from wherever the
            // last one actually ended.
            char saved = 0;
            uint8_t truncated = (len < (int16_t)strlen(s));
            if(truncated) { saved = s[len]; s[len] = 0; }
            uint16_t cells = display_tfb_place_str(s, 0, y);
            if(truncated) s[len] = saved;
    		for(uint16_t i=0;i<cells;i++) {
    			TFBf[y*TFB_COLS+i] = 0; ;
    			TFBfg[y*TFB_COLS+i] = EDITOR_COLOR_FG;
    			TFBbg[y*TFB_COLS+i] = EDITOR_COLOR_BG;
    		}
    		for(uint16_t i=cells;i<TFB_COLS;i++) {
    			TFB[y*TFB_COLS+i] = 0;
    		}
            if(y!=EDITOR_STATUS_ROW)editor_highlight_at_row(y);
    	}
    }
}


// (Re) paints the entire TFB
void paint_tfb(uint16_t start_at_y) {
    const int16_t status_row = EDITOR_STATUS_ROW;
    for(int16_t y=start_at_y;y<status_row;y++) {
        if(y_offset + y < lines) { 
	       string_at_row(text_lines[y_offset+y], -1, y);
        } else {
            clear_row(y);
        }
    }
    display_tfb_update(-1);
}



// Move the cursor to pos x,y and scroll the viewport if needed
void move_cursor(int16_t x, int16_t y) {
	// Undo old cursor. Indexed by column, not by cursor_x: the cursor is on the
	// screen and cursor_x counts bytes into the line.
	TFBf[cursor_y*TFB_COLS+cursor_col] = 0; 
    display_tfb_update(cursor_y);

	// Move viewport up/down half a screen of visible rows
	// Move cursor to next/prev line (which would now be in the middle of the screen)
	const int16_t status_row = EDITOR_STATUS_ROW;
	if(y < 0) {
		uint16_t cursor_y_line_was = cursor_y + y_offset;
		if(y_offset > (status_row/2)) {
			y_offset = y_offset - status_row/2;
		} else {
			y_offset = 0;
		}
		cursor_y = (cursor_y_line_was-1) - y_offset;
		paint_tfb(0);
	// >= rather than ==: page down and search both hand this a row that can
	// overshoot, and landing in the else below would park the cursor off the
	// bottom of the panel (and, past the buffer, in the next row's memory).
	} else if(y >= status_row) {
		uint16_t cursor_y_line_was = cursor_y + y_offset;
		y_offset = y_offset + status_row/2;
		cursor_y = (cursor_y_line_was+1) - y_offset;
		paint_tfb(0);
	} else {
		cursor_y = y;
	}
	// X scrolling TODO or NI, not sure yet
	if(x < 0) {
		dbg("NYI scroll left\n");
	} else {
		// x is a byte offset into the line the cursor is now on -- which, after a
		// vertical move, is not the line it was on when the caller worked x out.
		// cursor_y is already updated above, so this reads the right one.
		const char *line = (cursor_y + y_offset < lines) ? text_lines[cursor_y + y_offset] : "";
		uint16_t col = ed_col_of_byte(line, (uint16_t)x);
		if(col >= EDITOR_COLS) {
			// Still no horizontal scrolling, but stopping at the last visible
			// column beats parking the cursor where it cannot be seen -- and a
			// tab jump past TFB_COLS used to write into the next row's buffer.
			dbg("NYI scroll right %d %d\n", x, y);
		} else {
			cursor_x = x;
			cursor_col = col;
		}
	}
	// Put in new cursor 
    TFBf[cursor_y*TFB_COLS+cursor_col] = FORMAT_INVERSE|FORMAT_FLASH;

    if(TFB[cursor_y*TFB_COLS+cursor_col]==0) TFB[cursor_y*TFB_COLS+cursor_col] = 32;

    display_tfb_update(y);

	// Update status bar
	char status[TFB_COLS];
	// TODO, padding better 
	float percent = ((float)(cursor_y+y_offset+1) / (float)lines) * 100.0;
    char dirty_char = ' ';
    if(dirty) dirty_char = '*';
    #ifdef TDECK
    // Smaller screen, less space for text
	sprintf(status, "%04d / %04d [%02.2f%%] %3d %.10s %c", cursor_y+y_offset+1, lines,  percent, cursor_col, fn, dirty_char);
    #else
    sprintf(status, "%04d / %04d [%02.2f%%] %3d %.35s %c", cursor_y+y_offset+1, lines,  percent, cursor_col, fn, dirty_char);
    #endif    
	string_at_row(status, strlen(status), status_row);
	format_at_row(FORMAT_INVERSE, -1, status_row);
    display_tfb_update(status_row);


}

void editor_page_up() {
	if(y_offset > (EDITOR_ROWS-V_SCROLL_MARGIN)) {
		y_offset = y_offset - (EDITOR_ROWS-V_SCROLL_MARGIN);
	} else {
		y_offset = 0;
	}
	paint_tfb(0);
	move_cursor(cursor_x, 0);
}

void editor_page_down() {
	if(y_offset + (EDITOR_ROWS-V_SCROLL_MARGIN) < lines) {
		y_offset = y_offset + (EDITOR_ROWS-V_SCROLL_MARGIN);
		move_cursor(cursor_x, 0);
	} else {
		move_cursor(cursor_x, lines-y_offset);
	}
	paint_tfb(0);

}

void save_tfb() {
	// A codepoint per cell, not a byte: saving it into a uint8_t buffer would
	// truncate every Japanese character on screen to its low byte.
	saved_tfb = (uint16_t*)editor_malloc(TFB_ROWS*TFB_COLS*sizeof(uint16_t));
	saved_tfbf= (uint8_t*)editor_malloc(TFB_ROWS*TFB_COLS);
	saved_tfbfg= (uint8_t*)editor_malloc(TFB_ROWS*TFB_COLS);
	saved_tfbbg= (uint8_t*)editor_malloc(TFB_ROWS*TFB_COLS);
	for(uint16_t y=0;y<TFB_ROWS*TFB_COLS;y++) {
		saved_tfb[y] = TFB[y];
		saved_tfbf[y]= TFBf[y];
		saved_tfbfg[y]= TFBfg[y];
		saved_tfbbg[y]= TFBbg[y];
		TFB[y] = 0;
		TFBf[y] = 0;
		TFBfg[y] = EDITOR_COLOR_FG;
		TFBbg[y] = EDITOR_COLOR_BG;
	}
	saved_tfb_y = tfb_y_row;
	saved_tfb_x = tfb_x_col;
	tfb_y_row = 0;
	tfb_x_col = 0;
    for(uint16_t y=0;y<V_RES+OFFSCREEN_Y_PX;y++) {
        for(uint16_t x=0;x<H_RES+OFFSCREEN_X_PX;x++) {
            display_set_bg_pixel_pal(x,y,EDITOR_COLOR_BG);
        }
    }
}

void restore_tfb() {
	for(uint16_t y=0;y<TFB_ROWS*TFB_COLS;y++) {
		TFB[y] = saved_tfb[y];
		TFBf[y] = saved_tfbf[y];
		TFBfg[y] = saved_tfbfg[y];
		TFBbg[y] = saved_tfbbg[y];
        //if(y%TFB_COLS==0) delay_ms(1);
	}
	editor_free(saved_tfb);
	editor_free(saved_tfbf);
	editor_free(saved_tfbfg);
	editor_free(saved_tfbbg);
	tfb_y_row = saved_tfb_y;
	tfb_x_col = saved_tfb_x;
    for(uint16_t y=0;y<V_RES+OFFSCREEN_Y_PX;y++) {
        for(uint16_t x=0;x<H_RES+OFFSCREEN_X_PX;x++) {
            display_set_bg_pixel_pal(x,y,bg_pal_color);
        }
    }
    display_tfb_update(-1);
}

void editor_new_file() {
    text_lines = (char**)editor_malloc(sizeof(char**)*1);
    text_lines[0] = (char*)editor_malloc(sizeof(char)*1);
    text_lines[0][0]=0;
    lines=1;
}


void editor_open_file(const char *filename) {
    // A file may already be loaded, if so, insert the lines where we are
	uint16_t c = 0;
    uint16_t new_lines = 0;
	//dbg("opening file %s\n", filename);
	int32_t fs = file_size(filename);
	if(fs > 0) {
		char * text = (char*) editor_malloc(fs+3); 
		uint32_t bytes_read = read_file(filename, (uint8_t*)text, fs, 0);
        //dbg("Filesize %d bytes read %d\n", fs, bytes_read);
        new_lines = 1;
        //if(text[bytes_read-1]!='\n') { new_lines = 0; dbg("adding n\n"); text[bytes_read] = '\n'; bytes_read++; }
		text[bytes_read] = 0; // end

		// #580: expand tab characters to spaces so files saved with tabs (e.g.
		// from the web editors) are editable here. The editor stores indentation
		// as spaces (Tab inserts EDITOR_TAB_SPACES spaces), so a literal tab
		// would otherwise show as a single un-editable character.
		uint32_t tab_count = 0;
		for(uint32_t i=0;i<bytes_read;i++) if(text[i]=='\t') tab_count++;
		if(tab_count) {
			uint32_t expanded_len = bytes_read + tab_count*(EDITOR_TAB_SPACES-1);
			char * expanded = (char*) editor_malloc(expanded_len+3);
			uint32_t j = 0;
			for(uint32_t i=0;i<bytes_read;i++) {
				if(text[i]=='\t') {
					for(uint8_t s=0;s<EDITOR_TAB_SPACES;s++) expanded[j++] = ' ';
				} else {
					expanded[j++] = text[i];
				}
			}
			expanded[j] = 0;
			editor_free(text);
			text = expanded;
			bytes_read = j;
		}
		for(uint32_t i=0;i<bytes_read;i++) if(text[i]=='\n') new_lines++;
		//dbg("File %s has %d lines. %d bytes\n", filename, new_lines,bytes_read);
		char ** incoming_text_lines = (char**)editor_malloc(sizeof(char*)*(new_lines));

		uint32_t last = 0;
		for(uint32_t i=0;i<bytes_read+1;i++) {
			if(text[i]=='\n' || i==bytes_read) {
                //dbg("mallocing line %d\n", c);
				incoming_text_lines[c]  = editor_malloc(i-last + 1);
				uint16_t x;
				for(x=0;x<i-last;x++) { 
					incoming_text_lines[c][x] = text[last+x];
				}
				incoming_text_lines[c][x] = 0;
				last = i+1;
				c++;
			}
		}
        if(c < new_lines) {
            //dbg("allocing last line %d\n", c);
            incoming_text_lines[c] = editor_malloc(1);
            incoming_text_lines[c][0] = 0;
        }
		editor_free(text);

		//dbg("File %s read with %d lines. Inserting at position %d. existing lines %d\n", filename, new_lines, y_offset+cursor_y, lines);

        // Now insert the text lines at cursor_y+y_offset
        char ** combined_text_lines = (char**)editor_malloc(sizeof(char*)*(new_lines+lines));
        uint16_t line = 0;
        for(uint16_t y=0;y<cursor_y+y_offset;y++) {
            combined_text_lines[line++] = text_lines[y];
        }
        for(uint16_t y=0;y<new_lines;y++) {
            combined_text_lines[line++] = incoming_text_lines[y];
        }
        for(uint16_t y=cursor_y+y_offset;y<lines;y++) {
            combined_text_lines[line++] = text_lines[y];
        }
        if(lines) { 
            editor_free(text_lines);
        }
        editor_free(incoming_text_lines);
        text_lines = combined_text_lines;
        //dbg("Setting lines to %d, was %d\n", lines+new_lines, lines);
        lines = lines + new_lines;
	} else {
        //dbg("File doesn't exist\n");
        editor_new_file();
    }

	paint_tfb(0);
}

void prompt_for_string(char * prompt,  uint8_t mode) {
    prompted_count = 0;
    prompted_string[0] = 0;
    strcpy(current_prompt, prompt);
    string_at_row(prompt, -1, EDITOR_STATUS_ROW);
    format_at_row(FORMAT_INVERSE, -1, EDITOR_STATUS_ROW);
    paint_tfb(EDITOR_STATUS_ROW);
    display_tfb_update(EDITOR_STATUS_ROW);
    editor_mode=mode;
}


void editor_save() {
    if(strlen(fn)) {
        uint32_t bytes = 0;
        for(uint16_t i=0;i<lines;i++) { if(text_lines[i] != NULL) bytes = bytes + strlen(text_lines[i]) + 1; }
        char * text = (char*)editor_malloc(bytes);
        uint32_t c = 0;
        for(uint16_t i=0;i<lines;i++) { 
            if(text_lines[i]!=NULL) {
                if(text_lines[i][0] != 0) {
                    for(uint16_t j=0;j<strlen(text_lines[i]);j++) {
                        text[c++] = text_lines[i][j];
                    }
                    text[c++] = '\n';
                } else {
                    text[c++] = '\n';
                }
            }
        }
        //display_stop();
        write_file(fn, (uint8_t*)text, c, 0);
        //display_start();
        dirty = 0;
        editor_free(text);
        //dbg("Saved %s\n", fn);
        move_cursor(cursor_x, cursor_y);
    } else {
        dbg("No filename given, not saving\n");
    }
}



void editor_insert_character(int c) {
	dirty = 1;
	char * source_line = text_lines[cursor_y + y_offset];
	char * dest_line = (char*)editor_malloc(strlen(source_line) + 2);
	for(uint16_t i=0;i<cursor_x;i++) {
		dest_line[i] = source_line[i];
	}
	dest_line[cursor_x] = (uint8_t) c;
	for(uint16_t i=cursor_x+1;i<strlen(source_line)+1;i++) {
		dest_line[i] = source_line[i-1];
	}
	dest_line[strlen(source_line)+1] = 0;
    editor_free(source_line); 
	text_lines[cursor_y + y_offset] = dest_line;
	string_at_row(dest_line, -1, cursor_y);
	move_cursor(cursor_x+1, cursor_y);
}

// Insert a whole UTF-8 string at the cursor. editor_insert_character() takes one
// byte and is what the keyboard drives; the IME commits several bytes at once --
// 「日本語」 is nine of them and three characters -- and going through the
// per-byte path would leave the line mid-sequence between calls and repaint it
// three times.
void editor_insert_string(const char *text) {
    if(text == NULL || text[0] == 0) return;
    // This is reachable from Python as tulip.editor_insert(), so it has to be
    // safe when no editor is running rather than trusting the caller.
    if(text_lines == NULL || lines == 0 || (uint16_t)(cursor_y + y_offset) >= lines) return;
    dirty = 1;
    char *source_line = text_lines[cursor_y + y_offset];
    uint16_t source_len = strlen(source_line);
    uint16_t add = strlen(text);
    char *dest_line = (char*)editor_malloc(source_len + add + 1);
    memcpy(dest_line, source_line, cursor_x);
    memcpy(dest_line + cursor_x, text, add);
    memcpy(dest_line + cursor_x + add, source_line + cursor_x, source_len - cursor_x);
    dest_line[source_len + add] = 0;
    editor_free(source_line);
    text_lines[cursor_y + y_offset] = dest_line;
    string_at_row(dest_line, -1, cursor_y);
    move_cursor(cursor_x + add, cursor_y);
}

void editor_linestart() {
	move_cursor(0, cursor_y);
}

void editor_lineend() {
	move_cursor(strlen(text_lines[cursor_y + y_offset]), cursor_y);
}

void editor_backspace() {
	dirty = 1;
	char* cur_line = text_lines[cursor_y + y_offset];
	if(cursor_x > 0) {
        // Check if there's a tab / N spaces before us
        uint8_t no_tab = 1;
        uint16_t space_count = 0;
        if(cursor_x>=EDITOR_TAB_SPACES) {
            no_tab = 0;
            for(int16_t i=cursor_x-1;i>=0;i--) {
                if(cur_line[i] != 32) { no_tab = 1; } else { space_count++; }
            }
        }
        if(!no_tab && (space_count % EDITOR_TAB_SPACES == 0)) {
            // delete N characters
            for(uint16_t i=cursor_x-EDITOR_TAB_SPACES;i<strlen(cur_line);i++) {
                cur_line[i] = cur_line[i + EDITOR_TAB_SPACES];
            }
            string_at_row(cur_line, -1, cursor_y);
            move_cursor(cursor_x-EDITOR_TAB_SPACES, cursor_y);
        } else {
            // A whole character, which is up to three bytes of UTF-8. Removing
            // one byte of a Japanese character would leave the rest of it behind
            // as an invalid sequence.
            uint16_t prev = ed_prev_byte(cur_line, cursor_x);
            uint16_t gone = cursor_x - prev;
            uint16_t line_len = strlen(cur_line);
    		for(uint16_t i=prev;i+gone<=line_len;i++) {
	       		cur_line[i] = cur_line[i + gone];
    		}
            string_at_row(cur_line, -1, cursor_y);
            move_cursor(prev, cursor_y);
        }
	} else {
		// hard mode, move up
		if(cursor_y + y_offset > 0) {
			// we will have 1 less line when this is done
			char *above_line = text_lines[cursor_y+y_offset-1];
            char *old_line = text_lines[cursor_y+y_offset];
			char *new_line = (char*)editor_malloc(strlen(above_line) + strlen(cur_line) + 1);
			uint16_t c = 0;
			for(uint16_t i=0;i<strlen(above_line);i++) new_line[c++] = above_line[i];
			uint16_t split = c;
			for(uint16_t i=0;i<strlen(cur_line);i++) new_line[c++] = cur_line[i];
			new_line[c] = 0;
			editor_free(above_line);
			text_lines[cursor_y+y_offset-1] = new_line;
			lines--;


            char** dest_text_lines = (char**)editor_malloc(sizeof(char*) * (lines));
            for(uint16_t i=0;i<cursor_y+y_offset;i++) {
                dest_text_lines[i] = text_lines[i];
            }
			for(uint16_t i=cursor_y+y_offset;i<lines;i++) {
				dest_text_lines[i] = text_lines[i+1];
			}
            editor_free(text_lines);
            editor_free(old_line);
            text_lines = dest_text_lines;
			paint_tfb(cursor_y-1);

			// Move the cursor up at the split
			move_cursor(split, cursor_y-1);

		} // else do nothing, we're at the top of the file
	}
}

void editor_tab() {
	for(uint8_t i=0;i<EDITOR_TAB_SPACES;i++) editor_insert_character(' ');
}

void editor_crlf() {
	dirty = 1;
	char* cur_line = text_lines[cursor_y + y_offset];
	char** dest_text_lines = (char**)editor_malloc(sizeof(char*) * (lines + 1));
	for(uint16_t i=0;i<cursor_y+y_offset+1;i++) {
		dest_text_lines[i] = text_lines[i];
	}
    // Count tabs at the beginning of this line and add them to the line below
    uint16_t space_count = 0;
    for(uint16_t i=0;i<strlen(cur_line);i++) {
        if(cur_line[i]==32) {
            space_count++;
        } else {
            i = strlen(cur_line) + 1; // end loop
        }
    }
    uint16_t tab_count = space_count / EDITOR_TAB_SPACES;

	if(cursor_x == strlen(cur_line)) {
		// We are at the end of the line, so just make an empty line underneath and move on
		dest_text_lines[cursor_y+y_offset+1] = (char*)editor_malloc(1+(tab_count*EDITOR_TAB_SPACES));
        uint16_t c =0;
        for(uint16_t i=0;i<tab_count;i++) {
            for(uint16_t j=0;j<EDITOR_TAB_SPACES;j++) {
                dest_text_lines[cursor_y+y_offset+1][c++] = 32; 
            }
        }
		dest_text_lines[cursor_y+y_offset+1][c] = 0;
	} else {
		// We are somewhere in the middle of the line, so split and copy
		// Get the text on cur line that has to go below
		uint16_t chars_to_copy = strlen(cur_line) - cursor_x;
		dest_text_lines[cursor_y+y_offset+1] = (char*)editor_malloc(chars_to_copy+1+(tab_count*EDITOR_TAB_SPACES));
		uint16_t i;
        uint16_t c =0;
        for(uint16_t i=0;i<tab_count;i++) {
            for(uint16_t j=0;j<EDITOR_TAB_SPACES;j++) {
                dest_text_lines[cursor_y+y_offset+1][c++] = 32; 
            }
        }        
		for(i=0;i<chars_to_copy;i++) {
			dest_text_lines[cursor_y+y_offset+1][c++] = dest_text_lines[cursor_y+y_offset][cursor_x+i];
		}
		dest_text_lines[cursor_y+y_offset+1][c] = 0;
		// Chop the old line, no need to realloc, i think we're ok
		dest_text_lines[cursor_y+y_offset][cursor_x] = 0;
	}
	// Copy the remaining lines below that were not impacted
	for(uint16_t i=cursor_y+y_offset+2;i<lines+1;i++) {
		dest_text_lines[i] = text_lines[i-1];
	}
	lines++;
	editor_free(text_lines);		
	text_lines = dest_text_lines;

	// Redraw everything from the split (going down, as scroll)
	paint_tfb(cursor_y);
	// Update cursor
	move_cursor(tab_count*EDITOR_TAB_SPACES, cursor_y +1);
}




void editor_up() {
    if(cursor_y+y_offset > 0) {
        // ed_snap_byte() also clamps to the end of the destination line, which is
        // what the two-branch version here was for.
        const char *dest = text_lines[cursor_y-1 + y_offset];
        move_cursor(ed_snap_byte(dest, cursor_x), cursor_y-1);
    }
}
void editor_down() {
    if(cursor_y + y_offset < lines-1) {
        const char *dest = text_lines[cursor_y+1+y_offset];
        move_cursor(ed_snap_byte(dest, cursor_x), cursor_y+1);
    } 
}

void editor_right() {
    // easiest, just go right
    uint8_t tab = 0;
    if(cursor_x < strlen(text_lines[cursor_y + y_offset])) {
        // count spaces ahead , see there is EDITOR_TAB_SPACES in a row, move that many instead
        if(cursor_x + EDITOR_TAB_SPACES < (uint16_t)strlen(text_lines[cursor_y + y_offset])) {
            tab = 1;
            for(uint16_t i=cursor_x+1;i<cursor_x+EDITOR_TAB_SPACES;i++) {
                if(text_lines[cursor_y+y_offset][i] != 32) tab = 0;
            }
        }
        if(tab) {
            move_cursor(cursor_x + EDITOR_TAB_SPACES, cursor_y);
        } else {
            move_cursor(ed_next_byte(text_lines[cursor_y + y_offset], cursor_x), cursor_y);
        }
    } else {
        editor_down();
        editor_linestart();
    }
}
void editor_left() {
    // Easiest, if x is not zero, just move left
    uint8_t tab = 0;
    if(cursor_x > 0) {
        // count spaces behind , see there is EDITOR_TAB_SPACES in a row, move that many instead
        if(cursor_x - EDITOR_TAB_SPACES >= 0) {
            tab = 1;
            for(int16_t i=cursor_x-1;i>=cursor_x-EDITOR_TAB_SPACES;i--) {
                if(text_lines[cursor_y+y_offset][i] != 32) tab = 0;
            }
        }
        if(tab) {
            move_cursor(cursor_x - EDITOR_TAB_SPACES, cursor_y);
        } else {
            move_cursor(ed_prev_byte(text_lines[cursor_y + y_offset], cursor_x), cursor_y);
        }
    } else {
        // If x is at the left, go up a line
        editor_up();
        // And move x cursor to the end of line
        editor_lineend();
    }
}


void editor_yank() {
    char * yanked_line = text_lines[cursor_y+y_offset];
    if(strlen(yanked_line)) {
        dirty = 1;
    	if(yank) editor_free(yank);
    	yank = (char*)editor_malloc(strlen(yanked_line)+1);
    	for(uint16_t i=0;i<strlen(yanked_line);i++) yank[i] = yanked_line[i];
    	yank[strlen(yanked_line)] = 0;
    	// Now remove this line from the text_lines
    	for(uint16_t i=cursor_y+y_offset;i<lines-1;i++) {
    		text_lines[i] = text_lines[i+1];
    	}
    	lines--;
    	// Got a crash here when yanking twice without an unyank
    	editor_free(yanked_line);
    	paint_tfb(cursor_y);
    	move_cursor(0, cursor_y);
    } else {
        editor_backspace();
        editor_down();
    }
}

void editor_unyank() {
	if(yank) {
		dirty = 1;
		//dbg("unyanking ###%s###\n", yank);
		char** dest_text_lines = (char**)editor_malloc(sizeof(char*) * (lines + 1));
		for(uint16_t i=0;i<cursor_y+y_offset;i++) dest_text_lines[i] = text_lines[i];
		dest_text_lines[cursor_y+y_offset] = (char*)editor_malloc(strlen(yank)+1);
		for(uint16_t i=0;i<strlen(yank);i++) dest_text_lines[cursor_y+y_offset][i] = yank[i];
		dest_text_lines[cursor_y+y_offset][strlen(yank)] = 0;
		for(uint16_t i=cursor_y+y_offset;i<lines;i++) {
			dest_text_lines[i+1] = text_lines[i];
		}
		lines++;
		editor_free(text_lines);
		text_lines = dest_text_lines;
		paint_tfb(cursor_y);
		move_cursor(0,cursor_y);
        editor_down();
	}
}


void editor_search(char * search_string) {
    if(strlen(search_string)) {
        uint8_t found = 0;
        //dbg("starting search on line %d\n", y_offset + cursor_y);
        for(uint16_t i=y_offset+cursor_y;i<lines+y_offset+cursor_y;i++) {
            uint16_t actual_line = i % lines; // wrap around
            //dbg("wrap around actual line to search is %d\n", actual_line);
            int16_t offset = 0;
            if(strlen(text_lines[actual_line])) {
                while(!found && offset < (uint16_t)(strlen(text_lines[actual_line])-1)) {
                    //dbg("found was %d and offset %d was < %d\n", found, offset, (strlen(text_lines[actual_line])-1));
                    char * ret = strstr(text_lines[actual_line]+offset, search_string);
                    if(ret) {
                        //dbg("found match at line %d and col %d. offset %d\n", actual_line, ret - text_lines[actual_line] + offset, offset);
                        // See if this is before our last search result
                        if((y_offset + cursor_y == actual_line) && (ret-text_lines[actual_line]+offset <= cursor_x)) {
                            //dbg("skipping match because already seen. cursor line %d x %d\n", y_offset + cursor_y, cursor_x);
                            // it was, keep looking on this line
                            offset=(ret-text_lines[actual_line]+offset)+1;
                        } else {
                            // A new find, adjust the y_offset and cursor
                            if(i > EDITOR_STATUS_ROW) { // adjust page a bit 
                                y_offset = actual_line-10; // show some context
                            } else {
                                y_offset = 0;
                            }
                            //dbg("new find, offset to %d\n", y_offset);
                            move_cursor(ret-text_lines[actual_line]+offset, actual_line-y_offset);
                            //dbg("moved cursor to x %d y %d\n", ret-text_lines[actual_line]+offset, actual_line-y_offset);
                            found = 1; // we're done
                        }
                    } else { //nothing found  on this line, go to the next
                        offset = strlen(text_lines[actual_line])+2;
                        //dbg("setting offset to %d\n", offset);
                    }
                    if(found) i = lines+y_offset+cursor_y+1;
                } // end found
            } // end strlen
        } // for each line
        if(!found) {
            //dbg("nothing found\n");
        }
    } else {
        //dbg("no search string given\n");
    }
    
}


void process_char(int c) {
    if(editor_mode == EDITOR_PROMPT_SEARCH || editor_mode == EDITOR_PROMPT_SAVE || editor_mode == EDITOR_PROMPT_READ) {
        if(c>31 && c<127) {
            prompted_string[prompted_count++] = c;
            TFB[EDITOR_STATUS_ROW*TFB_COLS+prompted_count+strlen(current_prompt)] = c;
            paint_tfb(EDITOR_STATUS_ROW);
            display_tfb_update(EDITOR_STATUS_ROW);
        }
        if(c==3) {
            editor_mode=EDITOR_NORMAL;
            move_cursor(cursor_x,cursor_y);
        }
        if(c==127 || c==8) {
            if(prompted_count>0) {
                prompted_string[prompted_count] = 0;
                TFB[EDITOR_STATUS_ROW*TFB_COLS+prompted_count+strlen(current_prompt)] = ' ';
                prompted_count--; // now pc is 4
                paint_tfb(EDITOR_STATUS_ROW);
                display_tfb_update(EDITOR_STATUS_ROW);
            }
        }
        if(c==13 || c == 10) {
            prompted_string[prompted_count] = 0;
            if(strlen(prompted_string)==0) {
                dbg("no text entered\n");
            } else {
                //dbg("str %s\n", prompted_string); 
                if(editor_mode == EDITOR_PROMPT_SAVE) {
                    strcpy(fn, prompted_string);
                    editor_save();
                } else if(editor_mode==EDITOR_PROMPT_READ) {
                    if(file_exists(prompted_string)) {
                        editor_open_file(prompted_string);
                    } else {
                        dbg("no such file %s\n", prompted_string);
                    }
                } else if(editor_mode==EDITOR_PROMPT_SEARCH) {
                    editor_search(prompted_string);
                }
            }
            editor_mode = EDITOR_NORMAL;
            move_cursor(cursor_x,cursor_y);
        } 
    } else {
    	//dbg("Got char %d\n", c);
    	if(c==127 || c==8) { // backspace
    		editor_backspace();
    	} else if(c == 9) { // tab, control-I
    		editor_tab();
    	} else if(c==10 || c == 13) { // CRLF
    		editor_crlf();
    	} else if(c == 3) {  // control-C 
            // we should trap this and ... what?
    	} else if(c ==11) { // control-K, yank
    		editor_yank();
    	} else if(c== 21) { // control-U, unyank
    		editor_unyank();
    	} else if(c==15) { // control-O, save as 
            prompt_for_string("Save as: ", EDITOR_PROMPT_SAVE);
        } else if(c==18) { // control-R, read into
            prompt_for_string("Read file: ",EDITOR_PROMPT_READ);
    	} else if(c==23) { // control-W, "where is" aka search TODO 
            prompt_for_string("Search string: ", EDITOR_PROMPT_SEARCH);
    	} else if(c==24) { // control-X, save (no ask)
            if(strlen(fn)>0) {
                editor_save();
            } else {
                prompt_for_string("Save as: ", EDITOR_PROMPT_SAVE);
            }
    	} else if(c == 1) { // control-A, start of line
    		editor_linestart();
    	} else if(c == 5) { // control-E, end of line
    		editor_lineend();
    	} else if(c==25) { // control Y, page up
    		editor_page_up();
    	} else if(c==22) { // control V, page down 
    		editor_page_down();
    	} else if(c == 259) { editor_up(); 
    	} else if(c == 258) { editor_down(); 
    	} else if(c == 260) { editor_left(); 
    	} else if(c == 261) { editor_right(); 
    	} else if(c == 330 || c == 262) { editor_right(); editor_backspace(); 
    	} else if(c>31 && c<127) {
    		editor_insert_character(c);
    	} else {
            dbg("not supported %d\n", c);
    		// Ignore unsupported keycodes...
    	}
    }
}


void editor_start(const char * filename) {
    mc = 0; 
    fc = 0;
    prompted_string[0] = 0;
    current_prompt[0] = 0;
    prompted_count = 0;
    editor_mode = EDITOR_NORMAL;
    lines =0;
    dirty = 0;
    y_offset = 0;
    cursor_y = 0;
    cursor_x = 0;
    fn[0] = 0;

    if(filename != NULL) { 
        strcpy(fn, filename);
		editor_open_file(fn);

	} else {
		//dbg("no filename given\n");
        // In this case, let's just make a first text_line
        editor_new_file();
	}
	move_cursor(0,0);
	y_offset = 0;
}

void editor_key(int c) {
	process_char(c);
}

// stuff is still in RAM, just redraw
void editor_activate() {
    paint_tfb(0);
    move_cursor(cursor_x,cursor_y);
}

void editor_deinit() {
    //restore_tfb();
    for(uint16_t i=0;i<lines;i++) {
        //delay_ms(1);
        editor_free(text_lines[i]);
    }
    editor_free(text_lines);
    if(yank) editor_free(yank);
    yank = 0;
    // this is usually the case because the TFB is restored after deinit 
    //if(mc != fc) dbg("mc %d fc %d\n", mc, fc);
}



