import json
import networkx as nx
import matplotlib.pyplot as plt
import seaborn as sns
from typing import List, Dict
import os
from operator import itemgetter
from collections import Counter
import pandas as pd
from datetime import datetime

# DEX name mapping
DEX_NAME_MAPPING = {
    '0x4a6670...': 'Uniswap V2',
    '0x742627...': 'SushiSwap',
    '0xac6343...': 'Curve',
    '0x7a250d...': 'Uniswap V2 Router',
    '0xd9e1cE...': 'SushiSwap Router',
    '0x111111...': '1inch',
    '0x68b346...': 'Uniswap V3 Router',
    '0xE59242...': 'Uniswap V3 Router',
    '0x7c0252...': 'Balancer',
    '0x1111111254EEB25477B68fb85Ed929f73A960582': '1inch V4',
    '0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D': 'Uniswap V2',
    '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45': 'Uniswap V3',
    '0xE592427A0AEce92De3Edee1F18E0157C05861564': 'Uniswap V3',
    '0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F': 'SushiSwap',
    '0x7c0252003b3a3716c71409b57f74C5A31F195B07': 'Balancer'
}

def load_arbitrage_data(file_path: str) -> Dict:
    """Load arbitrage data from JSON file"""
    with open(file_path, 'r') as f:
        return json.load(f)

def get_representative_arbitrages(arbitrages: List[Dict], top_n: int = 5) -> List[Dict]:
    """Select representative arbitrage transactions."""
    # Sort by gas_used (most complex transactions)
    by_gas_used = sorted(arbitrages, key=itemgetter('gas_used'), reverse=True)[:top_n]
    
    # Sort by gas_price (most profitable potential)
    by_gas_price = sorted(arbitrages, key=itemgetter('gas_price'), reverse=True)[:top_n]
    
    # Get most active shooters
    shooter_freq = {}
    for arb in arbitrages:
        shooter_freq[arb['shooter']] = shooter_freq.get(arb['shooter'], 0) + 1
    
    most_active_shooters = sorted(shooter_freq.items(), key=itemgetter(1), reverse=True)[:3]
    active_shooter_txns = []
    for shooter, _ in most_active_shooters:
        shooter_txns = [arb for arb in arbitrages if arb['shooter'] == shooter][:2]
        active_shooter_txns.extend(shooter_txns)
    
    # Combine all representative transactions
    representative = by_gas_used + by_gas_price + active_shooter_txns
    
    # Remove duplicates while preserving order
    seen = set()
    unique_representative = []
    for arb in representative:
        if arb['txn_hash'] not in seen:
            seen.add(arb['txn_hash'])
            unique_representative.append(arb)
    
    return unique_representative

def create_arbitrage_graph(arbitrages: List[Dict]) -> nx.DiGraph:
    """Create a directed graph from arbitrage data."""
    G = nx.DiGraph()
    
    # Add nodes and edges for each arbitrage
    for arb in arbitrages:
        shooter = arb['shooter']
        txn_hash = arb['txn_hash'][:10] + '...'  # Truncate hash for better visualization
        gas_used = arb['gas_used']
        gas_price = arb['gas_price']
        
        # Add shooter node if not exists
        if not G.has_node(shooter):
            G.add_node(shooter, type='shooter')
        
        # Add transaction node with gas info
        G.add_node(txn_hash, type='transaction', 
                  gas_used=gas_used, 
                  gas_price=gas_price)
        
        # Add edges with gas info
        G.add_edge(shooter, txn_hash, type='initiates')
        G.add_edge(txn_hash, shooter, type='completes')
    
    return G

def visualize_arbitrage_graph(G: nx.DiGraph, output_path: str):
    """Visualize the arbitrage graph and save to file."""
    plt.figure(figsize=(15, 10))
    
    # Position nodes using spring layout with more space
    pos = nx.spring_layout(G, k=2, iterations=50)
    
    # Draw nodes
    shooters = [n for n in G.nodes() if G.nodes[n]['type'] == 'shooter']
    transactions = [n for n in G.nodes() if G.nodes[n]['type'] == 'transaction']
    
    # Draw shooter nodes
    nx.draw_networkx_nodes(G, pos, 
                          nodelist=shooters,
                          node_color='lightblue',
                          node_size=2000,
                          label='Shooter')
    
    # Draw transaction nodes
    nx.draw_networkx_nodes(G, pos,
                          nodelist=transactions,
                          node_color='lightgreen',
                          node_size=1500,
                          label='Transaction')
    
    # Draw edges
    nx.draw_networkx_edges(G, pos, 
                          edgelist=[(u, v) for u, v, d in G.edges(data=True) if d['type'] == 'initiates'],
                          edge_color='blue',
                          arrows=True,
                          label='Initiates',
                          width=2)
    
    nx.draw_networkx_edges(G, pos,
                          edgelist=[(u, v) for u, v, d in G.edges(data=True) if d['type'] == 'completes'],
                          edge_color='red',
                          arrows=True,
                          label='Completes',
                          width=2)
    
    # Add labels with smaller font
    labels = {}
    for node in G.nodes():
        if G.nodes[node]['type'] == 'transaction':
            gas_used = G.nodes[node]['gas_used']
            gas_price = G.nodes[node]['gas_price']
            labels[node] = f"{node}\nGas: {gas_used:,}\nPrice: {gas_price/1e9:.1f} Gwei"
        else:
            labels[node] = f"{node[:10]}..."
            
    nx.draw_networkx_labels(G, pos, labels, font_size=8)
    
    # Add legend
    plt.legend(fontsize=12)
    
    # Add title
    plt.title("Representative Arbitrage Transactions", fontsize=16, pad=20)
    
    # Save the plot
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    plt.close()

def analyze_dex_usage(data: Dict, output_path: str):
    """Analyze DEX usage patterns"""
    dex_usage = Counter()
    for path in data['detailed_arbitrage_paths']:
        for step in path['steps']:
            dex_address = step['dex_name']
            # Use mapped name if available, otherwise use truncated address
            dex_name = DEX_NAME_MAPPING.get(dex_address, dex_address)
            dex_usage[dex_name] += 1
    
    plt.figure(figsize=(12, 6))
    dex_names = list(dex_usage.keys())
    usage_counts = list(dex_usage.values())
    
    # Sort by usage count
    sorted_pairs = sorted(zip(dex_names, usage_counts), key=lambda x: x[1], reverse=True)
    dex_names, usage_counts = zip(*sorted_pairs)
    
    plt.bar(dex_names, usage_counts)
    plt.title('DEX Usage Distribution')
    plt.xlabel('DEX Name')
    plt.ylabel('Usage Count')
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()

def analyze_arbitrage_characteristics(data: Dict, output_path: str):
    """Analyze arbitrage characteristics"""
    # Path length distribution
    path_lengths = [path['path_length'] for path in data['detailed_arbitrage_paths']]
    
    # Token profit distribution
    token_profits = data['token_profits']
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
    
    # Path length distribution
    sns.histplot(path_lengths, ax=ax1)
    ax1.set_title('Arbitrage Path Length Distribution')
    ax1.set_xlabel('Path Length')
    ax1.set_ylabel('Frequency')
    
    # Token profit distribution
    tokens = list(token_profits.keys())
    profits = list(token_profits.values())
    ax2.bar(tokens, profits)
    ax2.set_title('Token Profit Distribution')
    ax2.set_xlabel('Token Address')
    ax2.set_ylabel('Total Profit (Wei)')
    plt.xticks(rotation=45)
    
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()

def analyze_economic_metrics(arbitrages: List[Dict], output_path: str):
    """Analyze economic metrics"""
    gas_costs = []
    profits = []
    
    for arb in arbitrages:
        gas_cost = arb['gas_used'] * arb['gas_price'] / 1e18  # Convert to ETH
        total_profit = sum(float(path['total_profit']) for path in arb['paths'])
        gas_costs.append(gas_cost)
        profits.append(total_profit)
    
    plt.figure(figsize=(10, 6))
    plt.scatter(gas_costs, profits)
    plt.plot([0, max(gas_costs)], [0, max(gas_costs)], 'r--', label='Break-even Line')
    plt.title('Gas Cost vs Profit Analysis')
    plt.xlabel('Gas Cost (ETH)')
    plt.ylabel('Profit (ETH)')
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()

def analyze_profit_taker_distribution(data: Dict, output_path: str):
    """Analyze profit taker distribution"""
    profit_takers = data['profit_takers']
    
    plt.figure(figsize=(12, 6))
    takers = list(profit_takers.keys())
    profits = list(profit_takers.values())
    
    plt.bar(takers, profits)
    plt.title('Profit Taker Distribution')
    plt.xlabel('Profit Taker Address')
    plt.ylabel('Total Profit (Wei)')
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()

def analyze_token_flow(data: Dict, output_path: str):
    """Analyze token flow patterns"""
    token_flows = {}
    for path in data['detailed_arbitrage_paths']:
        for step in path['steps']:
            from_token = step['from_token_symbol']
            to_token = step['to_token_symbol']
            amount = float(step['in_amount'])
            
            key = f"{from_token}->{to_token}"
            token_flows[key] = token_flows.get(key, 0) + amount
    
    plt.figure(figsize=(12, 6))
    flows = list(token_flows.keys())
    amounts = list(token_flows.values())
    
    plt.bar(flows, amounts)
    plt.title('Token Flow Analysis')
    plt.xlabel('Token Flow')
    plt.ylabel('Transaction Volume')
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()

def analyze_miner_vs_profit_taker(data: Dict, output_path: str):
    """Analyze miner revenue vs profit taker earnings for a single transaction"""
    # Single transaction gas fee (from external data)
    single_tx_gas_fee = 0.003190571274444204  # ETH
    
    # Profit taker earnings from the single transaction
    profit_taker_earnings = data['detailed_arbitrage_paths'][0]['total_profit'] / 1e18  # Convert to ETH
    
    # Create data for plotting
    categories = ['Miner Gas Fee', 'Profit Taker Earnings']
    values = [single_tx_gas_fee, profit_taker_earnings]
    
    # Create bar plot
    plt.figure(figsize=(10, 6))
    bars = plt.bar(categories, values)
    
    # Add value labels on top of bars
    for bar in bars:
        height = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2., height,
                f'{height:.6f} ETH',
                ha='center', va='bottom')
    
    plt.title('Single Transaction: Miner Fee vs Profit Taker Earnings')
    plt.ylabel('Amount (ETH)')
    
    # Add ratio annotation
    ratio = single_tx_gas_fee / profit_taker_earnings if profit_taker_earnings != 0 else 0
    plt.annotate(f'Ratio (Miner:Profit Taker) = {ratio:.2f}:1',
                xy=(0.5, 0.95), xycoords='axes fraction',
                ha='center', va='top',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
    
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()
    
    return single_tx_gas_fee, profit_taker_earnings

def main():
    # Load arbitrage data
    data = load_arbitrage_data('enhanced_arbitrage_analysis_results.json')
    
    # Create output directory
    os.makedirs('output', exist_ok=True)
    
    # Generate analysis charts
    analyze_dex_usage(data, 'output/dex_usage.png')
    analyze_arbitrage_characteristics(data, 'output/arbitrage_characteristics.png')
    analyze_profit_taker_distribution(data, 'output/profit_taker_distribution.png')
    analyze_token_flow(data, 'output/token_flow.png')
    miner_fee, profit_taker_earnings = analyze_miner_vs_profit_taker(data, 'output/miner_vs_profit.png')
    
    # Print statistics
    print("\nSingle Transaction Analysis:")
    print(f"Miner Gas Fee: {miner_fee:.6f} ETH")
    print(f"Profit Taker Earnings: {profit_taker_earnings:.6f} ETH")
    print(f"Ratio (Miner:Profit Taker): {miner_fee/profit_taker_earnings:.2f}:1")
    
    # Print path statistics
    print("\nPath Statistics:")
    print(f"Path Length: {data['path_length_stats']['mean']}")
    print(f"Number of Steps: {len(data['detailed_arbitrage_paths'][0]['steps'])}")
    
    # Print token profit statistics
    print("\nToken Profit Statistics:")
    for token, profit in data['token_profits'].items():
        print(f"Token {token}: {profit / 1e18:.4f} ETH")

if __name__ == "__main__":
    main() 