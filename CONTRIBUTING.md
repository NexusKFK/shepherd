# 参与贡献 · Contributing

欢迎提 issue 和 pull request。中文、英文都可以。

## 开发

```sh
python3 -m unittest discover -p 'test_*.py'   # 计算引擎的全部测试
./build-workbench.sh                           # 编译 App（需要 python.org 的 framework 版 Python 3.10+）
python3 -m workbench.demo --out /tmp/demo      # 生成一套演示数据
```

- 界面在 `native/Sources/Shepherd/`（SwiftUI），计算都在 `workbench/`（Python 标准库，无第三方依赖）。新的计算请放在 Python 里并配测试，界面只负责显示。
- 数据缺失或损坏时要明确显示"不可用"，不要把失败显示成"没有信号"或空列表。
- 改动账本写入逻辑时，测试必须用临时目录里的隔离账本。

## 翻译

界面文字在代码里写简体中文，显示时查 `workbench/i18n/<语言>.json` 翻成英文或繁体中文。改了界面文字以后：

```sh
python3 tools/i18n_extract.py .      # 重新收集要翻译的原文，写到 workbench/i18n/source.json
python3 tools/i18n_check.py en       # 检查英文表：缺几条、占位符和换行对不对得上
python3 tools/i18n_check.py zh-Hant  # 检查繁体表
```

拼接出来的句子请写成一整句带占位符的字符串（例如 `"第 \(n) 天（\(date)）"`），不要用 `+` 把几段连起来，否则翻译表只能拿到碎片。

## 测试数据必须是虚构的

测试、演示数据、截图和文档里不能出现任何真实账户、持仓、成交、净值或个人信息。请用明显虚构的数字。

## Pull request

一个 PR 只做一件事，写清楚改了什么、为什么改、怎么验证的。提交即表示你同意按 GPL-3.0 发布你的改动。

---

Issues and pull requests are welcome in Chinese or English. Run the tests above; after changing UI text, run `tools/i18n_extract.py` and `tools/i18n_check.py` and add the English and Traditional Chinese entries; keep calculations in `workbench/` with tests, use invented data only in tests and examples, and keep one change per pull request. Contributions are released under GPL-3.0.
