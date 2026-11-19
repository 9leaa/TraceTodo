// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "TraceTodo",
    platforms: [
        .macOS(.v14),
        .iOS(.v17)
    ],
    products: [
        .executable(name: "TraceTodo", targets: ["TraceTodo"])
    ],
    targets: [
        .executableTarget(
            name: "TraceTodo",
            path: "Sources/TraceTodo"
        )
    ],
    swiftLanguageModes: [.v5]
)
