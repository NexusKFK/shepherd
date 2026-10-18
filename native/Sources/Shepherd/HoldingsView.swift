import SwiftUI

struct HistoricalSnapshotView: View {
    let snapshot: J
    @EnvironmentObject var model: DeskModel
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack { Text("历史持仓快照").font(.title2.bold()); Spacer(); Button("关闭") { model.historical = nil } }
            Text(snapshot["asof"].s).foregroundStyle(.secondary)
            Text("只读历史记录，不代表当前账户；查看不会覆盖现在的持仓。") .font(.callout).foregroundStyle(PortfolioStyle.warn)
            Table(snapshot["positions"].items) {
                TableColumn("标的") { Text($0["sym"].s) }
                TableColumn("账户") { Text($0["broker"].s) }
                TableColumn("合约") { Text($0["label"].s) }
                TableColumn("数量") { Text(num($0["qty"])) }
                TableColumn("成本") { Text(num($0["open"])) }
            }
        }.padding(24).frame(width: 850, height: 560)
    }
}
