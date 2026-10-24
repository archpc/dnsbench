DNSBench icon — design research summary (verified against official sources)

SOURCES (fetched directly, full text in this directory)
- developer.apple.com/documentation/xcode/creating-your-app-icon-using-icon-composer.md (fetched 2026-10-16, copyright 2026)
- developer.apple.com HIG app-icons JSON (tutorials/data endpoint, HTTP 200)
- developer.apple.com/icon-composer (product page, via search)
- WWDC25 361 "Create icons with Icon Composer" (transcript via search)
- WWDC25 362 "Say hello to the new look of app icons" (notes via search)

MANDATORY REQUIREMENTS (from official docs)
1. Canvas 1024x1024 px, square, unmasked. System applies rounded-rect mask.
   Pre-masking "negatively impacts specular highlights and makes edges jagged". (HIG, Icon shape)
2. Background + one or more foreground layers, rendered back-to-front in z. (HIG Layer design)
3. Source art flat, opaque, simple. No baked blurs, shadows, specular, translucency,
   background colors/gradients — all applied in Icon Composer. (Xcode doc "Prepare your artwork for export")
4. Prefer vector SVG layers; PNG only for gradients/raster/mesh. Text must be outlined. (Xcode doc)
5. Max 4 groups in Icon Composer; groups = depth planes; Individual vs Combined material mode. (Xcode doc)
6. Clearly defined (non-feathered) edges on foreground shapes. (HIG)
7. Keep primary content centered; content near edges gets truncated by mask. (HIG)
8. Simplicity: minimal shapes, simple background; fine features look busy and fail at small size. (HIG)
9. No thin line weights / sharp corners — lose detail at small sizes. (HIG)
10. No baked system effects (drop shadows, bevels, glows). (HIG Visual effects)
11. Appearance variants: Default, Dark, Mono (clear light/dark, tinted light/dark previews).
    Mono needs at least one white element; other colors map to gray tones. (WWDC25-361)
12. Dark variant: adjust fills for legibility against black (e.g. Dictionary's maroon example). (WWDC25-361)
13. .icon file added to Xcode replaces AppIcon asset catalog; App Icon field must match
    filename without extension; Xcode generates fallbacks for earlier OS at build time. (Xcode doc)

RECOMMENDATIONS (not mandatory)
- Filled, overlapping shapes give depth when paired with transparency/blur. (HIG)
- Vary opacity between layers for liveliness; import opaque, tune opacity in Composer. (HIG)
- Solid/gradient background via Composer canvas fill (no background image import needed). (Xcode doc)
- Chromatic shadows look good over white backgrounds; neutral shadows for dark/mono. (WWDC25-361)
- Specular: Automatic default; turn Off per group when thin shapes go "pillowy". (WWDC25-361)
- Refraction: macOS 27+ only (no visible effect pre-27). (Xcode doc)
- Numbered layer filenames (increment back to front) organize z-order on import. (Xcode doc)
- Grid templates from Apple Design Resources (Figma/Sketch/PS/AI). (Xcode doc)
- Avoid: globe/Wi-Fi/terminal clichés (our judgment), text (HIG discourages), UI replicas.

PLATFORM FACTS
- macOS 27 icons: same 1024 canvas as iOS; system masks to rounded rect; Liquid Glass material.
- Icon Composer lives at /Applications/Xcode.app/Contents/Applications/Icon Composer.app (verified on this machine, Xcode 27.1).
- Composer UI: sidebar (groups/layers), canvas (platform + appearance selectors, grid, sizes, Effects on/off, backgrounds), Style inspector (Color / Liquid Glass / Composition).
