import os
import sys
import json
import logging
from web3 import Web3

# 获取项目根目录的绝对路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 将 goldphish 目录添加到 Python 路径
sys.path.append(os.path.join(project_root, 'goldphish'))

from backtest.gather_samples.analyses import get_arbitrage_from_receipt_if_exists

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def hex_to_bytes(hex_str):
    """将十六进制字符串转换为bytes"""
    if hex_str.startswith('0x'):
        hex_str = hex_str[2:]
    return bytes.fromhex(hex_str)

def process_transaction_data(transfer_data):
    """处理交易数据，转换为正确的格式"""
    processed_data = []
    for transfer in transfer_data:
        processed_transfer = {
            'address': transfer['address'],
            'transactionHash': hex_to_bytes(transfer['transactionHash']),
            'args': {
                'from': transfer['args']['from'],
                'to': transfer['args']['to'],
                'value': int(transfer['args']['value'])
            }
        }
        processed_data.append(processed_transfer)
    return processed_data

def save_arbitrage_to_json(arbitrage_data, output_file):
    """将套利数据保存为JSON格式"""
    try:
        # 将bytes类型转换为十六进制字符串
        for arb in arbitrage_data:
            if 'transactionHash' in arb:
                arb['transactionHash'] = '0x' + arb['transactionHash'].hex()
        
        with open(output_file, 'w') as f:
            json.dump(arbitrage_data, f, indent=2)
        logger.info(f"套利数据已保存到: {output_file}")
    except Exception as e:
        logger.error(f"保存JSON文件失败: {str(e)}")

def main():
    try:
        # 读取交易数据
        with open('block_17518743_transfers.json', 'r') as f:
            transfers = json.load(f)
        
        # 读取交易收据数据
        with open('block_17518743_receipts.json', 'r') as f:
            receipts = json.load(f)
        
        logger.info(f"成功加载 {len(transfers)} 笔转账和 {len(receipts)} 笔交易收据")
        
        # 处理交易数据
        processed_transfers = process_transaction_data(transfers)
        
        # 存储所有发现的套利机会
        arbitrage_opportunities = []
        
        # 对每个交易收据进行分析
        for receipt in receipts:
            # 将交易收据转换为Web3格式
            web3_receipt = {
                'transactionHash': hex_to_bytes(receipt['transactionHash']),
                'blockNumber': int(receipt['blockNumber']),
                'gasUsed': int(receipt['gasUsed']),
                'effectiveGasPrice': int(receipt['effectiveGasPrice']),
                'from': receipt['from'],
                'to': receipt['to']
            }
            
            # 获取该交易对应的所有转账记录
            tx_transfers = [t for t in processed_transfers 
                          if t['transactionHash'] == web3_receipt['transactionHash']]
            
            if not tx_transfers:
                continue
                
            # 调用套利分析函数
            arbitrage = get_arbitrage_from_receipt_if_exists(
                web3_receipt,
                tx_transfers
            )
            
            if arbitrage:
                logger.info(f"\n发现套利机会！")
                logger.info(f"交易哈希: 0x{web3_receipt['transactionHash'].hex()}")
                logger.info(f"套利详情: {arbitrage}")
                
                # 将套利机会添加到列表中
                arbitrage_opportunities.append({
                    'transactionHash': web3_receipt['transactionHash'],
                    'blockNumber': web3_receipt['blockNumber'],
                    'gasUsed': web3_receipt['gasUsed'],
                    'effectiveGasPrice': web3_receipt['effectiveGasPrice'],
                    'from': web3_receipt['from'],
                    'to': web3_receipt['to'],
                    'arbitrage': arbitrage
                })
        
        # 保存套利机会到JSON文件
        if arbitrage_opportunities:
            save_arbitrage_to_json(arbitrage_opportunities, 'block_17518743_arbitrage.json')
        else:
            logger.info("未发现套利机会")
            
    except Exception as e:
        logger.error(f"发生错误: {str(e)}")

if __name__ == "__main__":
    main() 