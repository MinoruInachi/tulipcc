// rcc700t.c - Tulip's fork of rcc700, a mini C compiler for RISC-V / RV32
// Upstream: https://github.com/valdanylchuk/rcc700 (MIT, see LICENSE),
// forked at 8cc06396. rcc700 is the RISC-V port of xcc700, so this file is
// the ESP32-P4 (Tab5) twin of ../xcc700/xcc700t.c and carries the same
// changes, for compiling tulip.install_c_process()/install_c_osc() user DSP
// on-device (see docs/user_c_dsp_design.md):
//   - int16_t / short pointers and arrays (lh/sh, x2 indexing);
//     scalar int16_t locals/params are treated as int
//   - casts are accepted and ignored (stores truncate by element size)
//   - `static` locals become function-scoped .bss (zero-init, persistent --
//     how user DSP keeps state between render calls)
//   - callable API rcc700_compile() with in-memory ELF output and error
//     capture (setjmp) instead of main()/exit()/file IO; the entry symbol
//     name is a parameter and lands in e_entry, so the ELF loader hands
//     back a ready function pointer after relocation.
// And two that xcc700t does not have:
//   - every file-scope name is static. Upstream's globals are called next,
//     line, token, patch, push, pop, ..., which is fine in a standalone
//     compiler and asking for trouble linked into a firmware image.
//   - identifiers are length-checked into their 64-byte buffers (take_name)
//     rather than strcpy()d, which a 64+ character name in user code overran.
// Build the CLI for host testing with -DRCC700_CLI.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdarg.h>
#include <setjmp.h>

#include "rcc700t.h"

static jmp_buf rcc_jmp;
static char *rcc_errbuf; static int rcc_errcap;
static void rcc_fail(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    if (rcc_errbuf != NULL && rcc_errcap > 0 && rcc_errbuf[0] == 0)
        vsnprintf(rcc_errbuf, rcc_errcap, fmt, ap);
    va_end(ap);
    longjmp(rcc_jmp, 1);
}

// --- Constants & Globals ---
enum {
    // tulip fork: sized for user DSP bodies rather than for compiling rcc700
    // itself, to match xcc700t's limits.
    MAX_FUNCS=128, MAX_VARS=256, MAX_LITS=256, MAX_PATCHES=1200, NAMES=4096,
    CODE_CAP=32768, RODATA_CAP=2048,
    MAXFRAME=2000, MAX_EPI=64, // frame cap (sp adjust + offsets must fit 12 bits); max returns/func
    EHDR_SZ=52, PHDR_SZ=32, N_PHDRS=1, N_SECS=7, SHDR_SZ=280, // N_SECS * 40
    R_RISCV_RELATIVE=3, R_RISCV_JUMP_SLOT=5,
    ZERO=0, RA=1, SP=2, T0=5, A0=10, A1=11, A2=12, A7=17,
    L_FUNC=0, L_STR=1, L_BSS=2
};

enum {
    T_EOF=0, T_INT=256, T_CHAR, T_VOID, T_IDENT, T_NUM, T_STR, T_RETURN,
    T_EQ, T_NE, T_LE, T_GE, T_IF, T_ELSE, T_WHILE, T_ENUM, T_ELLIPSIS,
    T_SHL, T_SHR, T_LAND, T_LOR, T_INC, T_DEC,
    T_SHORT, T_STATIC
};

// Variable/expression types: TY_* values, TF_* the bits they are made of.
// tulip fork: TF_SHORT marks 16-bit *elements* (int16_t pointers/arrays use
// lh/sh and x2 indexing). Scalar int16_t locals/params are treated as int.
enum {
    TF_BYTE=1, TF_PTR=2, TF_ARR=4, TF_GLOBAL=8, TF_CONST=16, TF_SHORT=32,
    TY_INT=0, TY_BYTE=1, TY_INTPTR=2, TY_BYTEPTR=3, TY_INTARR=4, TY_BYTEARR=5,
    TY_SHORTPTR=TF_PTR|TF_SHORT, TY_SHORTARR=TF_ARR|TF_SHORT
};

static char *src;
static int token; static int num_val; static int line; static int token_cnt;
static char str_val[256]; static int str_len;
static char *code_data; static int code_size; static int code_cap;
static char *rodata; static int rodata_sz; static int rodata_cap;
static char *name_buf; static int name_sz;

static int func_name_off[MAX_FUNCS]; static int func_addrs[MAX_FUNCS]; static int n_funcs;
static int var_name_off[MAX_VARS]; static int var_offsets[MAX_VARS]; static int var_types[MAX_VARS];
static int n_vars; // globals + locals of the current function
static int n_globals; static int bss_size; // globals (incl. enum constants) / .bss bytes
static int locals; static int esp; // bytes of locals so far / expression stack depth, both sp-relative
static int expr_type; // TY_* of the last parsed factor/expression
static int lit_vals[MAX_LITS]; static int lit_types[MAX_LITS]; static int n_lits;
static int patch_offs[MAX_PATCHES]; static int patch_lits[MAX_PATCHES]; static int n_patches;
// Per-function frame sizing: high-water mark of (locals+spills), the prologue's
// sp-adjust offset, and every epilogue's sp-adjust offset (all back-patched once
// the body is parsed and the real frame size is known).
static int frame_max; static int prologue_off; static int n_epi; static int epi_offs[MAX_EPI];

static void next(void); static void parse_func(void);
static uint8_t *write_elf_mem(const char *entry_name, uint32_t *out_size);

int rcc700_compile(const char *source, const char *entry_name,
                   uint8_t **elf_out, uint32_t *elf_size, char *err, int errlen) {
    rcc_errbuf = err; rcc_errcap = errlen;
    if (err != NULL && errlen > 0) err[0] = 0;
    // Reset all compiler state so the translation unit is reusable.
    line = 1; token = 0; token_cnt = 0; num_val = 0; str_len = 0;
    n_funcs = 0; n_vars = 0; n_globals = 0; bss_size = 0; locals = 0; esp = 0;
    expr_type = 0; n_lits = 0; n_patches = 0; frame_max = 0; prologue_off = 0; n_epi = 0;
    code_size = 0; rodata_sz = 0; name_sz = 0;
    code_cap = CODE_CAP; code_data = malloc(code_cap);
    rodata_cap = RODATA_CAP; rodata = malloc(rodata_cap);
    name_buf = malloc(NAMES);
    *elf_out = NULL; *elf_size = 0;
    if (code_data == NULL || rodata == NULL || name_buf == NULL) {
        free(code_data); free(rodata); free(name_buf);
        if (err != NULL && errlen > 0) snprintf(err, errlen, "out of memory");
        return -1;
    }
    src = (char *)source;
    if (setjmp(rcc_jmp)) {
        // A parse/codegen error longjmp'd here. write_elf_mem only fails
        // before it allocates, so nothing else is outstanding.
        free(code_data); free(rodata); free(name_buf);
        return -1;
    }
    next(); while (token != T_EOF) parse_func();
    uint8_t *out = write_elf_mem(entry_name, elf_size);
    free(code_data); free(rodata); free(name_buf);
    *elf_out = out;
    return 0;
}

// Replace <ctype.h> macros to avoid ABI issues
static int xisdigit(int c) { return c >= '0' && c <= '9'; }
static int xisalpha(int c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); }
static int xisspace(int c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; }
static int xisalnum(int c) { return xisdigit(c) || xisalpha(c); }
#define isdigit xisdigit
#define isalpha xisalpha
#define isspace xisspace
#define isalnum xisalnum

// Helpers
static void put32(char *b, int v) { b[0]=v; b[1]=v>>8; b[2]=v>>16; b[3]=v>>24; }
static void put16(char *b, int v) { b[0]=v; b[1]=v>>8; }
static int align4(int x) { return (x + 3) & ~3; }

// Copy the current identifier into a NAME_LEN buffer. Upstream strcpy()s it into
// a char[64], which a long enough name in user code overruns; say so instead.
enum { NAME_LEN = 64 };
static void take_name(char *dst) {
    size_t n = strlen(str_val);
    if (n >= NAME_LEN) rcc_fail("Line %d: name longer than %d characters", line, NAME_LEN - 1);
    memcpy(dst, str_val, n + 1);
}

static void unsupported(const char *what) {
    rcc_fail("Line %d: not implemented: %s (token %d)", line, what, token);
}

// --- Lexer ---
static void next(void) {
    while (isspace(*src) || (src[0]=='/' && src[1]=='/')) {
        if (*src == '\n') ++line;
        if (*src == '/') while (*src && *src != '\n') ++src; else ++src;
    }
    if (!*src) { token = T_EOF; return; }
    if (src[0]=='+' && src[1]=='+') { token=T_INC; src=src+2; return; }
    if (src[0]=='-' && src[1]=='-') { token=T_DEC; src=src+2; return; }
    if (src[0]=='=' && src[1]=='=') { token=T_EQ; src=src+2; return; }
    if (src[0]=='!' && src[1]=='=') { token=T_NE; src=src+2; return; }
    if (src[0]=='<' && src[1]=='=') { token=T_LE; src=src+2; return; }
    if (src[0]=='>' && src[1]=='=') { token=T_GE; src=src+2; return; }
    if (src[0]=='<' && src[1]=='<') { token=T_SHL; src=src+2; return; }
    if (src[0]=='>' && src[1]=='>') { token=T_SHR; src=src+2; return; }
    if (src[0]=='&' && src[1]=='&') { token=T_LAND; src=src+2; return; }
    if (src[0]=='|' && src[1]=='|') { token=T_LOR; src=src+2; return; }
    if (src[0]=='.' && src[1]=='.' && src[2]=='.') { token=T_ELLIPSIS; src=src+3; return; }
    if (isalpha(*src) || *src == '_') {
        char *p = str_val;
        while (isalnum(*src) || *src == '_') {
            if (p - str_val >= (int)sizeof(str_val) - 1) rcc_fail("Line %d: identifier too long", line);
            *p = *src; ++p; ++src;
        }
        *p = 0;
        token = !strcmp(str_val,"int") ? T_INT : !strcmp(str_val,"char") ? T_CHAR :
            !strcmp(str_val,"void") ? T_VOID : !strcmp(str_val,"if") ? T_IF :
            !strcmp(str_val,"else") ? T_ELSE : !strcmp(str_val,"while") ? T_WHILE :
            !strcmp(str_val,"enum") ? T_ENUM :
            !strcmp(str_val,"return") ? T_RETURN :
            !strcmp(str_val,"int16_t") ? T_SHORT : !strcmp(str_val,"short") ? T_SHORT :
            !strcmp(str_val,"static") ? T_STATIC : T_IDENT;
    } else if (isdigit(*src)) {
        num_val = strtol(src, &src, 0); token = T_NUM;
    } else if (*src == '\'') {
        ++src;
        if (*src == '\\') {
            ++src;
            if (*src == 'n') num_val = '\n'; else if (*src == 't') num_val = '\t';
            else if (*src == 'r') num_val = '\r'; else if (*src == '0') num_val = 0; else num_val = *src;
        } else num_val = *src;
        ++src; if (*src == '\'') ++src;
        token = T_NUM;
    } else if (*src == '"') {
        char *p = str_val; ++src;
        while (*src && *src != '"') {
            if (p - str_val >= (int)sizeof(str_val) - 1) rcc_fail("Line %d: string too long", line);
            if (*src == '\\') {
                ++src;
                if (*src == 'n') *p='\n'; else if (*src == 't') *p='\t';
                else if (*src == 'r') *p='\r'; else if (*src == '0') *p='\0'; else *p=*src;
            } else *p=*src;
            ++src; ++p;
        }
        *p = 0; if (*src) ++src;
        str_len = p - str_val;
        token = T_STR;
    } else { token = *src; ++src; }
    ++token_cnt;
}

static void expect(int tok) {
    if (token != tok) {
        if (tok < 256 && token < 256) rcc_fail("Line %d: expected '%c', got '%c'", line, tok, token);
        else if (tok < 256) rcc_fail("Line %d: expected '%c', got '%s'", line, tok, str_val);
        else if (token < 256) rcc_fail("Line %d: expected token %d, got '%c'", line, tok, token);
        else rcc_fail("Line %d: expected token %d, got '%s'", line, tok, str_val);
    }
    next();
}

// --- Code Emitter (RV32I) ---
static void emit32(int v) {
    if (code_size + 4 > code_cap) rcc_fail("Out of memory (code)");
    put32(code_data + code_size, v); code_size = code_size + 4;
}
static void emit16(int v) {
    if (code_size + 2 > code_cap) rcc_fail("Out of memory (code)");
    put16(code_data + code_size, v); code_size = code_size + 2;
}
static void emit_r(int f7, int rs2, int rs1, int f3, int rd, int op) {
    emit32((f7<<25)|(rs2<<20)|(rs1<<15)|(f3<<12)|(rd<<7)|op);
}
static void emit_i(int imm, int rs1, int f3, int rd, int op) {
    emit32(((imm&0xfff)<<20)|(rs1<<15)|(f3<<12)|(rd<<7)|op);
}
static int enc_j(int off, int rd) { // J-type: imm[20|10:1|11|19:12]
    return (((off>>20)&1)<<31)|(((off>>1)&0x3ff)<<21)|(((off>>11)&1)<<20)|(((off>>12)&0xff)<<12)|(rd<<7)|0x6f;
}
static int enc_b(int off, int rs2, int rs1, int f3) { // B-type: imm[12|10:5] ... imm[4:1|11]
    return (((off>>12)&1)<<31)|(((off>>5)&0x3f)<<25)|(rs2<<20)|(rs1<<15)|(f3<<12)|(((off>>1)&0xf)<<8)|(((off>>11)&1)<<7)|0x63;
}
static void emit_addi(int rd, int rs1, int imm) { emit_i(imm, rs1, 0, rd, 0x13); }
// A word access fits c.lwsp/c.swsp when it is sp-relative with off 0..252, /4
// (rd is always a real reg here, so c.lwsp's rd != x0 holds for free).
static int sp_word(int base, int off) { return base == SP && (off & 3) == 0 && off >= 0 && off < 256; }
static void emit_lw(int rd, int base, int off) {
    if (sp_word(base, off)) emit16(0x4002 | (rd<<7) | (((off>>5)&1)<<12) | (((off>>2)&7)<<4) | (((off>>6)&3)<<2)); // c.lwsp
    else emit_i(off, base, 2, rd, 3);
}
static void emit_lbu(int rd, int base, int off) { emit_i(off, base, 4, rd, 3); }
static void emit_lh(int rd, int base, int off) { emit_i(off, base, 1, rd, 3); } // sign-extends
static void emit_s(int rs2, int base, int off, int f3) {
    emit32((((off>>5)&0x7f)<<25)|(rs2<<20)|(base<<15)|(f3<<12)|((off&0x1f)<<7)|0x23);
}
static void emit_sw(int rs2, int base, int off) {
    if (sp_word(base, off)) emit16(0xC002 | (rs2<<2) | (((off>>2)&0xf)<<9) | (((off>>6)&3)<<7)); // c.swsp
    else emit_s(rs2, base, off, 2);
}
static void emit_sb(int rs2, int base, int off) { emit_s(rs2, base, off, 0); }
static void emit_sh(int rs2, int base, int off) { emit_s(rs2, base, off, 1); }
static void emit_lui(int rd, int hi20) { emit32(((hi20&0xfffff)<<12)|(rd<<7)|0x37); }
static void emit_li(int rd, int v) {
    if (v >= -2048 && v < 2048) emit_addi(rd, ZERO, v);
    else { int hi = (v + 0x800) >> 12; emit_lui(rd, hi); emit_addi(rd, rd, v - (hi<<12)); }
}
static void emit_ret(void) { emit_i(0, RA, 0, ZERO, 0x67); } // jalr zero, ra, 0
static void emit_beqz(int rs, int off) { emit32(enc_b(off, ZERO, rs, 0)); }
static void emit_j(int off) { emit32(enc_j(off, ZERO)); }

// Resolve a forward branch/jump emitted with offset 0: OR the now-known
// offset (current position - branch position) into the instruction.
static void patch(int addr, int is_j) {
    int off = code_size - addr; int w = 0;
    memcpy(&w, code_data + addr, 4);
    put32(code_data + addr, w | (is_j ? enc_j(off, ZERO) : enc_b(off, 0, 0, 0)));
}

static void patch_frame(int addr, int imm) {
    int w = 0; memcpy(&w, code_data + addr, 4);
    put32(code_data + addr, w | ((imm & 0xfff) << 20));
}

// Spill a0 into the frame above the locals; sp itself never moves inside a
// function body, so local var offsets stay valid.
static void push(void) {
    if (locals + esp >= MAXFRAME) rcc_fail("Line %d: expression too deep", line);
    emit_sw(A0, SP, locals + esp); esp = esp + 4;
    if (locals + esp > frame_max) frame_max = locals + esp;
}
static void pop(int r) { esp = esp - 4; emit_lw(r, SP, locals + esp); }

// --- Symbols & patches ---
static int add_name(const char *s) {
    int len = strlen(s) + 1;
    if (name_sz + len > NAMES) rcc_fail("Out of memory (names)");
    int off = name_sz; strcpy(name_buf + off, s);
    name_sz = name_sz + len; return off;
}
static int find_func(const char *name) {
    int i = 0;
    while (i < n_funcs) { if (!strcmp(name_buf + func_name_off[i], name)) return i; ++i; }
    return -1;
}
static int get_func(const char *name) {
    int i = find_func(name);
    if (i >= 0) return i;
    if (n_funcs >= MAX_FUNCS) rcc_fail("Too many functions");
    func_name_off[n_funcs] = add_name(name);
    func_addrs[n_funcs] = -1;
    ++n_funcs;
    return n_funcs - 1;
}
static int get_lit(int val, int type) {
    int i = 0;
    while (i < n_lits && (lit_vals[i] != val || lit_types[i] != type)) ++i;
    if (i == n_lits) {
        if (n_lits >= MAX_LITS) rcc_fail("Too many literals");
        lit_vals[i] = val; lit_types[i] = type; ++n_lits;
    }
    return i;
}

// rd = pool[lit], loaded pc-relative: auipc rd, hi; lw rd, lo(rd).
// Immediates stay 0 here; write_elf_mem patches them once the pool size (and
// so the slot's distance from the auipc) is known.
static void emit_pool_lw(int rd, int lit) {
    if (n_patches >= MAX_PATCHES) rcc_fail("Too many patches");
    patch_offs[n_patches] = code_size; patch_lits[n_patches] = lit; ++n_patches;
    emit32((rd << 7) | 0x17); // auipc rd, 0
    emit_lw(rd, rd, 0);       // lw rd, 0(rd)
}

static void emit_call(const char *name) {
    emit_pool_lw(T0, get_lit(get_func(name), L_FUNC));
    emit_i(0, T0, 0, RA, 0x67); // jalr ra, t0
}

static void emit_load_str(void) { // a0 = &.rodata[str]
    if (rodata_sz + str_len + 1 > rodata_cap) rcc_fail("Out of memory (rodata)");
    emit_pool_lw(A0, get_lit(rodata_sz, L_STR));
    memcpy(rodata + rodata_sz, str_val, str_len + 1);
    rodata_sz = rodata_sz + str_len + 1;
}

// --- Parser ---
static void parse_expr(int limit);

static int find_var(const char *name) {
    int i = n_vars;
    while (i > 0) { --i; if (!strcmp(name_buf + var_name_off[i], name)) return i; }
    return -1;
}

static int get_prec(int t) {
    if (t == '?') return 1;
    if (t == T_LOR) return 2;
    if (t == T_LAND) return 3;
    if (t == '|') return 4;
    if (t == '^') return 5;
    if (t == '&') return 6;
    if (t == T_EQ || t == T_NE) return 7;
    if (t == '<' || t == '>' || t == T_LE || t == T_GE) return 8;
    if (t == T_SHL || t == T_SHR) return 9;
    if (t == '+' || t == '-') return 10;
    if (t == '*' || t == '/' || t == '%') return 11;
    return 0;
}

static void emit_binop(int op) { // a0 = t0 op a0  (t0 = lhs, a0 = rhs)
    if (op == '+') emit_r(0, A0, T0, 0, A0, 0x33);
    else if (op == '-') emit_r(0x20, A0, T0, 0, A0, 0x33);
    else if (op == '*') emit_r(1, A0, T0, 0, A0, 0x33);
    else if (op == '/') emit_r(1, A0, T0, 4, A0, 0x33);
    else if (op == '%') emit_r(1, A0, T0, 6, A0, 0x33);
    else if (op == '&') emit_r(0, A0, T0, 7, A0, 0x33);
    else if (op == '|') emit_r(0, A0, T0, 6, A0, 0x33);
    else if (op == '^') emit_r(0, A0, T0, 4, A0, 0x33);
    else if (op == T_SHL) emit_r(0, A0, T0, 1, A0, 0x33);    // sll
    else if (op == T_SHR) emit_r(0x20, A0, T0, 5, A0, 0x33); // sra
    else if (op == '<') emit_r(0, A0, T0, 2, A0, 0x33);  // slt a0, t0, a0
    else if (op == '>') emit_r(0, T0, A0, 2, A0, 0x33);  // slt a0, a0, t0
    else if (op == T_GE) { emit_r(0, A0, T0, 2, A0, 0x33); emit_i(1, A0, 4, A0, 0x13); } // !(t0 < a0)
    else if (op == T_LE) { emit_r(0, T0, A0, 2, A0, 0x33); emit_i(1, A0, 4, A0, 0x13); } // !(a0 < t0)
    else if (op == T_EQ) { emit_r(0, A0, T0, 4, A0, 0x33); emit_i(1, A0, 3, A0, 0x13); } // xor; sltiu 1
    else if (op == T_NE) { emit_r(0, A0, T0, 4, A0, 0x33); emit_r(0, A0, ZERO, 3, A0, 0x33); } // xor; sltu 0<
    else if (op == T_LAND || op == T_LOR) { // normalize to 0/1; like xcc700, not short-circuit
        emit_r(0, T0, ZERO, 3, T0, 0x33);                  // sltu t0, zero, t0
        emit_r(0, A0, ZERO, 3, A0, 0x33);                  // sltu a0, zero, a0
        emit_r(0, A0, T0, op == T_LAND ? 7 : 6, A0, 0x33); // and/or
    }
    else unsupported("operator");
    expr_type = TY_INT;
}

static void load_var_address(int i) { // a0 = &var
    if (var_types[i] & TF_GLOBAL) emit_pool_lw(A0, get_lit(var_offsets[i], L_BSS));
    else emit_addi(A0, SP, var_offsets[i]);
}
static void load_var(int i) { // a0 = var (arrays decay to a pointer)
    int ty = var_types[i]; int is_byte = ((ty & ~TF_GLOBAL) == TY_BYTE);
    if (ty & TF_CONST) { emit_li(A0, var_offsets[i]); expr_type = TY_INT; }
    else if (ty & TF_ARR) {
        load_var_address(i);
        expr_type = (ty & TF_BYTE) ? TY_BYTEPTR : (ty & TF_SHORT) ? TY_SHORTPTR : TY_INTPTR;
    }
    else if (ty & TF_GLOBAL) {
        load_var_address(i);
        if (is_byte) emit_lbu(A0, A0, 0); else emit_lw(A0, A0, 0);
        expr_type = ty & ~TF_GLOBAL;
    } else {
        if (is_byte) emit_lbu(A0, SP, var_offsets[i]); else emit_lw(A0, SP, var_offsets[i]);
        expr_type = ty;
    }
}

static void parse_index(int base_type) { // a0 = a0 + index (scaled); '[' already current
    next(); push();
    parse_expr(1); expect(']');
    if (base_type & TF_SHORT) emit_i(1, A0, 1, A0, 0x13);        // slli a0, a0, 1
    else if (!(base_type & TF_BYTE)) emit_i(2, A0, 1, A0, 0x13); // slli a0, a0, 2
    pop(T0); emit_r(0, A0, T0, 0, A0, 0x33); // add a0, t0, a0
}

// Element load/store by pointee flags (byte / short / int).
static void emit_elem_load(int type) { // a0 = *a0
    if (type & TF_BYTE) emit_lbu(A0, A0, 0);
    else if (type & TF_SHORT) emit_lh(A0, A0, 0);
    else emit_lw(A0, A0, 0);
}
static void emit_elem_store(int type, int s, int b) { // *b = s
    if (type & TF_BYTE) emit_sb(s, b, 0);
    else if (type & TF_SHORT) emit_sh(s, b, 0);
    else emit_sw(s, b, 0);
}

static void parse_call(const char *name) {
    int arg_cnt = 0; next();
    if (token != ')') {
        parse_expr(1); push(); ++arg_cnt;
        while (token == ',') { next(); parse_expr(1); push(); ++arg_cnt; }
        if (arg_cnt > 6) rcc_fail("Line %d: more than 6 args", line);
        while (arg_cnt > 0) { --arg_cnt; pop(A0 + arg_cnt); }
    }
    expect(')'); emit_call(name);
}

static void parse_factor(void) { // Result in a0
    if (token == T_INC || token == T_DEC) { // prefix ++/--: returns the new value
        int diff = (token == T_INC) ? 1 : -1; next();
        char name[NAME_LEN]; take_name(name); expect(T_IDENT);
        int i = find_var(name);
        if (i < 0) rcc_fail("Undef: %s", name);
        load_var(i); emit_addi(A0, A0, diff);
        int is_byte = ((var_types[i] & ~TF_GLOBAL) == TY_BYTE);
        if (var_types[i] & TF_GLOBAL) {
            emit_pool_lw(T0, get_lit(var_offsets[i], L_BSS));
            if (is_byte) emit_sb(A0, T0, 0); else emit_sw(A0, T0, 0);
        } else if (is_byte) emit_sb(A0, SP, var_offsets[i]);
        else emit_sw(A0, SP, var_offsets[i]);
        expr_type = TY_INT;
    }
    else if (token == '!' || token == '~' || token == '-') {
        int op = token; next(); parse_factor();
        if (op == '-') emit_r(0x20, A0, ZERO, 0, A0, 0x33); // neg: sub a0, zero, a0
        else if (op == '~') emit_i(-1, A0, 4, A0, 0x13);    // xori a0, a0, -1
        else emit_i(1, A0, 3, A0, 0x13);                    // seqz: sltiu a0, a0, 1
        expr_type = TY_INT;
    }
    else if (token == T_NUM) { emit_li(A0, num_val); expr_type = TY_INT; next(); }
    else if (token == T_STR) { emit_load_str(); expr_type = TY_BYTEPTR; next(); }
    else if (token == '*') {
        next(); parse_factor(); int pt = expr_type;
        emit_elem_load(pt);
        expr_type = (pt & TF_BYTE) ? TY_BYTE : TY_INT;
    }
    else if (token == '&') {
        next(); char name[NAME_LEN]; take_name(name); expect(T_IDENT);
        int i = find_var(name);
        if (i < 0) rcc_fail("Undef: %s", name);
        load_var_address(i);
        expr_type = (var_types[i] & TF_BYTE) ? TY_BYTEPTR : TY_INTPTR;
    }
    else if (token == T_IDENT) {
        char name[NAME_LEN]; take_name(name); next();
        if (token == '(') { parse_call(name); expr_type = TY_INT; }
        else {
            int i = find_var(name);
            if (i < 0) rcc_fail("Undef: %s", name);
            load_var(i);
            if (token == '[') {
                int bt = expr_type;
                parse_index(bt);
                emit_elem_load(bt);
                expr_type = (bt & TF_BYTE) ? TY_BYTE : TY_INT;
            }
        }
    }
    else if (token == '(') {
        next();
        if (token == T_INT || token == T_CHAR || token == T_SHORT || token == T_VOID) {
            // tulip fork: casts accepted and ignored (values are 32-bit registers;
            // stores truncate by element size anyway).
            next(); while (token == '*') next();
            expect(')');
            parse_factor();
        } else { parse_expr(1); expect(')'); }
    }
    else rcc_fail("Line %d: unexpected token %d", line, token);
}

static void parse_expr(int limit) { // Precedence climbing; result in a0
    parse_factor();
    while (get_prec(token) >= limit) {
        int op = token; next();
        if (op == '?') { // ternary; the false branch recurses, so right-assoc
            int p1 = code_size; emit_beqz(A0, 0);
            parse_expr(2);
            int p2 = code_size; emit_j(0);
            expect(':'); patch(p1, 0);
            parse_expr(1);
            patch(p2, 1);
        } else {
            push(); parse_expr(get_prec(op) + 1);
            pop(T0); emit_binop(op);
        }
    }
}

// ra lives at sp+0 (fixed); the frame size isn't known until the body is parsed,
// so the sp-restore offset is recorded here and patched in parse_func.
static void emit_epilogue(void) {
    emit_lw(RA, SP, 0);
    if (n_epi >= MAX_EPI) rcc_fail("Line %d: too many returns", line);
    epi_offs[n_epi] = code_size; ++n_epi;
    emit_addi(SP, SP, 0); emit_ret();
}

static void parse_stmt(void) {
    esp = 0;
    if (token == T_WHILE) {
        next(); int loop_start = code_size;
        expect('('); parse_expr(1); expect(')');
        int exit_patch = code_size; emit_beqz(A0, 0);
        parse_stmt();
        emit_j(loop_start - code_size);
        patch(exit_patch, 0);
    } else if (token == T_IF) {
        next(); expect('('); parse_expr(1); expect(')');
        int p1 = code_size; emit_beqz(A0, 0);
        parse_stmt();
        if (token == T_ELSE) {
            int p2 = code_size; emit_j(0);
            patch(p1, 0); next(); parse_stmt(); patch(p2, 1);
        } else patch(p1, 0);
    } else if (token == T_INT || token == T_CHAR || token == T_SHORT) { // local: int x = expr; / int x[N];
        int is_byte = (token == T_CHAR); int is_short = (token == T_SHORT); next();
        int is_ptr = 0; while (token == '*') { is_ptr = 1; next(); }
        if (n_vars >= MAX_VARS) rcc_fail("Line %d: too many locals", line);
        var_name_off[n_vars] = add_name(str_val);
        var_offsets[n_vars] = locals; ++n_vars;
        expect(T_IDENT);
        if (token == '[') {
            next(); int sz = num_val; expect(T_NUM); expect(']');
            var_types[n_vars-1] = is_byte ? TY_BYTEARR : is_short ? TY_SHORTARR : TY_INTARR;
            locals = locals + (is_byte ? align4(sz) : is_short ? align4(sz * 2) : sz * 4);
        } else {
            var_types[n_vars-1] = is_ptr ? (is_byte ? TY_BYTEPTR : is_short ? TY_SHORTPTR : TY_INTPTR) : (is_byte ? TY_BYTE : TY_INT);
            locals = locals + 4;
            expect('='); parse_expr(1);
            if (is_byte && !is_ptr) emit_sb(A0, SP, var_offsets[n_vars-1]);
            else emit_sw(A0, SP, var_offsets[n_vars-1]);
        }
        if (locals >= MAXFRAME) rcc_fail("Line %d: stack frame overflow", line);
        if (locals > frame_max) frame_max = locals;
        expect(';');
    } else if (token == T_STATIC) {
        // tulip fork: static locals -> function-scoped .bss (zero-initialized,
        // persists across calls -- how user DSP keeps state). Optional "= 0"
        // initializer accepted; anything else is an error (no .data section).
        next();
        int is_byte = (token == T_CHAR); int is_short = (token == T_SHORT);
        if (token != T_INT && token != T_CHAR && token != T_SHORT) {
            rcc_fail("Line %d: static must be int/char/int16_t", line);
        }
        next();
        int is_ptr = 0; while (token == '*') { is_ptr = 1; next(); }
        if (n_vars >= MAX_VARS) rcc_fail("MAX_VARS exceeded");
        var_name_off[n_vars] = add_name(str_val);
        expect(T_IDENT);
        int ty = TF_GLOBAL | (is_ptr ? (is_byte ? TY_BYTEPTR : is_short ? TY_SHORTPTR : TY_INTPTR) : (is_byte ? TY_BYTE : TY_INT));
        if (token == '[') {
            next(); int sz = 0;
            if (token == T_NUM) { sz = num_val; next(); }
            else {
                int ci = find_var(str_val);
                if (ci < 0 || !(var_types[ci] & TF_CONST)) rcc_fail("Line %d: array size expected", line);
                sz = var_offsets[ci]; next();
            }
            expect(']');
            ty = TF_GLOBAL | (is_byte ? TY_BYTEARR : is_short ? TY_SHORTARR : TY_INTARR);
            var_offsets[n_vars] = bss_size;
            bss_size = bss_size + (is_byte ? align4(sz) : is_short ? align4(sz * 2) : sz * 4);
        } else {
            var_offsets[n_vars] = bss_size; bss_size = bss_size + 4;
            if (token == '=') {
                next();
                if (token != T_NUM || num_val != 0) rcc_fail("Line %d: static initializers must be 0 (.bss only)", line);
                next();
            }
        }
        var_types[n_vars] = ty;
        // Registered past n_globals: visible in this function only (C static-local
        // scoping); the next parse_func resets n_vars back to n_globals.
        ++n_vars;
        expect(';');
    } else if (token == T_RETURN) {
        next();
        if (token != ';') parse_expr(1); else emit_li(A0, 0);
        emit_epilogue(); expect(';');
    } else if (token == '{') {
        next(); while (token != '}' && token != T_EOF) parse_stmt(); expect('}');
    } else if (token == T_IDENT) {
        char name[NAME_LEN]; take_name(name); next();
        if (token == '(') { parse_call(name); expect(';'); }
        else {
            int i = find_var(name);
            if (i < 0) rcc_fail("Undef: %s", name);
            if (token == '[') { // arr[idx] = expr;
                load_var(i);
                int bt = expr_type;
                parse_index(bt); push();
                expect('='); parse_expr(1);
                pop(T0);
                emit_elem_store(bt, A0, T0);
            } else { // var = expr;
                expect('='); parse_expr(1);
                if (var_types[i] & TF_GLOBAL) {
                    emit_pool_lw(T0, get_lit(var_offsets[i], L_BSS));
                    emit_sw(A0, T0, 0);
                } else emit_sw(A0, SP, var_offsets[i]);
            }
            expect(';');
        }
    } else if (token == '*') { // *ptr = expr;
        next(); parse_factor();
        int pt = expr_type;
        push(); expect('='); parse_expr(1);
        pop(T0);
        emit_elem_store(pt, A0, T0);
        expect(';');
    } else { parse_expr(1); expect(';'); }
}

static void parse_func(void) {
    if (token == T_ENUM) { // enum [Name] { A, B = 2, ... }; members become int constants
        next(); if (token == T_IDENT) next();
        expect('{'); int val = 0;
        while (token == T_IDENT) {
            if (n_globals >= MAX_VARS) rcc_fail("Too many globals");
            var_name_off[n_globals] = add_name(str_val);
            var_offsets[n_globals] = val;
            var_types[n_globals] = TF_CONST;
            ++n_globals; n_vars = n_globals; next();
            if (token == '=') { next(); val = num_val; var_offsets[n_globals-1] = val; next(); }
            ++val; if (token == ',') next();
        }
        expect('}'); expect(';'); return;
    }

    int is_byte = (token == T_CHAR); int is_short = (token == T_SHORT);
    if (token == T_INT || token == T_CHAR || token == T_VOID || token == T_SHORT) next();
    else unsupported("declaration");
    int is_ptr = 0; while (token == '*') { is_ptr = 1; next(); }
    char name[NAME_LEN]; take_name(name); expect(T_IDENT);

    if (token == ';' || token == '[') { // global variable: zero-initialized bss
        if (n_globals >= MAX_VARS) rcc_fail("Too many globals");
        int ty = TF_GLOBAL | (is_ptr ? (is_byte ? TY_BYTEPTR : is_short ? TY_SHORTPTR : TY_INTPTR) : (is_byte ? TY_BYTE : TY_INT));
        var_offsets[n_globals] = bss_size;
        if (token == '[') {
            next(); int sz = 0;
            if (token == T_NUM) { sz = num_val; next(); }
            else if (token == T_IDENT) { // size given as an enum constant
                int i = find_var(str_val);
                if (i < 0 || !(var_types[i] & TF_CONST)) rcc_fail("Line %d: Undefined constant: %s", line, str_val);
                sz = var_offsets[i]; next();
            } else rcc_fail("Line %d: Array size expected", line);
            expect(']');
            ty = TF_GLOBAL | (is_byte ? TY_BYTEARR : is_short ? TY_SHORTARR : TY_INTARR);
            bss_size = bss_size + (is_byte ? align4(sz) : is_short ? align4(sz * 2) : sz * 4);
        } else bss_size = bss_size + 4;
        var_name_off[n_globals] = add_name(name);
        var_types[n_globals] = ty;
        ++n_globals; n_vars = n_globals;
        expect(';'); return;
    }

    expect('('); n_vars = n_globals; locals = 4; int n_args = 0; // locals start above ra (sp+0)
    frame_max = 4; n_epi = 0;
    while (token != ')' && token != T_EOF) { // parameters
        is_byte = 0; is_short = 0; int ptr_count = 0; char pname[NAME_LEN]; pname[0] = 0;
        // Skip type specifiers/qualifiers (const, int, ...); last ident is the name.
        while (token != ',' && token != ')' && token != T_EOF) {
            if (token == T_CHAR) is_byte = 1;
            else if (token == T_SHORT) is_short = 1;
            else if (token == '*') ++ptr_count;
            else if (token == T_IDENT) take_name(pname);
            else if (token != T_INT && token != T_VOID && token != T_ELLIPSIS) unsupported("parameter");
            next();
        }
        if (pname[0]) {
            if (n_vars >= MAX_VARS) rcc_fail("Too many locals");
            var_name_off[n_vars] = add_name(pname);
            var_offsets[n_vars] = locals;
            var_types[n_vars] = ptr_count >= 2 ? TY_INTPTR : ptr_count ? (is_byte ? TY_BYTEPTR : is_short ? TY_SHORTPTR : TY_INTPTR) : (is_byte ? TY_BYTE : TY_INT);
            locals = locals + 4; ++n_vars; ++n_args;
        }
        if (token == ',') next();
    }
    expect(')');
    if (token == ';') { next(); return; } // prototype
    if (n_args > 6) rcc_fail("Line %d: more than 6 args", line);
    if (locals > frame_max) frame_max = locals; // params live in the frame too
    func_addrs[get_func(name)] = code_size;
    prologue_off = code_size; emit_addi(SP, SP, 0); emit_sw(RA, SP, 0); // prologue; sp adjust patched at end
    int j = 0; while (j < n_args) { emit_sw(A0 + j, SP, var_offsets[n_globals + j]); ++j; } // spill args
    expect('{');
    while (token != '}' && token != T_EOF) parse_stmt();
    expect('}');
    emit_li(A0, 0); emit_epilogue(); // implicit return 0
    // Back-patch the frame to what the body actually used
    int fsz = (frame_max + 15) & ~15;
    patch_frame(prologue_off, -fsz);
    int k = 0; while (k < n_epi) { patch_frame(epi_offs[k], fsz); ++k; }
}

// --- ELF Writer ---
// Layout: ehdr | phdrs | .text (pool + code) | .rodata | .rela.dyn | .symtab |
// .strtab | .shstrtab | section headers. Alloc vaddrs equal file offsets (like
// a gcc -shared image), so one PT_LOAD at vaddr 0 covers them; the output loads
// on both Xtensa (via sections) and RISC-V (via PT_LOAD).
static uint8_t *write_elf_mem(const char *entry_name, uint32_t *out_size) {
    // tulip fork: e_entry is the caller-requested function ("process"/"render"),
    // so after esp_elf_relocate() the loader's entry pointer IS the user fn.
    // Looked up before anything is allocated, so failing here leaks nothing.
    int entry_func = find_func(entry_name);
    if (entry_func < 0 || func_addrs[entry_func] < 0) rcc_fail("no %s() defined", entry_name);

    int pool_sz = n_lits * 4;
    int phoff = EHDR_SZ; // phdr table right after ehdr; .text follows
    int text_addr = EHDR_SZ + N_PHDRS*PHDR_SZ; int text_size = pool_sz + code_size;
    int rodata_addr = align4(text_addr + text_size);
    // bss is a vaddr range only (p_memsz zero-fill), so no file bytes.
    // The trailing non-alloc sections sit right after .rodata
    int bss_addr = align4(rodata_addr + rodata_sz);
    int rela_off = bss_addr;
    int n_syms = n_funcs + 1;
    int symtab_off = rela_off + n_lits * 12;
    int strtab_off = symtab_off + n_syms * 16;
    int entry_addr = text_addr + pool_sz + func_addrs[entry_func];

    char *syms = calloc(1, n_syms * 16);
    char *strtab = malloc(NAMES + 1);
    char *pool = malloc(pool_sz + 4);
    char *rels = malloc(n_lits * 12 + 4);
    char *shdr = calloc(1, SHDR_SZ);
    if (syms == NULL || strtab == NULL || pool == NULL || rels == NULL || shdr == NULL) {
        free(syms); free(strtab); free(pool); free(rels); free(shdr);
        rcc_fail("out of memory (elf image)");
    }

    // Symbols: one per function; defined ones carry their .text address,
    // undefined ones are imports for the loader to resolve by name.
    strtab[0] = 0; int str_off = 1;
    int i = 0;
    while (i < n_funcs) {
        char *s = syms + (i + 1) * 16;
        char *name = name_buf + func_name_off[i];
        put32(s, str_off);
        strcpy(strtab + str_off, name); str_off = str_off + strlen(name) + 1;
        if (func_addrs[i] < 0) {
            s[12] = 16;                                   // GLOBAL | NOTYPE, UNDEF
        } else {
            put32(s + 4, text_addr + pool_sz + func_addrs[i]);
            s[12] = 18; put16(s + 14, 1);                 // GLOBAL | FUNC, .text
        }
        ++i;
    }

    // Literal pool + one relocation per slot.
    i = 0;
    while (i < n_lits) {
        int val = 0; int info = 0;
        if (lit_types[i] == L_STR) {
            val = rodata_addr + lit_vals[i]; info = R_RISCV_RELATIVE;
        } else if (lit_types[i] == L_BSS) {
            val = bss_addr + lit_vals[i]; info = R_RISCV_RELATIVE;
        } else if (func_addrs[lit_vals[i]] >= 0) {
            val = text_addr + pool_sz + func_addrs[lit_vals[i]]; info = R_RISCV_RELATIVE;
        } else {
            info = ((lit_vals[i] + 1) << 8) | R_RISCV_JUMP_SLOT; // import, by symbol
        }
        put32(pool + i*4, val);
        put32(rels + i*12, text_addr + i*4); put32(rels + i*12 + 4, info);
        put32(rels + i*12 + 8, val);                      // addend (RELATIVE: base + A)
        ++i;
    }

    // Point each auipc+lw pair at its pool slot, now that distances are known.
    i = 0;
    while (i < n_patches) {
        int off = patch_offs[i];
        int delta = patch_lits[i] * 4 - (pool_sz + off);
        int hi = (delta + 0x800) >> 12; int w = 0;
        memcpy(&w, code_data + off, 4);
        put32(code_data + off, w | ((hi & 0xfffff) << 12));
        memcpy(&w, code_data + off + 4, 4);
        put32(code_data + off + 4, w | (((delta - (hi << 12)) & 0xfff) << 20));
        ++i;
    }

    // Section headers: 0:null 1:.text 2:.rodata 3:.rela.dyn 4:.symtab
    // 5:.strtab 6:.shstrtab.  shstrtab name offsets are fixed.
    static const char shstr[] = "\0.text\0.rodata\0.rela.dyn\0.symtab\0.strtab\0.shstrtab\0";
    int shstr_sz = 51; int shstr_off = strtab_off + str_off;
    int shoff = align4(shstr_off + shstr_sz);
    // 1: .text  (PROGBITS, ALLOC|EXEC)
    put32(shdr+40, 1); put32(shdr+44, 1); put32(shdr+48, 6); put32(shdr+52, text_addr);
    put32(shdr+56, text_addr); put32(shdr+60, text_size); put32(shdr+72, 4);
    // 2: .rodata  (PROGBITS, ALLOC: literals only; globals live in the bss tail)
    put32(shdr+80, 7); put32(shdr+84, 1); put32(shdr+88, 2); put32(shdr+92, rodata_addr);
    put32(shdr+96, rodata_addr); put32(shdr+100, rodata_sz); put32(shdr+112, 4);
    // 3: .rela.dyn  (RELA, link=.symtab)
    put32(shdr+120, 15); put32(shdr+124, 4); put32(shdr+136, rela_off);
    put32(shdr+140, n_lits*12); put32(shdr+144, 4); put32(shdr+148, 1);
    put32(shdr+152, 4); put32(shdr+156, 12);
    // 4: .symtab  (link=.strtab, info=first global)
    put32(shdr+160, 25); put32(shdr+164, 2); put32(shdr+176, symtab_off);
    put32(shdr+180, n_syms*16); put32(shdr+184, 5); put32(shdr+188, 1);
    put32(shdr+192, 4); put32(shdr+196, 16);
    // 5: .strtab
    put32(shdr+200, 33); put32(shdr+204, 3); put32(shdr+216, strtab_off);
    put32(shdr+220, str_off); put32(shdr+232, 1);
    // 6: .shstrtab
    put32(shdr+240, 41); put32(shdr+244, 3); put32(shdr+256, shstr_off);
    put32(shdr+260, shstr_sz); put32(shdr+272, 1);

    // Single PT_LOAD: text+rodata file-backed, .bss tail is memsz-filesz zero fill.
    // The RISC-V loader pins svaddr to 0, so this segment must be vaddr 0.
    char phdr[PHDR_SZ]; memset(phdr, 0, PHDR_SZ);
    put32(phdr+0, 1);                                // p_type = PT_LOAD
    put32(phdr+4, 0);                                // p_offset = 0
    put32(phdr+8, 0);                                // p_vaddr = 0 (== p_offset)
    put32(phdr+16, rodata_addr + rodata_sz);         // p_filesz: through .rodata
    put32(phdr+20, bss_addr + bss_size);             // p_memsz: + zero-fill bss
    put32(phdr+24, 7); put32(phdr+28, 4);            // p_flags = R|W|X, p_align

    char ehdr[EHDR_SZ]; memset(ehdr, 0, EHDR_SZ);
    ehdr[0]=0x7f; ehdr[1]='E'; ehdr[2]='L'; ehdr[3]='F'; ehdr[4]=1; ehdr[5]=1; ehdr[6]=1;
    put16(ehdr+16, 3); put16(ehdr+18, 243);          // e_type=ET_DYN, e_machine=EM_RISCV
    put32(ehdr+20, 1); put32(ehdr+24, entry_addr);   // e_version, e_entry
    put32(ehdr+28, phoff); put32(ehdr+32, shoff);    // e_phoff, e_shoff
    put16(ehdr+40, EHDR_SZ); put16(ehdr+46, 40);     // ehsize, shentsize
    put16(ehdr+42, PHDR_SZ); put16(ehdr+44, N_PHDRS); // phentsize, phnum
    put16(ehdr+48, N_SECS); put16(ehdr+50, 6);       // shnum, shstrndx

    // tulip fork: assemble the ELF in memory instead of writing a file.
    uint32_t total = shoff + SHDR_SZ;
    uint8_t *img = calloc(1, total);
    if (img != NULL) {
        memcpy(img, ehdr, EHDR_SZ);
        memcpy(img + phoff, phdr, PHDR_SZ);
        memcpy(img + text_addr, pool, pool_sz);
        memcpy(img + text_addr + pool_sz, code_data, code_size);
        memcpy(img + rodata_addr, rodata, rodata_sz);
        memcpy(img + rela_off, rels, n_lits * 12);
        memcpy(img + symtab_off, syms, n_syms * 16);
        memcpy(img + strtab_off, strtab, str_off);
        memcpy(img + shstr_off, shstr, shstr_sz);
        memcpy(img + shoff, shdr, SHDR_SZ);
    }
    free(pool); free(rels); free(syms); free(strtab); free(shdr);
    if (img == NULL) rcc_fail("out of memory (elf image)");
    *out_size = total;
    return img;
}

#ifdef RCC700_CLI
// Host-side CLI for testing: rcc700t <in.c> -o <out.elf> [entry]
int main(int argc, char **argv) {
    const char *in = argc > 1 ? argv[1] : "input.c";
    const char *out = (argc > 3 && !strcmp(argv[2], "-o")) ? argv[3] : "output.elf";
    const char *entry = argc > 4 ? argv[4] : "process";
    FILE *f = fopen(in, "rb");
    if (f == NULL) { fprintf(stderr, "cannot open %s\n", in); return 1; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc(sz + 1);
    if (fread(buf, 1, sz, f) != (size_t)sz) { fprintf(stderr, "read failed\n"); return 1; }
    buf[sz] = 0; fclose(f);
    uint8_t *elf; uint32_t elf_sz; char err[512];
    if (rcc700_compile(buf, entry, &elf, &elf_sz, err, sizeof(err)) != 0) {
        fprintf(stderr, "compile failed: %s\n", err); return 1;
    }
    FILE *o = fopen(out, "wb");
    fwrite(elf, 1, elf_sz, o); fclose(o);
    fprintf(stderr, "%s: %u bytes, entry %s\n", out, (unsigned)elf_sz, entry);
    return 0;
}
#endif
