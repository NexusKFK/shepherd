# 安全说明 · Security

Shepherd 是本机 App：不监听网络端口，不登录券商，不向券商下单，不调用 AI 模型，也不上传任何数据。账本是 `~/Library/Application Support/Shepherd/TradingDesk/trading.sqlite3`，只有你的用户能读。界面和计算引擎之间通过私有的标准输入输出管道通信。

如果你发现安全问题（例如会读写约定位置以外的文件、导入文件能让程序执行代码），请不要公开发 issue，用 GitHub 的 [Private vulnerability reporting](../../security/advisories/new) 私下报告。

Shepherd runs locally: no listening sockets, no broker login or orders, no AI calls, no uploads. Please report security issues privately through GitHub's private vulnerability reporting instead of a public issue.
