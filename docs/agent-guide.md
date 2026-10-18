# 和 coding agent 一起用 Shepherd

[English](agent-guide.en.md)

Shepherd 自己不联网取数，也不调用 AI。它的输入都是约定格式的 JSON 文件，App 只读不改。这种结构正好适合交给 coding agent（例如 Claude Code、Codex、Cursor）：agent 写文件和脚本，Shepherd 算账和显示，最后由你拍板。

作者自己就是这么用的：持仓快照、日线、宏观灯、周线观察、纸盘的数据都由 agent 写的脚本每天生成，期权计划卡也常让 agent 先起草。

## 分工

| 谁 | 做什么 |
|---|---|
| 你 | 定 1R，决定买什么、买多少，在券商下单，在 App 里确认计划和成交。 |
| Agent | 写数据文件和采集脚本，起草期权计划卡，读账本帮你复盘，改 App 的代码。 |
| Shepherd | 读这些文件，算数量、热度和风险。文件缺了或坏了，页面会写明，不会假装正常。 |

## 三条边界

1. **账本只通过 App 写。** 交易计划、成交和 1R 都要你在 App 里确认。Agent 只写「数据源目录」里的文件（位置见 [data-formats.md](data-formats.md)）。它要读账本时，用只读方式打开：

   ```sh
   sqlite3 -readonly ~/Library/Application\ Support/Shepherd/TradingDesk/trading.sqlite3 '.tables'
   ```

2. **不下单。** Shepherd 没有下单功能。也不要让 agent 替你在券商下单；它可以起草指令，由你自己下。
3. **持仓数据不出本机。** 数据文件里是你的真实账户和持仓。不要让 agent 把它们提交到任何公开仓库，也不要贴到网上。

## 常见的合作方式

### 1. 把券商的持仓导出整理成快照

把券商导出的持仓 CSV 和 [`examples/holdings-template.json`](../examples/holdings-template.json) 一起交给 agent，让它转换成同样格式的 JSON。可以写成脚本，以后每天跑一次。

先检查，再在 App 里「数据与留存 → 导入持仓 JSON」导入：

```sh
python3 tools/check_sources.py --holdings 你的持仓.json
```

### 2. 补日线

风险页的 Beta、大盘情景和 VaR 要用日线。可以让 agent 把下面这条命令挂到 launchd 上，每个交易日收盘后跑一次：

```sh
python3 tools/fetch_market_data.py AAPL MSFT NVDA
```

### 3. 起草期权计划卡

你说想法：标的、方向、大概的到期日、这笔最多亏多少 R。Agent 用真实报价挑行权价，按 [data-formats.md 里的计划卡格式](data-formats.md#期权计划卡) 写一个 JSON 文件，放进期权计划文件夹。

App 的「期权计划」页会马上画出这张卡的盈亏图。你在图上核对最大亏损、盈亏平衡价和到期前的曲线，再决定下不下单。成交以后，把卡挪进子文件夹就不再显示。

### 4. 给可选模块写数据脚本

宏观灯、周线观察、纸盘、盘前观察和日历各读一个 JSON 文件，格式都在 [data-formats.md](data-formats.md)。最完整的样例是演示数据：

```sh
python3 -m workbench.demo --out /tmp/shepherd-demo    # 样例在 /tmp/shepherd-demo/sources/
```

让 agent 照着样例写一个脚本，从你信任的数据源生成文件，再挂到 launchd 上定时跑。判断规则（比如宏观灯什么时候变红）由你定，agent 负责实现和回测。

### 5. 复盘

让 agent 只读账本，按计划汇总成交、每笔结果是多少 R、有没有超出计划的 1R。结论写进你自己的交易日志，不写回账本。

### 6. 改 App 本身

仓库根目录的 [AGENTS.md](../AGENTS.md) 写了给 agent 的开发规则：计算放在 Python 引擎里并配测试，界面文字要能翻译，测试只用虚构数据。

## 每次写完都检查

```sh
python3 tools/check_sources.py                      # App 默认读的位置
python3 tools/check_sources.py --source-home 某目录   # 先写到测试目录里检查
```

每个数据源一行：OK、STALE（能读但过期）、MISSING（没有文件，不用的模块可以不管）、BAD（有文件但 App 用不了）。只要有 BAD，命令就以错误码退出，agent 可以自己发现并改正。

## 可以直接复制的开场白

```
这个项目是 Shepherd（github.com/NexusKFK/shepherd），一个以损定仓的 macOS 交易工作台。
先读仓库里的 AGENTS.md 和 docs/data-formats.md。

我要你：<在这里写任务>

规则：
- 只写数据源目录里的文件，不要改账本 trading.sqlite3；需要读账本时用 sqlite3 -readonly。
- 写完运行 python3 tools/check_sources.py，全部不是 BAD 再告诉我结果。
- 不要把我的持仓或账户数据提交到任何仓库。
```

几个任务的写法：

- 「把这个 CSV 转成 Shepherd 的持仓快照，写一个以后每天能重复跑的脚本。」
- 「帮我起草一张期权计划卡：标的 DE，看涨，11 月到期，最多亏 0.5R。用现在的真实报价选行权价。」
- 「写一个脚本生成周线观察文件，标的是 SPY 和 QQQ，每周六早上 8 点跑。」
- 「只读账本，列出这个月每笔已平仓交易的结果（按 R），以及哪几笔超过了计划的 1R。」
