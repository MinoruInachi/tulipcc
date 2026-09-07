/* amy_message.inc -- tulip.amy_message(), the C accelerator for amy.message().
 *
 * Textually included, not compiled on its own, so that adding a consumer costs
 * no build-system change on any of the seven targets that build
 * amy_connector.c. It lived inline in amy_connector.c until TAB5 needed it too:
 * that board builds its own modtulip_tab5.c and does NOT build amy_connector.c
 * (which is written around the ESP32-S3's MIDI UART), so it had no way to reach
 * this code and fell back to the pure-Python amy.message() on every send.
 *
 * Exactly one translation unit per build includes this -- amy_connector.c
 * everywhere else, modtulip_tab5.c on TAB5, and those two are never compiled
 * together -- so tulip_amy_message() staying non-static is not a clash.
 *
 * The code below is unchanged from where it was written; the commentary in it
 * is the reference for how this stays in step with amy.message().
 */

/* ---------------------------------------------------------------- message
 * tulip.amy_message(**kwargs) -> the wire string, or None.
 *
 * amy.message() in Python costs hundreds of microseconds per message on a
 * device — it slices the keyword map twice per argument (`map_code[:-1]` /
 * `[-1]`), calls a handler per argument, and accumulates with `+=`, and
 * every one of those allocates on the MicroPython heap. In C the same
 * message is a table walk into a stack buffer, ~6x faster measured on
 * ESP32 hardware. _boot.py installs this over amy.message for every
 * target, with the Python original kept as the fallback.
 *
 * It returns None rather than raising whenever it meets ANYTHING it does
 * not reproduce exactly — an unknown keyword, a numeric string where an
 * int was meant, an overlong result. The caller falls back to
 * amy.message(), which is the reference implementation and stays the one
 * that defines the format. That is the whole safety argument: this is an
 * accelerator for the shapes it is sure about, never a second definition
 * of the wire protocol. Bailing is always correct; being wrong is not.
 *
 * The keyword table is GENERATED from amy's own _KW_MAP_LIST (see
 * tulip/shared/gen_amy_kwmap.py) so it cannot drift from the Python, JS
 * and GDScript maps that are generated from the same list. This is pure
 * string building — it never touches AMY itself — so it compiles the same
 * on every target, including the web builds where AMY is external.
 */
#include "amy_kwmap.h"

#define AMY_MSG_CAP 512
#define AMY_MSG_MAX_KW 16

/* The table's keywords as qstrs, interned once on first use.
 * qstr_find_strn() FINDS without creating: a keyword that no frozen
 * module or caller ever mentioned has no qstr, stays 0, and so never
 * matches an incoming kwarg -- which routes that message to Python,
 * exactly as an unknown keyword should be. (In practice every keyword in
 * the table appears as a string literal in frozen amy/__init__.py, so
 * they all have compile-time qstrs.) */
static qstr amy_kw_q[AMY_KW_N];
static bool amy_kw_q_ready = false;

static qstr amy_kw_qstr(int k) {
    if (!amy_kw_q_ready) {
        for (int i = 0; i < AMY_KW_N; i++)
            amy_kw_q[i] = qstr_find_strn(AMY_KW[i].name, strlen(AMY_KW[i].name));
        amy_kw_q_ready = true;
    }
    return amy_kw_q[k];
}

/* Every formatter below calls MICROPYTHON'S OWN, not the C library's.
 * That is the whole correctness argument. snprintf("%.6f") disagrees with
 * Python as soon as a value needs more significant digits than a 24-bit
 * mantissa carries:
 *
 *     999.999      snprintf 999.999023   MicroPython 999.999
 *     123.456789   snprintf 123.456795   MicroPython 123.456792
 *
 * mp_format_float() is literally what `'%.6f' % x` runs (mpprint.c), and
 * mp_obj_print_helper(PRINT_STR) is literally what `str(x)` runs, so
 * matching is structural rather than a thing to be sampled for. */
#include "py/formatfloat.h"

/* append, refusing to overflow */
static bool amy_put(char *buf, size_t *len, const char *s, size_t n) {
    if (*len + n >= AMY_MSG_CAP)
        return false;
    memcpy(buf + *len, s, n);
    *len += n;
    buf[*len] = 0;
    return true;
}

/* str(o), MicroPython's own, appended. Used where amy's handlers call
 * str() rather than trunc() -- list elements and coef elements, whose
 * floats take the repr path and NOT %.6f. */
static bool amy_put_pystr(char *buf, size_t *len, mp_obj_t o) {
    vstr_t vs;
    vstr_init(&vs, 24);
    mp_print_t pr = {.data = &vs, .print_strn = (mp_print_strn_t)vstr_add_strn};
    mp_obj_print_helper(&pr, o, PRINT_STR);
    bool ok = amy_put(buf, len, vs.buf, vs.len);
    vstr_clear(&vs);
    return ok;
}

/* A plain decimal, for the int fast path. str(int) IS this, so it is an
 * exact shortcut around the vstr above rather than a second opinion.
 * Hand-rolled, NOT snprintf: the ESP-IDF builds use newlib-nano
 * (CONFIG_NEWLIB_NANO_FORMAT), whose printf silently mangles %lld -- on
 * hardware that turned every int argument into garbage while desktop
 * passed -- and on the VCV/linux builds snprintf is mp_vprintf-backed
 * with its own format gaps. Digits owe nothing to any libc. */
static bool amy_put_int(char *buf, size_t *len, mp_int_t i) {
    char tmp[24];   /* 64-bit mp_int_t: 20 digits + sign fits */
    char *p = tmp + sizeof tmp;
    bool neg = i < 0;
    /* unsigned copy so the most-negative value can't overflow on negate */
    unsigned long long u = neg ? -(unsigned long long)i : (unsigned long long)i;
    do { *--p = (char)('0' + (u % 10)); u /= 10; } while (u);
    if (neg) *--p = '-';
    return amy_put(buf, len, p, (size_t)(tmp + sizeof tmp - p));
}

/* amy's trunc():
 *     str -> '%'-token passthrough, blank -> '', else float(it)
 *     float -> ('%.6f' % x).rstrip('0').rstrip('.')
 *     anything else -> str(x)   (so a bool really does come out "True")
 * The '.' that %.6f always emits is what makes the rstrip safe: it stops
 * the zero-strip before it can eat an integer part like 100.0 -> "1". */
static bool amy_put_trunc(char *buf, size_t *len, mp_obj_t v) {
    if (mp_obj_is_str(v)) {
        size_t sl;
        const char *sp = mp_obj_str_get_data(v, &sl);
        if (sl && sp[0] == '%')
            return amy_put(buf, len, sp, sl);
        size_t a = 0, b = sl;
        while (a < b && (sp[a] == ' ' || sp[a] == '\t')) a++;
        while (b > a && (sp[b-1] == ' ' || sp[b-1] == '\t')) b--;
        if (a == b)
            return true;                       /* blank -> '' */
        return false;                          /* float(str): let Python */
    }
    if (mp_obj_is_float(v)) {
        char tmp[48];
        int n = mp_format_float((mp_float_t)mp_obj_get_float(v), tmp,
                                sizeof tmp, 'f', 6, '\0');
        if (n < 0 || (size_t)n >= sizeof tmp)
            return false;
        if (strchr(tmp, '.') != NULL) {        /* inf/nan have none */
            while (n > 0 && tmp[n-1] == '0') n--;
            while (n > 0 && tmp[n-1] == '.') n--;
            tmp[n] = 0;
        }
        return amy_put(buf, len, tmp, (size_t)n);
    }
    return amy_put_pystr(buf, len, v);         /* ints, bools, everything */
}

/* amy's str_of_int(): a '%'-token passes through, else str(int(arg)).
 * A numeric string would need int(str) semantics, so those bail. */
static bool amy_put_int_of(char *buf, size_t *len, mp_obj_t v) {
    if (mp_obj_is_str(v)) {
        size_t sl;
        const char *sp = mp_obj_str_get_data(v, &sl);
        if (sl && sp[0] == '%')
            return amy_put(buf, len, sp, sl);
        return false;                          /* int("12"): let Python */
    }
    if (mp_obj_is_float(v)) {
        mp_float_t f = mp_obj_get_float(v);
        if (!(f >= (mp_float_t)-1e15 && f <= (mp_float_t)1e15))
            return false;   /* int(huge/inf/nan): cast is UB, let Python */
        return amy_put_int(buf, len, (mp_int_t)f);
    }
    if (mp_obj_is_small_int(v))
        return amy_put_int(buf, len, MP_OBJ_SMALL_INT_VALUE(v));
    if (v == mp_const_true || v == mp_const_false)
        return amy_put_int(buf, len, mp_obj_get_int(v));   /* int(bool) */
    if (mp_obj_is_int(v))
        return amy_put_pystr(buf, len, v);     /* big int: str() is exact */
    return false;
}

/* Lists AND tuples: amy's parse_list_or_comma_string and
 * parse_ctrl_coefs both join either. */
static bool amy_seq_items(mp_obj_t v, size_t *n, mp_obj_t **items) {
    if (mp_obj_is_type(v, &mp_type_list)) { mp_obj_list_get(v, n, items); return true; }
    if (mp_obj_is_type(v, &mp_type_tuple)) { mp_obj_tuple_get(v, n, items); return true; }
    return false;
}

/* One list/coef element: amy's elem_to_str(). None is '', a float truncs
 * to the same canonical form a scalar coef gets, everything else str(). */
static bool amy_put_elem(char *buf, size_t *len, mp_obj_t e) {
    if (e == mp_const_none)
        return true;
    if (mp_obj_is_float(e))
        return amy_put_trunc(buf, len, e);
    if (mp_obj_is_small_int(e))
        return amy_put_int(buf, len, MP_OBJ_SMALL_INT_VALUE(e));
    return amy_put_pystr(buf, len, e);
}

/* amy's parse_list_or_comma_string(): ','.join of elem_to_str. */
static bool amy_put_list(char *buf, size_t *len, mp_obj_t v) {
    size_t n_items;
    mp_obj_t *items;
    if (!amy_seq_items(v, &n_items, &items))
        return amy_put_pystr(buf, len, v);
    for (size_t k = 0; k < n_items; k++) {
        if (k && !amy_put(buf, len, ",", 1))
            return false;
        if (!amy_put_elem(buf, len, items[k]))
            return false;
    }
    return true;
}

/* amy's parse_ctrl_coefs(): four accepted shapes, elements formatted by
 * the same elem_to_str as lists. */
static const char *const AMY_COEF_FIELDS[] = {
    /* Wire order, so index == coef slot. 'mod1' is last, not beside 'mod0':
     * amy appends new control inputs so stored patch strings, which are
     * positional, keep reading the same way. */
    "const", "note", "vel", "eg0", "eg1", "mod0", "bend", "ext0", "ext1", "mod1",
};
#define AMY_COEF_N 10

/* Superseded coef names, mapped to their slot in AMY_COEF_FIELDS. amy's
 * parse_ctrl_coefs keeps accepting these, so the fast path must too or it
 * would bail to Python for every pre-two-modulator patch. */
static const struct { const char *name; int slot; } AMY_COEF_ALIASES[] = {
    {"mod", 5},   /* named the only modulator before there were two */
};
#define AMY_COEF_ALIAS_N 1

static bool amy_put_coefs(char *buf, size_t *len, mp_obj_t v) {
    if (mp_obj_is_str(v)) {
        /* ','.join(trunc(x) for x in s.split(',')) */
        size_t sl;
        const char *sp = mp_obj_str_get_data(v, &sl);
        size_t a = 0;
        bool first = true;
        while (a <= sl) {
            size_t b = a;
            while (b < sl && sp[b] != ',') b++;
            if (!first && !amy_put(buf, len, ",", 1))
                return false;
            first = false;
            mp_obj_t piece = mp_obj_new_str(sp + a, b - a);
            if (!amy_put_trunc(buf, len, piece))
                return false;
            if (b >= sl) break;
            a = b + 1;
        }
        return true;
    }
    if (mp_obj_is_int(v) || mp_obj_is_float(v))
        return amy_put_trunc(buf, len, v);

    mp_obj_t vals[AMY_COEF_N];
    size_t n_items = 0;
    mp_obj_t *items;
    if (mp_obj_is_type(v, &mp_type_dict)) {
        for (int i = 0; i < AMY_COEF_N; i++)
            vals[i] = mp_const_none;
        mp_map_t *m = mp_obj_dict_get_map(v);
        for (size_t i = 0; i < m->alloc; i++) {
            if (m->table[i].key == MP_OBJ_NULL)
                continue;
            if (!mp_obj_is_str(m->table[i].key))
                return false;
            size_t kl;
            const char *kp = mp_obj_str_get_data(m->table[i].key, &kl);
            int slot = -1;
            for (int f = 0; f < AMY_COEF_N; f++)
                if (strlen(AMY_COEF_FIELDS[f]) == kl &&
                    memcmp(AMY_COEF_FIELDS[f], kp, kl) == 0) { slot = f; break; }
            for (int f = 0; slot < 0 && f < AMY_COEF_ALIAS_N; f++)
                if (strlen(AMY_COEF_ALIASES[f].name) == kl &&
                    memcmp(AMY_COEF_ALIASES[f].name, kp, kl) == 0) { slot = AMY_COEF_ALIASES[f].slot; break; }
            if (slot < 0)
                return false;              /* unrecognised: Python raises */
            vals[slot] = m->table[i].value;
        }
        items = vals;
        n_items = AMY_COEF_N;
    } else if (!amy_seq_items(v, &n_items, &items)) {
        return false;
    }
    /* trim_trailing(coefs, x is not None) */
    while (n_items > 0 && items[n_items - 1] == mp_const_none)
        n_items--;
    for (size_t k = 0; k < n_items; k++) {
        if (k && !amy_put(buf, len, ",", 1))
            return false;
        if (!amy_put_elem(buf, len, items[k]))
            return false;
    }
    return true;
}

static bool amy_fmt_arg(char *buf, size_t *len, char type, mp_obj_t v) {
    switch (type) {
    case 'I': return amy_put_int_of(buf, len, v);
    case 'F': return amy_put_trunc(buf, len, v);
    case 'L': return amy_put_list(buf, len, v);
    case 'S': return amy_put_pystr(buf, len, v);
    case 'C': return amy_put_coefs(buf, len, v);
    default:  return false;
    }
}

mp_obj_t tulip_amy_message(size_t n_args, const mp_obj_t *pos, mp_map_t *kwargs) {
    (void)pos;
    if (n_args != 0 || kwargs == NULL)
        return mp_const_none;

    /* Collect (table index, value), which IS priority order: the
     * generated table is _KW_MAP_LIST's order and patch_string is last. */
    uint8_t idx[AMY_MSG_MAX_KW];
    mp_obj_t val[AMY_MSG_MAX_KW];
    int n = 0;
    for (size_t i = 0; i < kwargs->alloc; i++) {
        if (kwargs->table[i].key == MP_OBJ_NULL)
            continue;
        if (n >= AMY_MSG_MAX_KW)
            return mp_const_none;
        if (!mp_obj_is_qstr(kwargs->table[i].key))
            return mp_const_none;
        qstr q = MP_OBJ_QSTR_VALUE(kwargs->table[i].key);
        mp_obj_t v = kwargs->table[i].value;
        int found = -1;
        for (int k = 0; k < AMY_KW_N; k++)
            if (amy_kw_qstr(k) == q && amy_kw_q[k] != 0) { found = k; break; }
        if (found < 0)
            return mp_const_none;                /* unknown: let Python raise */
        if (v == mp_const_none) {
            /* ticks=None is ignored (synth.py's note_on/note_off pass it on
             * every unscheduled note, so this must stay on the fast path);
             * any other None is an error, and Python is where that error
             * should come from. */
            if (strcmp(AMY_KW[found].name, "ticks") == 0)
                continue;
            return mp_const_none;
        }
        idx[n] = (uint8_t)found;
        val[n] = v;
        n++;
    }
    /* insertion sort by priority; n is tiny */
    for (int i = 1; i < n; i++) {
        uint8_t ki = idx[i];
        mp_obj_t vi = val[i];
        int j = i - 1;
        while (j >= 0 && idx[j] > ki) {
            idx[j + 1] = idx[j]; val[j + 1] = val[j]; j--;
        }
        idx[j + 1] = ki; val[j + 1] = vi;
    }

    char buf[AMY_MSG_CAP];
    size_t len = 0;
    buf[0] = 0;
    for (int i = 0; i < n; i++) {
        const amy_kw_t *e = &AMY_KW[idx[i]];
        if (!amy_put(buf, &len, e->wire, strlen(e->wire)))
            return mp_const_none;
        if (!amy_fmt_arg(buf, &len, e->type, val[i]))
            return mp_const_none;
    }
    if (!amy_put(buf, &len, "Z", 1))
        return mp_const_none;
    return mp_obj_new_str(buf, len);
}
MP_DEFINE_CONST_FUN_OBJ_KW(tulip_amy_message_obj, 0, tulip_amy_message);
