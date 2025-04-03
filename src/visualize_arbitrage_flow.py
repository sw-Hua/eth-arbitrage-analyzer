import json
import matplotlib.pyplot as plt
import networkx as nx
from web3 import Web3
import os

# 配置
CONFIG = {
    'input_file': 'enhanced_arbitrage_analysis_results.json',
    'output_dir': 'visualizations',
    'token_decimals': {
        '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2': 18,  # WETH
        '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48': 6,   # USDC
        '0x6B175474E89094C44Da98b954EedeAC495271d0F': 18,  # DAI
        '0xC7a2572fA8FDB0f7E81d6D3c4e3CCF78FB0DC374': 18,  # FINALE
        '0x511686014F39F487E5CDd5C37B4b37606B795ae3': 18,  # LOYAL
    }
}

# 常见代币符号映射
COMMON_TOKENS = {
    '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2': 'WETH',
    '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48': 'USDC',
    '0x6B175474E89094C44Da98b954EedeAC495271d0F': 'DAI',
    '0xC7a2572fA8FDB0f7E81d6D3c4e3CCF78FB0DC374': 'FINALE',
    '0x511686014F39F487E5CDd5C37B4b37606B795ae3': 'LOYAL',
}

# DEX名称映射
DEX_NAMES = {
    '0x7426274e92478c7ba306a48b46a6fbefce6c7099': 'Uniswap V2',
    '0xac63436b092b944cadea9243f9aff315421d4fee': 'Uniswap V3',
    '0x4a6670b0afb21b2770541c4c9bd678323f7d84c4': 'Uniswap V2',
}

def format_amount(amount, decimals=18):
    """格式化金额显示"""
    if amount == 0:
        return "0"
    
    # 转换为浮点数
    amount_float = float(amount) / (10 ** decimals)
    
    # 根据大小选择显示格式
    if abs(amount_float) >= 1_000_000:
        return f"{amount_float/1_000_000:.2f}M"
    elif abs(amount_float) >= 1_000:
        return f"{amount_float/1_000:.2f}K"
    else:
        return f"{amount_float:.4f}"

def create_arbitrage_flow_diagram(arbitrage_path):
    """创建套利流程图"""
    # 创建有向图
    G = nx.DiGraph()
    
    # 添加节点和边
    for i, step in enumerate(arbitrage_path['steps']):
        # 获取代币符号
        from_token = COMMON_TOKENS.get(step['from_token'], step['from_token'][:8])
        to_token = COMMON_TOKENS.get(step['to_token'], step['to_token'][:8])
        dex_name = DEX_NAMES.get(step['exchange'], step['exchange'][:8])
        
        # 格式化金额
        in_amount = format_amount(step['in_amount'], CONFIG['token_decimals'].get(step['from_token'], 18))
        out_amount = format_amount(step['out_amount'], CONFIG['token_decimals'].get(step['to_token'], 18))
        
        # 添加节点
        G.add_node(f"{from_token}\n({in_amount})", 
                  node_type='token',
                  pos=(i*2, 1))
        G.add_node(f"{dex_name}", 
                  node_type='dex',
                  pos=(i*2+1, 0))
        G.add_node(f"{to_token}\n({out_amount})", 
                  node_type='token',
                  pos=(i*2+2, 1))
        
        # 添加边
        G.add_edge(f"{from_token}\n({in_amount})", f"{dex_name}")
        G.add_edge(f"{dex_name}", f"{to_token}\n({out_amount})")
    
    # 设置图形大小和样式
    plt.figure(figsize=(15, 8))
    
    # 获取节点位置
    pos = nx.get_node_attributes(G, 'pos')
    
    # 绘制节点
    token_nodes = [n for n in G.nodes() if G.nodes[n]['node_type'] == 'token']
    dex_nodes = [n for n in G.nodes() if G.nodes[n]['node_type'] == 'dex']
    
    nx.draw_networkx_nodes(G, pos, 
                          nodelist=token_nodes,
                          node_color='lightblue',
                          node_size=2000,
                          node_shape='o')
    nx.draw_networkx_nodes(G, pos,
                          nodelist=dex_nodes,
                          node_color='lightgreen',
                          node_size=1500,
                          node_shape='s')
    
    # 绘制边
    nx.draw_networkx_edges(G, pos, 
                          edge_color='gray',
                          arrows=True,
                          arrowsize=20)
    
    # 添加标签
    nx.draw_networkx_labels(G, pos)
    
    # 添加标题
    profit_amount = format_amount(arbitrage_path['total_profit'], 
                                CONFIG['token_decimals'].get(arbitrage_path['profit_token'], 18))
    plt.title(f"Arbitrage Flow\nTotal Profit: {profit_amount} {COMMON_TOKENS.get(arbitrage_path['profit_token'], '')}",
             pad=20)
    
    # 保存图片
    plt.savefig('arbitrage_flow_diagram.png', 
                bbox_inches='tight',
                dpi=300)
    plt.close()

def main():
    # 创建输出目录
    os.makedirs(CONFIG['output_dir'], exist_ok=True)
    
    # 读取分析结果
    with open(CONFIG['input_file'], 'r') as f:
        results = json.load(f)
    
    # 为每个套利路径创建流程图
    for path in results['detailed_arbitrage_paths']:
        create_arbitrage_flow_diagram(path)
        print(f"Created visualization for arbitrage path: {path['transaction_hash'][:10]}...")

if __name__ == "__main__":
    main() 