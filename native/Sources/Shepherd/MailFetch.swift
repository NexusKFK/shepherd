import SwiftUI

/// 「收取邮件」按钮和上次收取的状态行；持仓页和成交与复盘页共用。
/// 点一下让邮件收取任务现在跑一轮：Mail 没开会在后台拉起，把券商邮件全读进来、标已读，读完了就把 Mail 关掉（它只用来给交易留档；有人正在用它时不关）。读进来的成交由自动入账写进账本，所以点完之后每 20 秒刷新一次，共 5 次。
struct MailFetchControl: View {
    @EnvironmentObject var model: DeskModel
    var mail: J { model.state["econf"]["mail"] }
    var failed: Bool { (mail["exit"].n ?? 0) > 0 }
    var body: some View {
        // 收取任务是本机自建的 launchd 任务；没装就不显示按钮，免得点了只会报错
        if mail["installed"].b { control }
    }
    var control: some View {
        HStack(spacing: 8) {
            if !mail["text"].s.isEmpty {
                Text(mail["text"].s).font(.caption).foregroundStyle(failed ? PortfolioStyle.warn : Color.secondary)
                    .lineLimit(1).truncationMode(.tail)
            }
            Button("收取邮件") { fetch() }.disabled(mail["running"].b)
                .help("把券商邮件全读一遍并标已读；Mail 没开会先在后台拉起，读完关掉")
        }
    }
    func fetch() {
        model.action("econf-fetch")
        Task { for _ in 0..<5 { try? await Task.sleep(nanoseconds: 20_000_000_000); model.action("econf-scan") } }
    }
}
