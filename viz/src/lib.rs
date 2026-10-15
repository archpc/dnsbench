//! DNSBench point-cloud renderer: 62,608 resolver candidates as a spatial field.
//! Canvas2D via a persistent Float32Array (shared with JS), drawn in one pass per
//! frame. Rendering is dirty-driven: JS calls mark_dirty when data actually changes;
//! idle frames cost nothing.

use std::cell::RefCell;
use std::os::raw::{c_char, c_int};

const MAX_POINTS: usize = 65536;
// float layout per point: x, y, cat(f32), status, lat_ms, phase, target_alpha
const STRIDE: usize = 7;

thread_local! {
    static STATE: RefCell<State> = RefCell::new(State::new());
}

struct State {
    data: Vec<f32>,       // STRIDE * MAX_POINTS
    n: usize,             // active points
    width: u32,
    height: u32,
    dirty: bool,
    // color palette as premultiplied-ish RGB (0-255) per category
    palette: Vec<[u8; 3]>,
    n_cats: usize,
}

impl State {
    fn new() -> Self {
        State {
            data: vec![0.0; STRIDE * MAX_POINTS],
            n: 0,
            width: 0,
            height: 0,
            dirty: true,
            palette: vec![[0, 0, 0]; 16],
            n_cats: 0,
        }
    }
}

#[no_mangle]
pub extern "C" fn viz_init(n_cats: c_int) -> *mut f32 {
    STATE.with(|s| {
        let mut s = s.borrow_mut();
        s.n_cats = n_cats.max(0) as usize;
        s.dirty = true;
        s.data.as_mut_ptr()
    })
}

#[no_mangle]
pub extern "C" fn viz_set_count(n: c_int) {
    STATE.with(|s| {
        let mut s = s.borrow_mut();
        let n = (n.max(0) as usize).min(MAX_POINTS);
        if n != s.n {
            s.n = n;
            s.dirty = true;
        }
    });
}

#[no_mangle]
pub extern "C" fn viz_resize(width: u32, height: u32) {
    STATE.with(|s| {
        let mut s = s.borrow_mut();
        if width != s.width || height != s.height {
            s.width = width;
            s.height = height;
            s.dirty = true;
        }
    });
}

/// Update one point (0-based index) — called in bulk from JS after data fetch.
/// Returns 1 if this changed any rendered-relevant value, else 0.
#[no_mangle]
pub extern "C" fn viz_set_point(
    idx: c_int, x: f32, y: f32, cat: c_int, status: c_int, lat_ms: f32,
) -> c_int {
    STATE.with(|s| {
        let mut s = s.borrow_mut();
        if idx < 0 || (idx as usize) >= MAX_POINTS {
            return 0;
        }
        let i = idx as usize;
        let base = i * STRIDE;
        let phase = status_phase(status);
        let target = status_alpha(status, lat_ms);
        let d = &mut s.data;
        let changed = d[base] != x
            || d[base + 1] != y
            || d[base + 2] != cat as f32
            || d[base + 3] != status as f32
            || d[base + 4] != lat_ms
            || d[base + 5] != phase
            || d[base + 6] != target;
        if changed {
            d[base] = x;
            d[base + 1] = y;
            d[base + 2] = cat as f32;
            d[base + 3] = status as f32;
            d[base + 4] = lat_ms;
            d[base + 5] = phase;
            d[base + 6] = target;
            s.dirty = true;
        }
        if changed {
            1
        } else {
            0
        }
    })
}

/// Smoothly ease point alphas toward targets; returns 1 while any motion remains.
#[no_mangle]
pub extern "C" fn viz_tick(dt_ms: f32) -> c_int {
    STATE.with(|s| {
        let mut s = s.borrow_mut();
        if s.n == 0 {
            return 0;
        }
        let mut moving = false;
        let k = (dt_ms / 120.0).min(1.0); // easing rate
        for i in 0..s.n {
            let base = i * STRIDE;
            let target = s.data[base + 6];
            let cur = alpha_of(&s.data, base, target);
            let next = cur + (target - cur) * k;
            if (next - target).abs() > 0.004 {
                set_alpha(&mut s.data, base, next, target);
                moving = true;
                s.dirty = true;
            } else if cur != target {
                set_alpha(&mut s.data, base, target, target);
            }
        }
        if moving {
            1
        } else {
            0
        }
    })
}

// alpha storage: packed into the phase slot's fractional bits is overkill —
// dedicate a separate alpha value encoded in lat fraction would corrupt data.
// Simpler: alpha stored in a parallel array indexed by point, in the f32's
// spare slot via status phase: we re-purpose nothing; we store current alpha
// in data[base+3+?] — instead use a dedicated small vec.
thread_local! {
    static ALPHAS: RefCell<Vec<f32>> = RefCell::new(vec![0.0f32; MAX_POINTS]);
}

fn alpha_of(_d: &[f32], base: usize, target: f32) -> f32 {
    ALPHAS.with(|a| a.borrow()[base / STRIDE]).max(0.0)
}

fn set_alpha(d: &mut [f32], base: usize, val: f32, target: f32) {
    ALPHAS.with(|a| a.borrow_mut()[base / STRIDE] = val);
    let _ = target;
}

#[no_mangle]
pub extern "C" fn viz_is_dirty() -> c_int {
    STATE.with(|s| if s.borrow().dirty { 1 } else { 0 })
}

#[no_mangle]
pub extern "C" fn viz_clear_dirty() {
    STATE.with(|s| s.borrow_mut().dirty = false);
}

/// Palette upload: n_cats * 3 bytes per category (R,G,B).
#[no_mangle]
pub unsafe extern "C" fn viz_set_palette(ptr: *const u8, n_cats: c_int) {
    STATE.with(|s| {
        let mut s = s.borrow_mut();
        let n = (n_cats.max(0) as usize).min(16);
        s.palette = vec![[0, 0, 0]; n];
        for i in 0..n {
            let r = *ptr.add(i * 3);
            let g = *ptr.add(i * 3 + 1);
            let b = *ptr.add(i * 3 + 2);
            s.palette[i] = [r, g, b];
        }
        s.n_cats = n;
        s.dirty = true;
    });
}

/// Render: iterates points, emits fillRect-equivalent via the Canvas2D C API
/// exposed by JS bindings. JS provides: ctx_set_fillstyle(r,g,b,a),
/// ctx_fill_rect(x,y,w,h). We batch by grouping nearby same-color points
/// implicitly via fillStyle changes only when color actually changes.
#[no_mangle]
pub unsafe extern "C" fn viz_render() -> c_int {
    let mut out_drawn: c_int = 0;
    STATE.with(|st| {
        {
            let s = st.borrow();
            if !s.dirty {
                out_drawn = 0;
                return;
            }
        }
        let mut s = st.borrow_mut();
        let dw = if s.width > 0 { s.width as f32 } else { 1.0 };
        let dh = if s.height > 0 { s.height as f32 } else { 1.0 };
        let alphas = ALPHAS.with(|a| a.borrow().clone());
        let mut last: [u8; 3] = [255, 255, 255];
        let mut last_a: f32 = -1.0;
        let mut drawn = 0usize;
        let n = s.n;
        for i in 0..n {
            let base = i * STRIDE;
            let x = s.data[base] * dw;
            let y = s.data[base + 1] * dh;
            let cat = (s.data[base + 2] as usize).min(s.n_cats.saturating_sub(1));
            let status = s.data[base + 3] as i32;
            let lat = s.data[base + 4];
            let alpha = alphas[i].clamp(0.0, 1.0);
            if alpha < 0.02 {
                continue;
            }
            let speed = if lat > 0.0 { (1.0 - (lat / 300.0)).max(0.15) } else { 0.5 };
            let rgb = if cat < s.palette.len() {
                s.palette[cat]
            } else {
                [128, 128, 128]
            };
            let r = (rgb[0] as f32 * speed) as u8;
            let g = (rgb[1] as f32 * speed) as u8;
            let b = (rgb[2] as f32 * speed) as u8;
            let size = point_size(status, lat);
            if [r, g, b] != last || (alpha - last_a).abs() > 0.02 {
                viz_set_fillstyle(r, g, b, alpha);
                last = [r, g, b];
                last_a = alpha;
            }
            viz_fill_rect(x - size * 0.5, y - size * 0.5, size, size);
            drawn += 1;
        }
        s.dirty = false;
        out_drawn = drawn as c_int;
    });
    out_drawn
}

fn status_phase(status: i32) -> f32 {
    match status {
        0 => 0.0, // unknown
        1 => 1.0, // alive
        2 => 2.0, // dead
        3 => 3.0, // hijacker
        _ => 0.0,
    }
}

fn status_alpha(status: i32, lat: f32) -> f32 {
    match status {
        0 => 0.18,                                              // unknown: dim
        1 => (0.35 + 0.55 * (1.0 - (lat / 400.0).min(1.0))).clamp(0.25, 0.9),
        2 => 0.03,                                              // dead: fade out
        3 => 1.0,                                               // hijacker: full alarm
        _ => 0.2,
    }
}

fn point_size(status: i32, lat: f32) -> f32 {
    match status {
        1 => if lat > 0.0 && lat < 50.0 { 2.2 } else { 1.6 },
        3 => 3.0,
        _ => 1.2,
    }
}

extern "C" {
    #[link_name = "viz_set_fillstyle"]
    fn viz_set_fillstyle(r: u8, g: u8, b: u8, a: f32);
    #[link_name = "viz_fill_rect"]
    fn viz_fill_rect(x: f32, y: f32, w: f32, h: f32);
}

// ---- helper exposed to JS for fast bulk ingest ----

/// Bulk-copy a JS Float32Array (interleaved per-point: cat,status,lat then
/// x,y precomputed on JS side) into internal state. Layout: for point i,
/// src[i*5 + 0..5] = x, y, cat, status, lat. Returns number of points changed.
#[no_mangle]
pub unsafe extern "C" fn viz_ingest(
    src: *const f32, count: c_int, changed_out: *mut c_int,
) -> c_int {
    STATE.with(|s| {
        let mut s = s.borrow_mut();
        let count = (count.max(0) as usize).min(MAX_POINTS);
        let mut changed = 0;
        for i in 0..count {
            let x = *src.add(i * 5);
            let y = *src.add(i * 5 + 1);
            let cat = *src.add(i * 5 + 2);
            let status = *src.add(i * 5 + 3) as i32;
            let lat = *src.add(i * 5 + 4);
            let base = i * STRIDE;
            let phase = status_phase(status);
            let target = status_alpha(status, lat);
            let d = &mut s.data;
            let differs = d[base] != x
                || d[base + 1] != y
                || d[base + 2] != cat
                || d[base + 3] != status as f32
                || d[base + 4] != lat
                || d[base + 5] != phase
                || d[base + 6] != target;
            if differs {
                d[base] = x;
                d[base + 1] = y;
                d[base + 2] = cat;
                d[base + 3] = status as f32;
                d[base + 4] = lat;
                d[base + 5] = phase;
                d[base + 6] = target;
                changed += 1;
            }
        }
        if s.n != count {
            s.n = count;
        }
        if changed > 0 {
            s.dirty = true;
        }
        if !changed_out.is_null() {
            *changed_out = changed as c_int;
        }
        changed as c_int
    })
}

/// Layout helper: golden-angle spiral positions for n points into dst
/// (dst[i*2], dst[i*2+1] in [0,1]^2). Deterministic, stable across runs.
#[no_mangle]
pub unsafe extern "C" fn viz_layout_spiral(dst: *mut f32, n: c_int) {
    let n = (n.max(0) as usize).min(MAX_POINTS);
    if n == 0 {
        return;
    }
    let golden = 2.39996322972865332; // golden angle in radians
    let nf = n as f64;
    for i in 0..n {
        let fi = (i + 1) as f64;
        let r = (fi / nf).sqrt().min(1.0); // unit disc radius
        let theta = fi * golden;
        *dst.add(i * 2) = (0.5 + r * 0.48 * theta.cos()) as f32;
        *dst.add(i * 2 + 1) = (0.5 + r * 0.48 * theta.sin()) as f32;
    }
}

// ---- scratch allocator for JS->wasm bulk data transfer ----

struct Scratch {
    buf: Vec<u8>,
    offset: usize,
}

struct ScratchState {
    blocks: Vec<Block>,
}

thread_local! {
    static SCRATCH: RefCell<ScratchState> = RefCell::new(ScratchState { blocks: Vec::new() });
}

/// Allocate a scratch buffer of at least `size` bytes; returns offset.
/// Bump allocator over a NON-MOVING arena: growth replaces the backing store
/// but previously returned offsets stay stable because we allocate the arena
/// as disjoint fixed-size blocks chained together (no memcpy on grow).
#[no_mangle]
pub extern "C" fn viz_scratch_alloc(size: u32) -> *mut u8 {
    SCRATCH.with(|s| {
        let mut s = s.borrow_mut();
        let need = size as usize;
        let existing = s.blocks.iter_mut().find(|b| b.len - b.used >= need);
        if let Some(b) = existing {
            let p = unsafe { b.ptr.add(b.used) };
            b.used += need;
            return p;
        }
        let cap = need.max(65536);
        let mut v = Vec::with_capacity(cap);
        let p = v.as_mut_ptr();
        v.resize(cap, 0);
        s.blocks.push(Block { ptr: p, len: cap, used: need, _v: v });
        p
    })
}

struct Block {
    ptr: *mut u8,
    len: usize,
    used: usize,
    // keep the allocation alive as long as the block lives
    _v: Vec<u8>,
}

/// Rewind the scratch arena (call after the frame's data has been consumed or
/// copied). Contents become invalid — do not hold pointers across frames.
#[no_mangle]
pub extern "C" fn viz_scratch_reset() {
    SCRATCH.with(|s| {
        for b in s.borrow_mut().blocks.iter_mut() {
            b.used = 0;
        }
    });
}

/// Pointer to the palette upload region (n_cats * 3 bytes, max 48).
#[no_mangle]
pub extern "C" fn viz_palette_ptr() -> *mut u8 {
    viz_scratch_alloc(48)
}

/// Expose wasm memory so JS can write into scratch buffers directly.
#[no_mangle]
pub extern "C" fn viz_memory() -> *const () {
    // JS reads instance.exports.memory anyway; this export exists for symmetry.
    // wasm linear memory base is at offset 0 of the memory object; returning a
    // dummy pointer is useless — JS uses WebAssembly.Memory directly.
    std::ptr::null()
}

/// Number of points currently active.
#[no_mangle]
pub extern "C" fn viz_point_count() -> c_int {
    STATE.with(|s| s.borrow().n as c_int)
}

#[no_mangle]
pub extern "C" fn viz_dirty_count() -> c_int {
    0 // reserved: JS tracks changed count from ingest return
}

/// Memory size helpers for JS-side typed-array views.
#[no_mangle]
pub extern "C" fn viz_max_points() -> c_int {
    MAX_POINTS as c_int
}

#[no_mangle]
pub extern "C" fn viz_ingest_stride() -> c_int {
    5 // x, y, cat, status, lat
}
