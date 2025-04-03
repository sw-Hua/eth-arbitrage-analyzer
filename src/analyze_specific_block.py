"""
特定区块套利分析模块
用于分析指定区块中的套利机会
"""

import os
import sys
import logging
import json
from web3 import Web3
from dotenv import load_dotenv
from typing import List, Dict
from web3.types import HexBytes

# 获取项目根目录的绝对路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 将 goldphish 目录添加到 Python 路径
sys.path.append(os.path.join(project_root, 'goldphish'))

# 导入套利分析相关模块
from backtest.gather_samples.analyses import get_arbitrage_from_receipt_if_exists
from backtest.gather_samples.models import Arbitrage, ArbitrageCycle, ArbitrageCycleExchange, ArbitrageCycleExchangeItem

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 初始化Web3
w3 = Web3(Web3.HTTPProvider(os.getenv('ALCHEMY_API_URL')))

class HexJsonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, HexBytes):
            return obj.hex()
        return super().default(obj)

def load_erc20_transactions(file_path: str) -> List[Dict]:
    """从JSON文件加载ERC-20交易数据"""
    try:
        with open(file_path, 'r') as f:
            transactions = json.load(f)
        logger.info(f"成功加载 {len(transactions)} 笔ERC-20交易")
        return transactions
    except Exception as e:
        logger.error(f"加载ERC-20交易数据失败: {str(e)}")
        raise

def analyze_arbitrage(block_number: int, erc20_transactions: List[Dict]) -> List[Arbitrage]:
    """分析区块中的套利机会"""
    try:
        # 获取区块中的所有交易收据
        block = w3.eth.get_block(block_number, full_transactions=True)
        arbitrages = []
        
        # 对每个交易进行套利分析
        for tx in block.transactions:
            receipt = w3.eth.get_transaction_receipt(tx.hash)
            
            # 使用analyses.py中的函数进行套利识别
            arbitrage = get_arbitrage_from_receipt_if_exists(receipt, erc20_transactions)
            if arbitrage:
                arbitrages.append(arbitrage)
                logger.info(f"发现套利交易: {tx.hash.hex()}")
                if arbitrage.only_cycle:
                    logger.info(f"套利获利代币: {arbitrage.only_cycle.profit_token}")
                    logger.info(f"套利获利金额: {arbitrage.only_cycle.profit_amount}")
                    logger.info(f"套利执行地址: {arbitrage.shooter}")
        
        return arbitrages
        
    except Exception as e:
        logger.error(f"套利分析失败: {str(e)}")
        raise

def save_arbitrages_to_file(arbitrages: List[Arbitrage], filename: str):
    """保存套利结果到文件"""
    try:
        # 将Arbitrage对象转换为可序列化的字典
        arbitrage_dicts = []
        for arb in arbitrages:
            arb_dict = {
                'txn_hash': arb.txn_hash.hex(),
                'block_number': arb.block_number,
                'gas_used': arb.gas_used,
                'gas_price': arb.gas_price,
                'shooter': arb.shooter,
                'n_cycles': arb.n_cycles,
            }
            
            if arb.only_cycle:
                cycle_dict = {
                    'profit_token': arb.only_cycle.profit_token,
                    'profit_amount': arb.only_cycle.profit_amount,
                    'profit_taker': arb.only_cycle.profit_taker,
                    'exchanges': [
                        {
                            'token_in': exc.token_in,
                            'token_out': exc.token_out,
                            'items': [
                                {
                                    'address': item.address,
                                    'amount_in': item.amount_in,
                                    'amount_out': item.amount_out
                                }
                                for item in exc.items
                            ]
                        }
                        for exc in arb.only_cycle.cycle
                    ]
                }
                arb_dict['only_cycle'] = cycle_dict
            
            arbitrage_dicts.append(arb_dict)
            
        with open(filename, 'w') as f:
            json.dump(arbitrage_dicts, f, indent=2)
        logger.info(f"套利结果已保存到 {filename}")
    except Exception as e:
        logger.error(f"保存套利结果失败: {str(e)}")
        raise

def main():
    """主函数"""
    try:
        # 指定要分析的区块号
        block_number = 17518743
        
        # 加载ERC-20交易数据
        erc20_transactions = load_erc20_transactions('block_17518743_erc20_transactions.json')
        
        # 分析套利机会
        arbitrages = analyze_arbitrage(block_number, erc20_transactions)
        
        # 保存套利结果
        save_arbitrages_to_file(arbitrages, f'block_{block_number}_arbitrages.json')
        
        # 输出统计信息
        logger.info(f"分析完成，共发现 {len(arbitrages)} 个套利机会")
        
    except Exception as e:
        logger.error(f"程序执行失败: {str(e)}")
        raise

if __name__ == "__main__":
    main() 