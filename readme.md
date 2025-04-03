ETH Arbitrage Analyzer
基于 ERC-20 Token 日志的以太坊套利路径识别与分析工具

本项目旨在从以太坊链上真实交易（以区块 #17518743 为例）中，识别潜在套利路径，分析 MEV 收益结构，并实现可视化呈现。

🔍 功能亮点
路径图自动构建：从 ERC-20 日志中提取资金流向，构建 Token → Token 有向图。

潜在 DEX 识别：通过 in/out Token 分析判断地址是否为交换节点，支持 DEX 路由识别。

套利路径挖掘：基于 networkx 自动检测闭环交易结构（如三角套利路径）。

收益估算与归属：支持 Token 数量追踪，结合 gas 成本计算套利者与矿工收益。

Etherscan / MEV Explore 验证：可与链上数据交叉对比，验证套利路径准确性。

图表可视化：使用 matplotlib 绘制 DEX 使用频率、代币利润分布、套利者收益等图形。

示例结果（区块 #17518743）
共识别出 8 条套利路径，全部为典型三角套利结构。

实证路径：WETH → FINALE → LOYAL → WETH，净赚约 0.0032 ETH

套利者与矿工收益近似 1:1，符合文献中“套利收益高度回流矿工”的 MEV 研究结论。

数据输入
block_17518743_transfers.json：ERC-20 转账事件

block_17518743_receipts.json：交易收据（含 gasUsed / effectiveGasPrice）

自动生成：

block_17518743_movements.json

potential_exchanges.json

enhanced_arbitrage_analysis_results.json

可视化输出
dex_usage.png：DEX 使用频率

profit_taker_distribution.png：套利者分布

token_profits.png：代币盈亏分布

token_graph.png：Token 交换路径图（Directed Graph）

代码结构
src/：主要逻辑模块，包括 get_addr_to_movements、get_potential_exchanges 等

test_*.py：每个功能单元的验证脚本

enhanced_arbitrage_analysis.py：一键运行主程序，生成 JSON + 可视化结果

参考文献
A Large-Scale Study of the Ethereum Arbitrage Ecosystem 
goldphish: https://github.com/ucsb-seclab/goldphish
MEV Explore 实际链上验证