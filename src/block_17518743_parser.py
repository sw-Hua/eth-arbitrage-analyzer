"""
区块17518743的ERC-20转账解析模块
基于 Alchemy API 实现指定区块的 ERC-20 转账解析功能
"""

import logging
import os
import json
from typing import List, Dict, TypedDict, Optional, Union
from web3 import Web3
from web3.types import TxReceipt
from dotenv import load_dotenv
from decimal import Decimal

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 类型定义
class ERC20TransferArgs(TypedDict):
    to: str
    from_: str
    value: int

class ERC20Transfer(TypedDict):
    address: str
    transactionHash: str
    args: Dict[str, Union[str, int]]

# 获取 Alchemy API URL
ALCHEMY_API_URL = os.getenv('ALCHEMY_API_URL')
if not ALCHEMY_API_URL:
    raise ValueError("未找到 ALCHEMY_API_URL 环境变量")

logger.info(f"使用 Alchemy API URL: {ALCHEMY_API_URL}")

# 初始化 Web3
w3 = Web3(Web3.HTTPProvider(ALCHEMY_API_URL))

# ERC20 代币 ABI
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    }
]

def get_token_decimals(contract_address: str) -> int:
    """获取代币精度"""
    try:
        contract = w3.eth.contract(address=contract_address, abi=ERC20_ABI)
        decimals = contract.functions.decimals().call()
        return decimals
    except Exception as e:
        logger.warning(f"获取代币精度失败: {str(e)}")
        return 18  # 默认精度

def parse_transfer_log(log: Dict) -> Optional[ERC20Transfer]:
    """解析单个转账日志"""
    try:
        # 解析发送方和接收方地址
        from_address = '0x' + log['topics'][1].hex()[-40:]
        to_address = '0x' + log['topics'][2].hex()[-40:]
        
        # 解析转账金额（保持为整数）
        amount_hex = log['data'][2:]  # 移除 '0x' 前缀
        amount = int(amount_hex, 16)  # 保持为整数
        
        # 构建符合 analyses.py 期望格式的转账记录
        return {
            'address': log['address'],
            'transactionHash': log['transactionHash'].hex(),
            'args': {
                'from': from_address,
                'to': to_address,
                'value': amount
            }
        }
    except Exception as e:
        logger.warning(f"解析转账记录失败: {str(e)}")
        return None

def parse_block_transfers(block_number: int = 17518743) -> List[ERC20Transfer]:
    """
    解析指定区块中的所有ERC-20转账
    
    Args:
        block_number: 要解析的区块号，默认为17518743
        
    Returns:
        List[ERC20Transfer]: 解析出的ERC-20转账列表
    """
    try:
        # 获取区块信息
        block = w3.eth.get_block(block_number, full_transactions=True)
        logger.info(f"开始解析区块 {block_number}，包含 {len(block.transactions)} 笔交易")
        
        all_transfers = []
        
        # 遍历区块中的所有交易
        for tx in block.transactions:
            try:
                # 获取交易收据
                receipt = w3.eth.get_transaction_receipt(tx.hash)
                
                # 遍历所有日志
                for log in receipt['logs']:
                    # 严格验证ERC-20转账事件
                    if (len(log['topics']) == 3 and  # 必须有3个topics
                        log['topics'][0].hex() == '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef' and  # 必须是Transfer事件
                        len(log['data']) >= 2):  # data字段不能为空
                        
                        # 验证合约是否实现了ERC-20接口
                        try:
                            contract = w3.eth.contract(address=log['address'], abi=ERC20_ABI)
                            # 尝试调用ERC-20标准函数
                            contract.functions.decimals().call()
                            
                            transfer = parse_transfer_log(log)
                            if transfer:
                                all_transfers.append(transfer)
                                logger.info(f"发现ERC-20转账: {transfer['address']} 从 {transfer['args']['from']} 到 {transfer['args']['to']} 金额 {transfer['args']['value']}")
                        except Exception as e:
                            logger.debug(f"合约 {log['address']} 不是有效的ERC-20合约: {str(e)}")
                            continue
            except Exception as e:
                logger.warning(f"处理交易 {tx.hash.hex()} 时出错: {str(e)}")
                continue
        
        logger.info(f"区块 {block_number} 解析完成，共发现 {len(all_transfers)} 笔ERC-20转账")
        return all_transfers
        
    except Exception as e:
        logger.error(f"解析区块 {block_number} 失败: {str(e)}")
        return []

def save_transfers_to_file(transfers: List[ERC20Transfer], filename: str = "block_17518743_transfers.json"):
    """将转账数据保存到JSON文件"""
    try:
        with open(filename, 'w') as f:
            json.dump(transfers, f, indent=2)
        logger.info(f"转账数据已保存到文件: {filename}")
    except Exception as e:
        logger.error(f"保存转账数据失败: {str(e)}")

if __name__ == "__main__":
    try:
        # 直接解析指定区块
        logger.info("开始解析区块17518743的ERC-20转账")
        transfers = parse_block_transfers()
        
        # 保存结果
        save_transfers_to_file(transfers)
        
    except Exception as e:
        logger.error(f"程序执行失败: {str(e)}") 