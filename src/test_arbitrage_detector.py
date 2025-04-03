"""
测试套利检测基础功能
"""

import logging
from arbitrage_detector import detect_arbitrage, analyze_block_for_arbitrage, w3

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_arbitrage_detection():
    """测试套利检测功能"""
    try:
        # 获取最新区块
        latest_block = w3.eth.get_block('latest')
        block_number = latest_block.number
        logger.info(f"分析最新区块: {block_number}")
        
        # 分析区块中的套利机会
        arbitrages = analyze_block_for_arbitrage(block_number)
        
        # 输出分析结果
        logger.info(f"分析完成，找到 {len(arbitrages)} 笔套利交易")
        
        # 详细展示每笔套利交易
        for i, arb in enumerate(arbitrages, 1):
            logger.info(f"\n套利交易 #{i}")
            logger.info(f"交易哈希: 0x{arb.txn_hash.hex()}")
            logger.info(f"区块号: {arb.block_number}")
            logger.info(f"Gas使用量: {arb.gas_used}")
            logger.info(f"Gas价格: {arb.gas_price}")
            
            if arb.only_cycle:
                logger.info(f"获利代币: {arb.only_cycle.profit_token}")
                logger.info(f"获利金额: {arb.only_cycle.profit_amount}")
                logger.info(f"获利地址: {arb.only_cycle.profit_taker}")
                
                # 显示套利路径
                logger.info("套利路径:")
                for exchange in arb.only_cycle.cycle:
                    logger.info(f"  {exchange.token_in} -> {exchange.token_out}")
            
            logger.info("-" * 50)
            
    except Exception as e:
        logger.error(f"测试过程中发生错误: {str(e)}")

if __name__ == "__main__":
    test_arbitrage_detection() 