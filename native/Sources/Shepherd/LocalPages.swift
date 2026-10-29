import SwiftUI

/// One page added by a local module. The list comes from `LocalRegistry.pages`; the public build has none.
/// `after` is the engine key of the built-in page this entry follows in the sidebar.
struct LocalPage: Identifiable {
    let id: String
    let title: String
    let icon: String
    let after: String
    let view: () -> AnyView
}

/// Where the sidebar is pointing: a built-in page or a local page.
enum Destination: Hashable {
    case page(Page)
    case local(String)
}
