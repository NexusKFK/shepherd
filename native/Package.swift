// swift-tools-version: 5.9
import PackageDescription
let package = Package(
    name: "Shepherd", platforms: [.macOS(.v14)],
    products: [.executable(name: "Shepherd", targets: ["Shepherd"])],
    targets: [.executableTarget(name: "Shepherd")]
)
