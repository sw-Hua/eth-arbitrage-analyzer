import os
import sys
import json
import logging

# 获取项目根目录的绝对路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 将 goldphish 目录添加到 Python 路径
sys.path.append(os.path.join(project_root, 'goldphish'))

from backtest.gather_samples.analyses import get_addr_to_movements

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def save_movements_to_json(movements, output_file):
    """将地址移动数据保存为JSON格式"""
    try:
        with open(output_file, 'w') as f:
            json.dump(movements, f, indent=2)
        logger.info(f"数据已保存到: {output_file}")
    except Exception as e:
        logger.error(f"保存JSON文件失败: {str(e)}")

def main():
    try:
        # 读取交易数据
        with open('block_17518743_transfers.json', 'r') as f:
            transfers = json.load(f)
        
        logger.info(f"成功加载 {len(transfers)} 笔转账")
        
        # 调用get_addr_to_movements
        addr_movements = get_addr_to_movements(transfers)
        
        # 打印结果
        logger.info(f"分析完成，共发现 {len(addr_movements)} 个地址的代币流动")
        
        # 打印前5个地址的流动情况
        for addr, movements in list(addr_movements.items())[:5]:
            logger.info(f"\n地址: {addr}")
            logger.info(f"转入交易数量: {len(movements['in'])}")
            logger.info(f"转出交易数量: {len(movements['out'])}")
            
            # 打印第一笔转入转出的详细信息（如果有的话）
            if movements['in']:
                first_in = movements['in'][0]
                logger.info(f"示例转入: Token地址={first_in['address']}, 数量={first_in['args']['value']}")
            if movements['out']:
                first_out = movements['out'][0]
                logger.info(f"示例转出: Token地址={first_out['address']}, 数量={first_out['args']['value']}")
        
        # 保存结果到JSON文件
        output_file = 'block_17518743_movements.json'
        save_movements_to_json(addr_movements, output_file)
            
    except Exception as e:
        logger.error(f"发生错误: {str(e)}")

if __name__ == "__main__":
    main() 