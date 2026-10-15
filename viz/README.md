# dnsbench-viz — WASM point-cloud renderer

Renders up to 65,536 resolver candidates as a spatial field on Canvas2D,
driven from `static/app.js` via WebAssembly. Dirty-driven: frames are only
drawn when data or layout actually changed; idle costs nothing.

## Layout

Per-point float record, STRIDE = 7: x, y, cat(f32), status, lat_ms, phase,
target_alpha. JS ingests the dataset once (golden-spiral layout in
`viz_layout_spiral`), then live SSE updates mutate individual points in place.

## JS-facing exports

- `viz_resize(w, h)`, `viz_tick(dt) -> changed`, `viz_is_dirty()`,
  `viz_clear_dirty()`, `viz_render() -> n_drawn`
- `viz_set_palette(ptr, n_cats)`, `viz_palette_ptr()`
- `viz_scratch_alloc(bytes)` — bump arena over chained blocks (see caveat)
- `viz_ingest(ptr, n)`, `viz_layout_spiral(dst, n)`

## Scratch-allocator caveat (fixed at v1.0.0 of the crate)

`viz_scratch_alloc` can trigger `memory.grow`, which DETACHES existing JS
TypedArrays over wasm memory. All scratch must be allocated BEFORE creating
any views (`app.js` does this; keep that order if you touch the ingest path).

## Build (macOS, Homebrew rust toolchain quirks)

Homebrew's rust lacks the `wasm32-unknown-unknown` std — install via rustup:

    rustup target add wasm32-unknown-unknown

Homebrew rustc's broken rpath crashes rust-lld with SIGABRT/SIGSEGV; the
working build pins the rustup toolchain, points DYLD_LIBRARY_PATH at its lib
dir, and gives rustc a huge stack:

    export RUSTC="$HOME/.rustup/toolchains/<ver>-aarch64-apple-darwin/bin/rustc"
    export RUST_MIN_STACK=4294967296
    export DYLD_LIBRARY_PATH="$HOME/.rustup/toolchains/<ver>-aarch64-apple-darwin/lib"
    export RUSTFLAGS="-C link-arg=--allow-undefined"
    cargo build --release --target wasm32-unknown-unknown
    cp target/wasm32-unknown-unknown/release/dnsbench_viz.wasm ../static/viz.wasm

A prebuilt `static/viz.wasm` (39 KB) is committed, so building is only needed
after editing `src/lib.rs`.
