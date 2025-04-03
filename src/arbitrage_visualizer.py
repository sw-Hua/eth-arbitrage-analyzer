import json
import os
from typing import Dict, List, Optional
from web3 import Web3

# 配置
CONFIG = {
    'data_dir': '.',
    'results_file': 'enhanced_arbitrage_analysis_results.json',
    'output_file': 'arbitrage_flow.txt'
}

# 常用代币符号映射
COMMON_TOKENS = {
    '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2': 'WETH',
    '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48': 'USDC',
    '0x6B175474E89094C44Da98b954EedeAC495271d0F': 'DAI',
    '0xdAC17F958D2ee523a2206206994597C13D831ec7': 'USDT',
    '0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599': 'WBTC',
    '0xC7a2572fA8FDB0f7E81d6D3c4e3CCF78FB0DC374': 'FINALE',
    '0x511686014F39F487E5CDd5C37B4b37606B795ae3': 'LOYAL'
}

# DEX名称映射
DEX_NAMES = {
    '0x7426274e92478c7ba306a48b46a6fbefce6c7099': 'Uniswap V2 Pool',
    '0xac63436b092b944cadea9243f9aff315421d4fee': 'Uniswap V3 Pool',
    '0x4a6670b0afb21b2770541c4c9bd678323f7d84c4': 'Uniswap V2 Pool'
}

def format_amount(amount: float, token_symbol: str) -> str:
    """格式化代币数量"""
    if token_symbol in ['WETH', 'ETH']:
        return f"{amount / 1e18:.6f} {token_symbol}"
    elif token_symbol in ['USDC', 'USDT', 'DAI']:
        return f"{amount / 1e6:.2f} {token_symbol}"
    else:
        return f"{amount / 1e18:.2f} {token_symbol}"

def get_token_symbol(address: str) -> str:
    """获取代币符号"""
    return COMMON_TOKENS.get(address, address[:8] + '...')

def get_dex_name(address: str) -> str:
    """获取DEX名称"""
    return DEX_NAMES.get(address, address[:8] + '...')

def visualize_arbitrage_flow():
    """可视化套利流程"""
    # 读取分析结果
    results_path = os.path.join(CONFIG['data_dir'], CONFIG['results_file'])
    with open(results_path, 'r') as f:
        results = json.load(f)
    
    # 创建输出文件
    output_path = os.path.join(CONFIG['data_dir'], CONFIG['output_file'])
    with open(output_path, 'w') as f:
        # 写入总体统计信息
        f.write("=== Arbitrage Analysis Summary ===\n\n")
        f.write(f"Total Arbitrage Opportunities Found: {results['arbitrage_count']}\n")
        f.write(f"Average Path Length: {results['path_length_stats']['mean']}\n")
        f.write(f"Total Miner Revenue: {results['miner_revenue'] / 1e18:.6f} ETH\n\n")
        
        # 写入每个套利路径的详细信息
        f.write("=== Detailed Arbitrage Paths ===\n\n")
        for path in results['detailed_arbitrage_paths']:
            f.write(f"Transaction Hash: {path['transaction_hash']}\n")
            f.write(f"Block Number: {path['block_number']}\n")
            f.write(f"Arbitrageur: {path['profit_taker']}\n")
            f.write(f"Total Profit: {format_amount(path['total_profit'], path['profit_token_symbol'])}\n\n")
            
            # 写入每个步骤的详细信息
            f.write("Arbitrage Flow:\n")
            for i, step in enumerate(path['steps'], 1):
                from_symbol = get_token_symbol(step['from_token'])
                to_symbol = get_token_symbol(step['to_token'])
                dex_name = get_dex_name(step['exchange'])
                
                f.write(f"Step {i}: {format_amount(step['in_amount'], from_symbol)} ")
                f.write(f"→ {dex_name} → ")
                f.write(f"{format_amount(step['out_amount'], to_symbol)}\n")
                
                if step['profit'] != 0:
                    profit_symbol = from_symbol if step['profit'] < 0 else to_symbol
                    f.write(f"   Profit/Loss: {format_amount(abs(step['profit']), profit_symbol)}\n")
                f.write("\n")
            
            f.write("-" * 80 + "\n\n")
        
        # 写入代币利润统计
        f.write("=== Token Profit Summary ===\n\n")
        for token, profit in results['token_profits'].items():
            if profit != 0:
                symbol = get_token_symbol(token)
                f.write(f"{symbol}: {format_amount(profit, symbol)}\n")
        
        # 写入套利者收益统计
        f.write("\n=== Arbitrageur Profit Summary ===\n\n")
        for taker, profit in results['profit_takers'].items():
            f.write(f"Arbitrageur {taker[:8]}...: {format_amount(profit, 'WETH')}\n")

if __name__ == '__main__':
    visualize_arbitrage_flow()
    print(f"Arbitrage flow visualization has been saved to {os.path.join(CONFIG['data_dir'], CONFIG['output_file'])}") 