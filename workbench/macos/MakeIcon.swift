import AppKit
let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let sourceURL = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("AppIconSource.png")
guard let source = NSImage(contentsOf: sourceURL) else {
    FileHandle.standardError.write("无法读取图标源图 AppIconSource.png\n".data(using: .utf8)!)
    exit(1)
}
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
// macOS 图标网格: 1024 画布中圆角方块占 824, 居中留白
let contentRatio = CGFloat(824) / 1024
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let image = NSImage(size: NSSize(width: pixels, height: pixels))
        image.lockFocus()
        let content = CGFloat(pixels) * contentRatio
        let origin = (CGFloat(pixels) - content) / 2
        NSGraphicsContext.current?.imageInterpolation = .high
        source.draw(in: NSRect(x: origin, y: origin, width: content, height: content),
                    from: .zero, operation: .copy, fraction: 1)
        image.unlockFocus()
        let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
        let name = "icon_\(size)x\(size)" + (scale == 2 ? "@2x" : "") + ".png"
        try bitmap.representation(using: .png, properties: [:])!.write(to: folder.appendingPathComponent(name))
    }
}
