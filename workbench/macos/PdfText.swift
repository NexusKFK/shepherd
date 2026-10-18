// 确认单 PDF 文本提取器：PDFKit 抽全文后打到 stdout，供 workbench/econf.py 调用。
// 由 build-workbench.sh 编译进 App（Contents/MacOS/pdf_text），仓库内也可手动 swiftc 使用。
import Foundation
import PDFKit

guard CommandLine.arguments.count >= 2 else {
    FileHandle.standardError.write("用法：pdf_text <文件.pdf>\n".data(using: .utf8)!)
    exit(2)
}
guard let doc = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])) else {
    FileHandle.standardError.write("打不开这个 PDF\n".data(using: .utf8)!)
    exit(1)
}
var out = ""
for i in 0..<doc.pageCount {
    out += doc.page(at: i)?.string ?? ""
    out += "\n"
}
FileHandle.standardOutput.write(out.data(using: .utf8)!)
