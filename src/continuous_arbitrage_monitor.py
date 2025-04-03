"""
持续监控区块链上的套利机会
基于arbitrage_analyzer.py的核心逻辑,但会持续运行直到发现套利机会
"""

import os
import sys
import time
import logging
from typing import Optional
from web3 import Web3
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 获取项目根目录的绝对路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 将 goldphish 目录添加到 Python 路径
sys.path.append(os.path.join(project_root, 'goldphish'))

from backtest.gather_samples.analyses import get_arbitrage_from_receipt_if_exists, get_addr_to_movements, get_potential_exchanges
from erc20_parser import parse_transaction_receipt
from arbitrage_analyzer import (
    format_transaction_hash,
    safe_int_conversion,
    KNOWN_DEX_ADDRESSES,
    logger
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 初始化 Web3
w3 = Web3(Web3.HTTPProvider(os.getenv('ALCHEMY_API_URL')))

def analyze_block(block_number: int) -> Optional[dict]:
    """
    分析指定区块中的套利机会
    
    Args:
        block_number: 区块号
        
    Returns:
        Optional[dict]: 如果找到套利机会则返回详细信息,否则返回None
    """
    try:
        logger.info(f"\n开始分析区块 {block_number}")
        
        # 获取区块数据
        block = w3.eth.get_block(block_number, full_transactions=True)
        if not block:
            logger.error("获取区块数据失败")
            return None
            
        # 获取区块中的交易收据
        transactions = []
        failed_tx_count = 0
        for tx in block['transactions']:
            try:
                receipt = w3.eth.get_transaction_receipt(tx['hash'])
                if receipt and receipt['status'] == 1:  # 只处理成功的交易
                    transactions.append(receipt)
            except Exception as e:
                failed_tx_count += 1
                logger.debug(f"获取交易收据失败: {str(e)}")
                continue
                
        logger.info(f"区块中包含 {len(transactions)} 笔成功交易，{failed_tx_count} 笔失败交易")
        
        # 解析 ERC-20 转账记录
        all_transfers = []
        for tx in transactions:
            try:
                transfer_records = parse_transaction_receipt(tx)
                if transfer_records:
                    valid_records = [
                        record for record in transfer_records
                        if all(key in record for key in ['token_address', 'from_address', 'to_address', 'amount'])
                    ]
                    all_transfers.extend(valid_records)
            except Exception as e:
                logger.debug(f"解析转账记录失败: {str(e)}")
                continue
                
        # 格式化转账记录
        formatted_transfers = []
        for transfer in all_transfers:
            try:
                amount_wei = safe_int_conversion(transfer['amount'])
                if amount_wei == 0:
                    continue
                    
                formatted_transfer = {
                    'address': transfer['token_address'].lower(),
                    'transactionHash': format_transaction_hash(transfer['transaction_hash']),
                    'args': {
                        'to': transfer['to_address'].lower(),
                        'from': transfer['from_address'].lower(),
                        'value': amount_wei
                    }
                }
                if formatted_transfer['transactionHash'] and formatted_transfer['address']:
                    formatted_transfers.append(formatted_transfer)
            except Exception as e:
                logger.debug(f"格式化转账记录失败: {str(e)}")
                continue
                
        if not formatted_transfers:
            logger.info("没有有效的转账记录可以分析")
            return None
            
        # 分析每笔交易
        addr_to_movements = get_addr_to_movements(formatted_transfers)
        
        for tx in transactions:
            potential_exchanges = get_potential_exchanges(tx, addr_to_movements)
            if potential_exchanges:
                arbitrage = get_arbitrage_from_receipt_if_exists(tx, formatted_transfers)
                
                if arbitrage and arbitrage.only_cycle:
                    # 找到套利机会!
                    logger.info("\n🎯 发现套利机会!")
                    arbitrage_info = {
                        'block_number': block_number,
                        'transaction_hash': tx['transactionHash'].hex(),
                        'profit_token': arbitrage.only_cycle.profit_token,
                        'profit_taker': arbitrage.only_cycle.profit_taker,
                        'profit_amount': arbitrage.only_cycle.profit_amount,
                        'gas_used': tx['gasUsed'],
                        'gas_price': tx['effectiveGasPrice'],
                        'path': []
                    }
                    
                    # 记录交易路径
                    for exchange in arbitrage.only_cycle.cycle:
                        path_step = {
                            'token_in': exchange.token_in,
                            'token_out': exchange.token_out,
                            'exchanges': [{
                                'address': item.address,
                                'amount_in': item.amount_in,
                                'amount_out': item.amount_out
                            } for item in exchange.items]
                        }
                        arbitrage_info['path'].append(path_step)
                        
                    return arbitrage_info
                    
        return None
        
    except Exception as e:
        logger.error(f"分析区块时发生错误: {str(e)}")
        return None

def main():
    """
    主函数：持续监控新区块,直到发现套利机会
    """
    try:
        logger.info("开始监控区块链上的套利机会...")
        last_block = w3.eth.block_number
        
        while True:
            current_block = w3.eth.block_number
            
            # 检查是否有新区块
            if current_block > last_block:
                logger.info(f"\n发现新区块: {current_block}")
                
                # 分析新区块中的所有区块
                for block_number in range(last_block + 1, current_block + 1):
                    arbitrage = analyze_block(block_number)
                    
                    if arbitrage:
                        # 打印套利详情
                        logger.info("\n💰 套利机会详情:")
                        logger.info(f"区块号: {arbitrage['block_number']}")
                        logger.info(f"交易哈希: {arbitrage['transaction_hash']}")
                        logger.info(f"获利代币: {arbitrage['profit_token']}")
                        logger.info(f"获利地址: {arbitrage['profit_taker']}")
                        logger.info(f"获利金额: {arbitrage['profit_amount']}")
                        logger.info(f"Gas使用: {arbitrage['gas_used']}")
                        logger.info(f"Gas价格: {arbitrage['gas_price']}")
                        
                        logger.info("\n交易路径:")
                        for i, step in enumerate(arbitrage['path'], 1):
                            logger.info(f"\n第{i}步: {step['token_in']} -> {step['token_out']}")
                            for ex in step['exchanges']:
                                logger.info(f"  DEX地址: {ex['address']}")
                                logger.info(f"  输入金额: {ex['amount_in']}")
                                logger.info(f"  输出金额: {ex['amount_out']}")
                                
                        return  # 找到套利机会后退出
                
                last_block = current_block
            
            # 等待一段时间再检查新区块
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("\n监控已停止")
    except Exception as e:
        logger.error(f"运行过程中发生错误: {str(e)}")
        raise

if __name__ == "__main__":
    main() 