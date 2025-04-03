"""
测试 ERC-20 交易解析模块
"""

import os
import json
import logging
from web3 import Web3
from erc20_parser import analyze_transaction, w3

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_erc20_parser():
    """测试 ERC20 解析器"""
    try:
        # 获取最新区块
        latest_block = w3.eth.get_block('latest')
        logger.info(f"获取到最新区块: {latest_block.number}")
        
        # 获取区块中的交易
        transactions = latest_block.transactions[:3]  # 只测试前3个交易
        logger.info(f"获取到 {len(transactions)} 笔交易进行测试")
        
        total_transfers = 0
        
        for tx_hash in transactions:
            try:
                transfers = analyze_transaction(tx_hash.hex())
                total_transfers += len(transfers)
            except Exception as e:
                logger.error(f"处理交易 {tx_hash.hex()} 时出错: {str(e)}")
        
        logger.info(f"解析完成，共发现 {total_transfers} 笔代币转账")
        
    except Exception as e:
        logger.error(f"测试过程中发生错误: {str(e)}")

if __name__ == "__main__":
    test_erc20_parser() 