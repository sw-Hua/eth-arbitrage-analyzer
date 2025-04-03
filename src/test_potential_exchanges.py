import os
import sys
import json
import logging
from web3 import Web3
from collections import defaultdict

# 获取项目根目录的绝对路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 将 goldphish 目录添加到 Python 路径
sys.path.append(os.path.join(project_root, 'goldphish'))

from backtest.gather_samples.analyses import get_potential_exchanges

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    try:
        # 1. 读取地址移动数据
        with open('block_17518743_movements.json', 'r') as f:
            addr_movements = json.load(f)
        logger.info(f"成功加载 {len(addr_movements)} 个地址的代币流动数据")

        # 2. 读取交易收据数据
        with open('block_17518743_receipts.json', 'r') as f:
            receipts = json.load(f)
        logger.info(f"成功加载 {len(receipts)} 个交易收据")
            
        # 3. 创建一个字典来存储所有交易的潜在交易所
        all_potential_exchanges = {}
            
        # 4. 对每个交易收据进行分析
        for receipt in receipts:
            # 调用get_potential_exchanges
            potential_exchanges = get_potential_exchanges(receipt, addr_movements)
            
            # 将结果存储到字典中
            tx_hash = receipt['transactionHash']
            all_potential_exchanges[tx_hash] = {
                'addresses': list(potential_exchanges),
                'movements': {}
            }
            
            # 如果发现了潜在的交易所地址，打印出来并记录详细信息
            if potential_exchanges:
                logger.info(f"\n交易哈希: {tx_hash}")
                logger.info(f"发现 {len(potential_exchanges)} 个潜在的交易所地址:")
                for addr in potential_exchanges:
                    # 获取该地址的转入转出情况
                    movements = addr_movements[addr]
                    in_tokens = set(x['address'] for x in movements['in'])
                    out_tokens = set(x['address'] for x in movements['out'])
                    
                    # 记录详细信息
                    all_potential_exchanges[tx_hash]['movements'][addr] = {
                        'in_tokens': list(in_tokens),
                        'out_tokens': list(out_tokens),
                        'in_count': len(movements['in']),
                        'out_count': len(movements['out'])
                    }
                    
                    logger.info(f"地址: {addr}")
                    logger.info(f"  转入代币: {', '.join(in_tokens)}")
                    logger.info(f"  转出代币: {', '.join(out_tokens)}")
                    logger.info(f"  转入次数: {len(movements['in'])}")
                    logger.info(f"  转出次数: {len(movements['out'])}")
        
        # 5. 将结果保存为JSON文件
        output_file = 'potential_exchanges.json'
        with open(output_file, 'w') as f:
            json.dump(all_potential_exchanges, f, indent=2)
        logger.info(f"\n结果已保存到 {output_file}")
            
    except Exception as e:
        logger.error(f"发生错误: {str(e)}")
        raise

if __name__ == "__main__":
    main() 