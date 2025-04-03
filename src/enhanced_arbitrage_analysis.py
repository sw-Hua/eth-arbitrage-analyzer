import os
import sys
import json
import logging
import networkx as nx
import matplotlib.pyplot as plt
from typing import List, Dict, Optional, Set
from collections import defaultdict

# 配置日志
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# 已知的DEX路由器地址
KNOWN_DEX_ROUTERS = {
    '0x7a250d5630b4cf539739df2c5dacb4c659f2488d': 'Uniswap V2 Router',
    '0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45': 'Uniswap V3 Router',
    '0xe592427a0aece92de3edee1f18e0157c05861564': 'Uniswap V3 Router',
    '0x1b02da8cb0d097eb8d57a175b88c7d8b47997506': 'SushiSwap Router',
    '0x8c4b866f3c9f9cc5ef62a5a6ced0a12d95b7b9b5': 'SushiSwap Router'
}

# 常见代币符号
COMMON_TOKENS = {
    '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2': 'WETH',
    '0xC7a2572fA8FDB0f7E81d6D3c4e3CCF78FB0DC374': 'FINALE',
    '0x511686014F39F487E5CDd5C37B4b37606B795ae3': 'LOYAL'
}

def hex_to_bytes(hex_str: str) -> bytes:
    """将十六进制字符串转换为字节"""
    if hex_str.startswith('0x'):
        hex_str = hex_str[2:]
    return bytes.fromhex(hex_str)

def get_addr_to_movements(txns: List[Dict]) -> Dict[str, Dict]:
    """获取地址的转入转出记录"""
    addr_to_movements = defaultdict(lambda: {'in': [], 'out': []})
    
    for txn in txns:
        from_addr = txn['args']['from'].lower()
        to_addr = txn['args']['to'].lower()
        value = int(txn['args']['value'])
        
        addr_to_movements[from_addr]['out'].append({
            'address': txn['address'],
            'value': value,
            'args': txn['args']
        })
        
        addr_to_movements[to_addr]['in'].append({
            'address': txn['address'],
            'value': value,
            'args': txn['args']
        })
        
    return addr_to_movements

def get_potential_exchanges(full_txn: Dict, addr_to_movements: Dict) -> Set[str]:
    """识别潜在的交易所地址"""
    potential_exchanges = set()
    
    for addr, movements in addr_to_movements.items():
        if len(movements['in']) > 0 and len(movements['out']) > 0:
            potential_exchanges.add(addr)
            
    return potential_exchanges

def get_token_symbol(token_address: str) -> str:
    """获取代币符号"""
    return COMMON_TOKENS.get(token_address, token_address[:8] + '...')

def get_dex_name(dex_address: str) -> str:
    """获取DEX名称"""
    return KNOWN_DEX_ROUTERS.get(dex_address, dex_address[:8] + '...')

def get_arbitrage_from_receipt_if_exists(full_txn: Dict, txns: List[Dict]) -> Optional[Dict]:
    """分析交易收据中的套利机会"""
    # 获取地址的转账记录
    addr_to_movements = get_addr_to_movements(txns)
    
    # 识别潜在的交易所
    potential_exchanges = get_potential_exchanges(full_txn, addr_to_movements)
    
    if len(potential_exchanges) <= 1:
        return None
        
    # 构建代币流向图
    token_graph = nx.DiGraph()
    dex_usage = defaultdict(int)
    profits = defaultdict(float)
    
    # 记录每个交易所的转入转出代币
    for addr in potential_exchanges:
        ins = addr_to_movements[addr]['in']
        outs = addr_to_movements[addr]['out']
        
        in_coins = set(x['address'] for x in ins)
        out_coins = set(x['address'] for x in outs)
        
        if len(in_coins) != 1 or len(out_coins) != 1:
            continue
            
        in_token = next(iter(in_coins))
        out_token = next(iter(out_coins))
        
        # 计算转入转出金额
        in_amount = sum(float(x['value']) for x in ins)
        out_amount = sum(float(x['value']) for x in outs)
        
        # 添加到代币流向图
        token_graph.add_edge(in_token, out_token, 
                           weight=out_amount,
                           exchange=addr,
                           in_amount=in_amount,
                           out_amount=out_amount)
        
        # 统计 DEX 使用情况
        if addr in KNOWN_DEX_ROUTERS:
            dex_usage[KNOWN_DEX_ROUTERS[addr]] += 1
            
    # 寻找套利环路
    try:
        cycles = list(nx.simple_cycles(token_graph))
    except:
        return None
        
    if not cycles:
        return None
        
    # 分析每个环路
    arbitrage_paths = []
    for cycle in cycles:
        # 检查是否是回到同一个代币的环路
        if len(cycle) < 2 or cycle[0] != cycle[-1]:
            continue
            
        path = []
        start_token = cycle[0]
        start_amount = None
        end_amount = None
        
        # 计算环路中每条边的信息
        for i in range(len(cycle) - 1):  # -1 是因为最后一个节点是重复的
            from_token = cycle[i]
            to_token = cycle[i + 1]
            
            edge_data = token_graph.get_edge_data(from_token, to_token)
            if not edge_data:
                continue
                
            # 记录起始数量
            if i == 0:
                start_amount = edge_data['in_amount']
                
            # 记录结束数量
            if to_token == start_token and i == len(cycle) - 2:
                end_amount = edge_data['out_amount']
                
            path.append({
                'from_token': from_token,
                'from_token_symbol': get_token_symbol(from_token),
                'to_token': to_token,
                'to_token_symbol': get_token_symbol(to_token),
                'exchange': edge_data['exchange'],
                'dex_name': get_dex_name(edge_data['exchange']),
                'in_amount': edge_data['in_amount'],
                'out_amount': edge_data['out_amount']
            })
            
        # 只有当回到同一个代币且数量增加时才记录套利
        if start_amount is not None and end_amount is not None and end_amount > start_amount:
            profit = end_amount - start_amount
            profits[start_token] += profit
            
            arbitrage_paths.append({
                'path': path,
                'length': len(path),
                'profit': profit,
                'start_token': start_token,
                'start_amount': start_amount,
                'end_amount': end_amount
            })
        
    # 计算矿工收益
    gas_cost = int(full_txn['gasUsed']) * int(full_txn['effectiveGasPrice'])
    miner_revenue = gas_cost
    
    return {
        'transaction_hash': full_txn['transactionHash'],
        'block_number': full_txn['blockNumber'],
        'token_graph': token_graph,
        'dex_usage': dict(dex_usage),
        'arbitrage_paths': arbitrage_paths,
        'profits': dict(profits),
        'miner_revenue': miner_revenue,
        'gas_used': full_txn['gasUsed'],
        'gas_price': full_txn['effectiveGasPrice']
    }

class EnhancedArbitrageAnalysis:
    def __init__(self):
        self.token_graph = nx.DiGraph()  # 代币流向图
        self.dex_usage = defaultdict(int)  # DEX 使用频率
        self.path_lengths = []  # 套利路径长度
        self.token_profits = defaultdict(float)  # 各代币套利收益
        self.miner_revenue = 0  # 矿工收益
        self.arbitrage_count = 0  # 套利交易数量
        self.arbitrage_details = []  # 详细套利信息
        self.profit_takers = defaultdict(float)  # 套利者收益
        
    def analyze_transaction(self, receipt: Dict, transfers: List[Dict]) -> Optional[Dict]:
        """分析单笔交易"""
        # 将 receipt 中的字符串转换为正确的格式
        processed_receipt = {
            'transactionHash': receipt['transactionHash'],
            'blockNumber': int(receipt['blockNumber']),
            'gasUsed': int(receipt['gasUsed']),
            'effectiveGasPrice': int(receipt['effectiveGasPrice']),
            'from': receipt['from'],
            'to': receipt['to']
        }
        
        # 处理 transfers 中的 transactionHash
        processed_transfers = []
        for transfer in transfers:
            processed_transfer = {
                'address': transfer['address'],
                'transactionHash': transfer['transactionHash'],
                'args': {
                    'from': transfer['args']['from'],
                    'to': transfer['args']['to'],
                    'value': int(transfer['args']['value'])
                }
            }
            processed_transfers.append(processed_transfer)
        
        # 调用套利分析函数
        arb_info = get_arbitrage_from_receipt_if_exists(processed_receipt, processed_transfers)
        if not arb_info:
            return None
            
        # 更新统计信息
        if 'token_graph' in arb_info:
            self.token_graph = nx.compose(self.token_graph, arb_info['token_graph'])
            
        for dex, count in arb_info.get('dex_usage', {}).items():
            self.dex_usage[dex] += count
            
        for path in arb_info.get('arbitrage_paths', []):
            self.path_lengths.append(path['length'])
            
        for token, profit in arb_info.get('profits', {}).items():
            self.token_profits[token] += profit
            
        self.miner_revenue += arb_info['miner_revenue']
        self.arbitrage_count += 1
        
        # 记录详细套利信息
        for path in arb_info.get('arbitrage_paths', []):
            profit_taker = processed_receipt['from']
            self.profit_takers[profit_taker] += path['profit']
            
            # 找到利润最高的代币
            max_profit_token = max(path['path'], key=lambda x: x['profit'])
            
            self.arbitrage_details.append({
                'transaction_hash': processed_receipt['transactionHash'],
                'block_number': processed_receipt['blockNumber'],
                'profit_taker': profit_taker,
                'path_length': path['length'],
                'total_profit': path['profit'],
                'profit_token': max_profit_token['to_token'],
                'profit_token_symbol': get_token_symbol(max_profit_token['to_token']),
                'profit_amount': max_profit_token['profit'],
                'steps': path['path']
            })
        
        return arb_info
        
    def generate_visualizations(self):
        """生成可视化结果"""
        # 1. 代币流向图
        plt.figure(figsize=(12, 8))
        pos = nx.spring_layout(self.token_graph)
        nx.draw(self.token_graph, pos, with_labels=True, node_color='lightblue', 
                node_size=500, font_size=8, font_weight='bold')
        plt.title('Token Flow Graph')
        plt.savefig('enhanced_token_flow_graph.png')
        plt.close()
        
        # 2. DEX 使用频率
        plt.figure(figsize=(10, 6))
        plt.bar(self.dex_usage.keys(), self.dex_usage.values())
        plt.xticks(rotation=45)
        plt.title('DEX Usage Frequency')
        plt.tight_layout()
        plt.savefig('enhanced_dex_usage.png')
        plt.close()
        
        # 3. 套利路径长度分布
        plt.figure(figsize=(10, 6))
        plt.hist(self.path_lengths, bins=20)
        plt.title('Arbitrage Path Length Distribution')
        plt.xlabel('Path Length')
        plt.ylabel('Frequency')
        plt.savefig('enhanced_path_length_dist.png')
        plt.close()
        
        # 4. 代币收益分布
        plt.figure(figsize=(10, 6))
        tokens = [get_token_symbol(t) for t in self.token_profits.keys()]
        profits = list(self.token_profits.values())
        plt.bar(tokens, profits)
        plt.xticks(rotation=45)
        plt.title('Token Profits Distribution')
        plt.tight_layout()
        plt.savefig('enhanced_token_profits.png')
        plt.close()
        
        # 5. 套利者收益分布
        plt.figure(figsize=(10, 6))
        plt.bar(self.profit_takers.keys(), self.profit_takers.values())
        plt.xticks(rotation=45)
        plt.title('Profit Taker Revenue Distribution')
        plt.tight_layout()
        plt.savefig('profit_taker_revenue.png')
        plt.close()
        
    def save_analysis_results(self, output_file: str):
        """保存分析结果到 JSON 文件"""
        if not self.path_lengths:  # 防止除零错误
            return
            
        results = {
            'arbitrage_count': self.arbitrage_count,
            'miner_revenue': self.miner_revenue,
            'dex_usage': dict(self.dex_usage),
            'path_length_stats': {
                'mean': sum(self.path_lengths) / len(self.path_lengths),
                'max': max(self.path_lengths),
                'min': min(self.path_lengths)
            },
            'token_profits': dict(self.token_profits),
            'profit_takers': dict(self.profit_takers),
            'detailed_arbitrage_paths': self.arbitrage_details
        }
        
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)

def main():
    # 读取交易数据
    with open('block_17518743_transfers.json', 'r') as f:
        transfers = json.load(f)
    with open('block_17518743_receipts.json', 'r') as f:
        receipts = json.load(f)
        
    print(f"已加载 {len(transfers)} 条 ERC-20 代币转账和 {len(receipts)} 条交易收据（transaction receipts）")
    
    # 创建分析器实例
    analyzer = EnhancedArbitrageAnalysis()
    
    # 分析每笔交易
    for receipt in receipts:
        # 获取该交易的所有转账记录
        txn_transfers = [t for t in transfers if t['transactionHash'] == receipt['transactionHash']]
        
        # 分析交易
        arb_info = analyzer.analyze_transaction(receipt, txn_transfers)
        if arb_info:
            print(f"发现代币流转环路（token flow cycle）: {receipt['transactionHash']}")
            for path in arb_info.get('arbitrage_paths', []):
                print(f"发现套利：路径长度: {path['length']}, 总利润: {path['profit']}")
    
    # 生成可视化结果
    analyzer.generate_visualizations()
    
    # 保存分析结果
    analyzer.save_analysis_results('enhanced_arbitrage_analysis_results.json')
    print(f"分析完成。发现 {analyzer.arbitrage_count} 笔代币流转环路（token flow cycle）。")
    print("结果已保存到 enhanced_arbitrage_analysis_results.json")

if __name__ == "__main__":
    main()
