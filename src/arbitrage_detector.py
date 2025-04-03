"""
套利检测基础模块
复用 analyses.py 和 models.py 中的功能实现基本的套利检测
"""

import os
import sys
# 获取项目根目录的绝对路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 将 goldphish 目录添加到 Python 路径
sys.path.append(os.path.join(project_root, 'goldphish'))

import logging
from web3 import Web3
from typing import List, Optional
from backtest.gather_samples.models import Arbitrage  # 先导入 Arbitrage 类型
from backtest.gather_samples.analyses import get_arbitrage_from_receipt_if_exists
from erc20_parser import analyze_transaction, w3

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def detect_arbitrage(tx_hash: str) -> Optional[Arbitrage]:
    """
    检测单个交易中的套利机会
    复用 analyses.py 中的检测逻辑
    """
    try:
        # 获取交易收据
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        
        # 使用 ERC20 解析器获取交易的转账记录
        transfers = analyze_transaction(tx_hash)
        if not transfers:
            logger.info(f"未找到 ERC20 转账记录: {tx_hash}")
            return None
            
        # 转换转账记录格式以匹配 analyses.py 的要求
        formatted_transfers = []
        for transfer in transfers:
            try:
                # 确保转换后的数据都是基本类型，而不是字典
                formatted_transfer = {
                    'address': str(transfer['token_address']),  # 确保是字符串
                    'transactionHash': bytes.fromhex(transfer['transaction_hash'].replace('0x', '')),
                    'args': {
                        'to': str(transfer['to_address']),      # 确保是字符串
                        'from': str(transfer['from_address']),  # 确保是字符串
                        'value': int(float(transfer['amount']) * (10 ** 18))  # 确保是整数
                    }
                }
                formatted_transfers.append(formatted_transfer)
            except Exception as e:
                logger.warning(f"转换转账记录格式时出错: {str(e)}, transfer: {transfer}")
                continue
        
        if not formatted_transfers:
            logger.info(f"没有有效的转账记录可以分析: {tx_hash}")
            return None
            
        # 使用 analyses.py 中的套利检测逻辑
        arbitrage = get_arbitrage_from_receipt_if_exists(receipt, formatted_transfers)
        
        if arbitrage:
            logger.info(f"发现套利交易: {tx_hash}")
            if arbitrage.only_cycle:
                logger.info(f"套利获利代币: {arbitrage.only_cycle.profit_token}")
                logger.info(f"套利获利金额: {arbitrage.only_cycle.profit_amount}")
                logger.info(f"套利执行地址: {arbitrage.shooter}")
            
        return arbitrage
        
    except Exception as e:
        logger.error(f"检测套利时发生错误: {str(e)}")
        return None

def analyze_block_for_arbitrage(block_number: int) -> List[Arbitrage]:
    """
    分析指定区块中的套利机会
    """
    try:
        # 获取区块信息
        block = w3.eth.get_block(block_number, full_transactions=True)
        logger.info(f"分析区块 {block_number} 中的套利机会")
        
        arbitrages = []
        for tx in block.transactions:
            arb = detect_arbitrage(tx.hash.hex())
            if arb:
                arbitrages.append(arb)
                
        logger.info(f"在区块 {block_number} 中找到 {len(arbitrages)} 笔套利交易")
        return arbitrages
        
    except Exception as e:
        logger.error(f"分析区块套利时发生错误: {str(e)}")
        return [] 