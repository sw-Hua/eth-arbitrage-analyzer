import os
import sys
import json
import logging
import networkx as nx
import matplotlib.pyplot as plt
from collections import defaultdict
from typing import List, Dict, Set, Tuple, Optional
from web3 import Web3

# 获取项目根目录的绝对路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 将 goldphish 目录添加到 Python 路径
sys.path.append(os.path.join(project_root, 'goldphish'))

from backtest.gather_samples.models import Arbitrage, ArbitrageCycle, ArbitrageCycleExchange, ArbitrageCycleExchangeItem

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 已知的 DEX 路由合约地址
KNOWN_DEX_ROUTERS = {
    '0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D': 'Uniswap V2',
    '0xE592427A0AEce92De3Edee1F18E0157C05861564': 'Uniswap V3',
    '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45': '1inch',
    '0x1111111254EEB25477B68fb85Ed929f73A960582': '1inch V4',
    '0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD': 'Paraswap',
    '0xE7667Cb1cd8FE89AA38d7F20DCC50ee262cC9D12': '0x Protocol',
    '0x32400084C286CF3E17e7B677ea9583e60a000324': 'Kyber Network',
    '0xBA12222222228d8Ba445958a75a0704d566BF2C8': 'Balancer V2'
}

def hex_to_bytes(hex_str: str) -> bytes:
    """将十六进制字符串转换为bytes"""
    if hex_str.startswith('0x'):
        hex_str = hex_str[2:]
    return bytes.fromhex(hex_str)

def get_addr_to_movements(txns: List[Dict]) -> Dict[str, Dict]:
    """获取地址的转入转出记录"""
    addr_to_movements = defaultdict(lambda: {'in': [], 'out': []})
    for txn in txns:
        to_addr = txn['args']['to']
        from_addr = txn['args']['from']
        addr_to_movements[to_addr]['in'].append(txn)
        addr_to_movements[from_addr]['out'].append(txn)
    return addr_to_movements

def get_potential_exchanges(full_txn: Dict, addr_to_movements: Dict) -> Set[str]:
    """识别潜在的交易所地址"""
    potential_exchanges = set()
    for addr in addr_to_movements:
        # 忽略零地址
        if addr.startswith('0x' + '00' * 17):
            continue
        # 忽略已知的路由合约
        if addr in KNOWN_DEX_ROUTERS:
            continue

        ins = addr_to_movements[addr]['in']
        outs = addr_to_movements[addr]['out']
        if not ins or not outs:
            continue

        # 忽略发送者地址
        if addr == full_txn['from']:
            continue

        in_coins = set(x['address'] for x in ins)
        out_coins = set(x['address'] for x in outs)
        if len(in_coins) == 1 and len(out_coins) == 1 and in_coins != out_coins:
            potential_exchanges.add(addr)

    return potential_exchanges

def get_arbitrage_from_receipt_if_exists(full_txn: Dict, txns: List[Dict]) -> Optional[Dict]:
    """分析交易收据中的套利机会
    
    Args:
        full_txn: 交易收据
        txns: ERC20 转账记录列表
    
    Returns:
        Dict: 套利详情,包含:
            - token_graph: 代币流向图
            - dex_usage: DEX 使用频率
            - path_length: 套利路径长度
            - profits: 各代币收益
            - miner_revenue: 矿工收益
    """
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
        in_amount = sum(float(x['args']['value']) for x in ins)
        out_amount = sum(float(x['args']['value']) for x in outs)
        
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
        path = []
        total_profit = 0
        
        # 计算环路中每条边的信息
        for i in range(len(cycle)):
            from_token = cycle[i]
            to_token = cycle[(i + 1) % len(cycle)]
            
            edge_data = token_graph.get_edge_data(from_token, to_token)
            if not edge_data:
                continue
                
            path.append({
                'from_token': from_token,
                'to_token': to_token,
                'exchange': edge_data['exchange'],
                'in_amount': edge_data['in_amount'],
                'out_amount': edge_data['out_amount']
            })
            
            # 计算这一步的收益
            profit = edge_data['out_amount'] - edge_data['in_amount']
            total_profit += profit
            profits[from_token] += profit
            
        arbitrage_paths.append({
            'path': path,
            'length': len(path),
            'profit': total_profit
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

class ArbitrageAnalysis:
    def __init__(self):
        self.token_graph = nx.DiGraph()  # 代币流向图
        self.dex_usage = defaultdict(int)  # DEX 使用频率
        self.path_lengths = []  # 套利路径长度
        self.token_profits = defaultdict(float)  # 各代币套利收益
        self.miner_revenue = 0  # 矿工收益
        self.arbitrage_count = 0  # 套利交易数量
        
    def analyze_transaction(self, receipt: Dict, transfers: List[Dict]) -> Optional[Dict]:
        """分析单笔交易,返回详细的套利信息"""
        # 将 receipt 中的字符串转换为正确的格式
        processed_receipt = {
            'transactionHash': hex_to_bytes(receipt['transactionHash']),
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
                'transactionHash': hex_to_bytes(transfer['transactionHash']),
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
        
        return arb_info
        
    def generate_visualizations(self):
        """生成可视化结果"""
        # 1. 代币流向图
        plt.figure(figsize=(12, 8))
        pos = nx.spring_layout(self.token_graph)
        nx.draw(self.token_graph, pos, with_labels=True, node_color='lightblue', 
                node_size=500, font_size=8, font_weight='bold')
        plt.title('Token Flow Graph')
        plt.savefig('token_flow_graph.png')
        plt.close()
        
        # 2. DEX 使用频率
        plt.figure(figsize=(10, 6))
        plt.bar(self.dex_usage.keys(), self.dex_usage.values())
        plt.xticks(rotation=45)
        plt.title('DEX Usage Frequency')
        plt.tight_layout()
        plt.savefig('dex_usage.png')
        plt.close()
        
        # 3. 套利路径长度分布
        plt.figure(figsize=(10, 6))
        plt.hist(self.path_lengths, bins=20)
        plt.title('Arbitrage Path Length Distribution')
        plt.xlabel('Path Length')
        plt.ylabel('Frequency')
        plt.savefig('path_length_dist.png')
        plt.close()
        
        # 4. 代币收益分布
        plt.figure(figsize=(10, 6))
        tokens = list(self.token_profits.keys())
        profits = list(self.token_profits.values())
        plt.bar(tokens, profits)
        plt.xticks(rotation=45)
        plt.title('Token Profits Distribution')
        plt.tight_layout()
        plt.savefig('token_profits.png')
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
            'token_profits': dict(self.token_profits)
        }
        
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
            
def main():
    try:
        # 读取交易数据
        with open('block_17518743_transfers.json', 'r') as f:
            transfers = json.load(f)
        
        # 读取交易收据数据
        with open('block_17518743_receipts.json', 'r') as f:
            receipts = json.load(f)
        
        logger.info(f"成功加载 {len(transfers)} 笔转账和 {len(receipts)} 笔交易收据")
        
        # 创建分析器
        analyzer = ArbitrageAnalysis()
        
        # 处理每笔交易
        for receipt in receipts:
            try:
                # 获取该交易对应的转账记录
                tx_transfers = [t for t in transfers 
                              if t['transactionHash'] == receipt['transactionHash']]
                
                if not tx_transfers:
                    continue
                    
                logger.info(f"正在分析交易: {receipt['transactionHash']}")
                logger.info(f"转账记录数量: {len(tx_transfers)}")
                logger.info(f"第一条转账记录: {tx_transfers[0]}")
                
                # 分析交易
                detailed_arb = analyzer.analyze_transaction(receipt, tx_transfers)
                if detailed_arb:
                    logger.info(f"发现套利机会: {detailed_arb['transaction_hash'].hex()}")
            except Exception as e:
                logger.error(f"处理交易时发生错误: {str(e)}")
                logger.error(f"交易信息: {receipt['transactionHash']}")
                import traceback
                logger.error(f"错误堆栈: {traceback.format_exc()}")
                continue
        
        # 生成可视化结果
        analyzer.generate_visualizations()
        
        # 保存分析结果
        analyzer.save_analysis_results('arbitrage_analysis_results.json')
        
    except Exception as e:
        logger.error(f"分析过程中发生错误: {str(e)}")

if __name__ == "__main__":
    main() 