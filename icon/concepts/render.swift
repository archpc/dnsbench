import AppKit

// Composite SVG-rasterized layers, apply approximate macOS 27 rounded-rect mask,
// and emit per-concept size-comparison sheets on light and dark backgrounds.
// Usage: render.swift <outDir> <conceptName>:<dir> ...

let outDir = CommandLine.arguments[1]
let specs = CommandLine.arguments[2...]

let sizes = [1024, 256, 64, 32, 16]
let maskRatio: CGFloat = 0.2247 // approximate unified-grid corner radius fraction

func loadPNG(_ path: String) -> NSImage? { NSImage(contentsOfFile: path) }

func compositeIcon(_ bg: NSImage, _ fg: NSImage) -> NSImage {
    let img = NSImage(size: NSSize(width: 1024, height: 1024))
    img.lockFocus()
    bg.draw(in: NSRect(x: 0, y: 0, width: 1024, height: 1024))
    fg.draw(in: NSRect(x: 0, y: 0, width: 1024, height: 1024))
    img.unlockFocus()
    return img
}

func masked(_ icon: NSImage, size S: Int) -> NSImage {
    let img = NSImage(size: NSSize(width: S, height: S))
    img.lockFocus()
    let r = CGFloat(S) * maskRatio
    let path = NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: CGFloat(S), height: CGFloat(S)),
                            xRadius: r, yRadius: r)
    path.addClip()
    icon.draw(in: NSRect(x: 0, y: 0, width: CGFloat(S), height: CGFloat(S)))
    img.unlockFocus()
    return img
}

var sheets: [String: [NSImage]] = [:]
for spec in specs {
    let parts = spec.split(separator: ":", maxSplits: 1)
    let name = String(parts[0]), dir = String(parts[1])
    let files = try! FileManager.default.contentsOfDirectory(atPath: dir).filter { $0.hasSuffix(".png") }.sorted()
    let img = NSImage(size: NSSize(width: 1024, height: 1024))
    img.lockFocus()
    for f in files {
        if let layer = loadPNG(dir + "/" + f) {
            layer.draw(in: NSRect(x: 0, y: 0, width: 1024, height: 1024))
        }
    }
    img.unlockFocus()
    sheets[name] = sizes.map { masked(img, size: $0) }
    print("rendered \(name)")
}

// sheets: one PNG per concept: row1 on white, row2 on dark gray
let cell: CGFloat = 360, pad: CGFloat = 40
for (name, imgs) in sheets {
    let W = CGFloat(sizes.count) * cell + pad * 2
    let H = cell * 2 + 120
    let sheet = NSImage(size: NSSize(width: W, height: H))
    sheet.lockFocus()
    NSColor.white.setFill()
    NSRect(x: 0, y: cell + 100, width: W, height: cell).fill()
    NSColor(calibratedWhite: 0.10, alpha: 1).setFill()
    NSRect(x: 0, y: 0, width: W, height: cell + 100).fill()
    for (i, im) in imgs.enumerated() {
        let s = CGFloat(sizes[i])
        // light row (top)
        let scaleL = min(280 / s, 1)
        let drawL = s * scaleL
        im.draw(in: NSRect(x: pad + CGFloat(i) * cell + (cell - drawL) / 2,
                           y: cell + 100 + (cell - drawL) / 2, width: drawL, height: drawL))
        // dark row (bottom)
        let drawD: CGFloat = min(CGFloat(s), 280)
        im.draw(in: NSRect(x: pad + CGFloat(i) * cell + (cell - drawD) / 2,
                           y: 40 + (cell - drawD) / 2, width: drawD, height: drawD))
        let label = "\(s)px"
        label.draw(at: NSPoint(x: pad + CGFloat(i) * cell + cell / 2 - 20, y: cell + 105),
                   withAttributes: [.font: NSFont.systemFont(ofSize: 13), .foregroundColor: NSColor.darkGray])
    }
    name.draw(at: NSPoint(x: pad, y: H - 30), withAttributes: [.font: NSFont.boldSystemFont(ofSize: 18), .foregroundColor: NSColor.labelColor])
    sheet.unlockFocus()
    let rep = NSBitmapImageRep(data: sheet.tiffRepresentation!)!
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: outDir + "/concept-\(name).png"))
    print("wrote concept-\(name).png")
}
