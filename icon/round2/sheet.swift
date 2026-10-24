import AppKit

// Grid sheet: 8 columns (concept x palette) x 5 size rows, masked tiles.
let dir = CommandLine.arguments[1]
let cols: [(String, String)] = [
    ("lanes dark", "lanes_dark"), ("lanes light", "lanes_light"),
    ("sonar dark", "sonar_dark"), ("sonar light", "sonar_light"),
    ("cards dark", "cards_dark"), ("cards light", "cards_light"),
    ("trace dark", "trace_dark"), ("trace light", "trace_light"),
]
let sizes = [256, 64, 32, 16]
let maskRatio: CGFloat = 0.2247
let cell: CGFloat = 250, label: CGFloat = 26
let W = CGFloat(cols.count) * cell + 20
let H = CGFloat(sizes.count) * (cell + label) + 60

let sheet = NSImage(size: NSSize(width: W, height: H))
sheet.lockFocus()
NSColor(calibratedWhite: 0.13, alpha: 1).setFill()
NSRect(x: 0, y: 0, width: W, height: H).fill()

for (ci, spec) in cols.enumerated() {
    let files = try! FileManager.default.contentsOfDirectory(atPath: dir + "/" + spec.1)
        .filter { $0.hasSuffix(".png") }.sorted()
    let icon = NSImage(size: NSSize(width: 1024, height: 1024))
    icon.lockFocus()
    for f in files { NSImage(contentsOfFile: dir + "/" + spec.1 + "/" + f)?.draw(in: NSRect(x: 0, y: 0, width: 1024, height: 1024)) }
    icon.unlockFocus()
    // row 0 = 1024 scaled down to cell; rows 1.. = native sizes
    let allSizes: [CGFloat] = [CGFloat(cell - 20)] + sizes.map { CGFloat($0) }
    for (ri, s) in allSizes.enumerated() {
        let t = NSImage(size: NSSize(width: Int(s), height: Int(s)))
        t.lockFocus()
        NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: s, height: s), xRadius: s * maskRatio, yRadius: s * maskRatio).addClip()
        icon.draw(in: NSRect(x: 0, y: 0, width: s, height: s))
        t.unlockFocus()
        let maxH = ri == 0 ? cell - 20 : s
        let x = 20 + CGFloat(ci) * cell + (cell - maxH) / 2
        let yTop = H - 30 - CGFloat(ri) * (cell + label) - (ri == 0 ? maxH : cell / 2 - CGFloat(s) / 2)
        t.draw(in: NSRect(x: x, y: yTop, width: maxH, height: maxH))
    }
    (spec.0 as NSString).draw(at: NSPoint(x: 20 + CGFloat(ci) * cell + 20, y: 16),
        withAttributes: [.font: NSFont.systemFont(ofSize: 15), .foregroundColor: NSColor.white])
}
let sizeLabels = ["1024", "256", "64", "32", "16"]
for (ri, l) in sizeLabels.enumerated() {
    let y = H - 30 - CGFloat(ri) * (cell + label) - (ri == 0 ? 24 : 12)
    (l as NSString).draw(at: NSPoint(x: 2, y: y),
        withAttributes: [.font: NSFont.systemFont(ofSize: 12), .foregroundColor: NSColor.gray])
}
sheet.unlockFocus()
let rep = NSBitmapImageRep(data: sheet.tiffRepresentation!)!
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: dir + "/sheet.png"))
print("wrote sheet")
