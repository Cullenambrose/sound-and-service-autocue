// Original BOOTH BUDDY artwork, drawn with AppKit. No external assets or Metal.
import AppKit
import Foundation
let output = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        bitmap.size = NSSize(width: pixels, height: pixels)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        let context = NSGraphicsContext.current!.cgContext
        context.scaleBy(x: CGFloat(pixels) / 1024, y: CGFloat(pixels) / 1024)
        let rect = NSRect(x: 80, y: 80, width: 864, height: 864)
        let background = NSBezierPath(roundedRect: rect, xRadius: 195, yRadius: 195)
        let gradient = NSGradient(starting: NSColor(srgbRed: 0.025, green: 0.22, blue: 0.23, alpha: 1), ending: NSColor(srgbRed: 0.04, green: 0.47, blue: 0.39, alpha: 1))!
        gradient.draw(in: background, angle: 45)
        NSColor(srgbRed: 0.45, green: 0.98, blue: 0.79, alpha: 1).setFill()
        // Five level bars form a compact booth/equalizer mark.
        for (index, height) in [180.0, 340, 470, 340, 180].enumerated() {
            NSBezierPath(roundedRect: NSRect(x: 250 + Double(index) * 112, y: 512 - height / 2, width: 72, height: height), xRadius: 36, yRadius: 36).fill()
        }
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        try bitmap.representation(using: .png, properties: [:])!.write(to: output.appendingPathComponent("icon_\(size)x\(size)\(suffix).png"))
    }
}
