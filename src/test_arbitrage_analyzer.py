"""
套利分析进阶模块的测试
包含多个测试用例验证套利检测的准确性和性能
"""

import logging
import unittest
from decimal import Decimal
from datetime import datetime
from web3 import Web3
from dotenv import load_dotenv
import os

from arbitrage_analyzer import (
    ArbitragePath,
    ArbitrageOpportunity,
    analyze_complex_path,
    calculate_profit,
    optimize_performance,
    find_arbitrage_opportunities
)

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TestArbitrageAnalyzer(unittest.TestCase):
    """测试套利分析进阶功能"""
    
    def setUp(self):
        """测试准备工作"""
        # 初始化 Web3
        self.w3 = Web3(Web3.HTTPProvider(os.getenv('ALCHEMY_API_URL')))
        
        # 创建测试数据 - 使用真实的代币地址
        self.test_transfers = [
            {
                'token_address': '0xdac17f958d2ee523a2206206994597c13d831ec7',  # USDT
                'transaction_hash': '0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef',
                'from_address': '0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D',  # Uniswap V2 Router
                'to_address': '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45',  # Uniswap V3 Router
                'amount': '1000.0',
                'timestamp': datetime.now()
            },
            {
                'token_address': '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',  # WETH
                'transaction_hash': '0x5678901234abcdef5678901234abcdef5678901234abcdef5678901234abcdef',
                'from_address': '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45',  # Uniswap V3 Router
                'to_address': '0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D',  # Uniswap V2 Router
                'amount': '800.0',  # 减少金额以产生正利润
                'timestamp': datetime.now()
            }
        ]
        self.test_block_number = 12345678
        
    def test_complex_path_analysis(self):
        """测试复杂套利路径分析"""
        opportunity = analyze_complex_path(self.test_transfers)
        self.assertIsNotNone(opportunity)
        self.assertIsInstance(opportunity, ArbitrageOpportunity)
        self.assertGreater(len(opportunity.path), 0)
        
        # 验证路径中的代币地址
        for path in opportunity.path:
            self.assertIn(path.token_in, ['0xdac17f958d2ee523a2206206994597c13d831ec7', '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'])
            self.assertIn(path.exchange, ['0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D', '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45'])
        
    def test_profit_calculation(self):
        """测试利润计算功能"""
        paths = [
            ArbitragePath(
                token_in='0xdac17f958d2ee523a2206206994597c13d831ec7',  # USDT
                token_out='0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',  # WETH
                exchange='0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D',  # Uniswap V2
                amount_in=Decimal('1000.0'),
                amount_out=Decimal('1500.0'),  # 修改为产生正利润的金额
                timestamp=datetime.now()
            )
        ]
        profit = calculate_profit(paths)
        self.assertGreater(profit, Decimal('0'))
        
    def test_performance_optimization(self):
        """测试性能优化功能"""
        optimized_transfers = optimize_performance(self.test_transfers)
        self.assertEqual(len(optimized_transfers), len(self.test_transfers))
        self.assertIsInstance(optimized_transfers, list)
        
        # 验证优化后的数据格式
        for transfer in optimized_transfers:
            self.assertIn('token_address', transfer)
            self.assertIn('amount', transfer)
            self.assertIn('timestamp', transfer)
        
    def test_arbitrage_opportunities(self):
        """测试套利机会发现功能"""
        opportunities = find_arbitrage_opportunities(
            self.test_block_number,
            self.test_transfers,
            min_profit=Decimal('0.01')
        )
        self.assertIsInstance(opportunities, list)
        
        # 验证套利机会的详细信息
        for opportunity in opportunities:
            self.assertGreater(opportunity.profit, Decimal('0'))
            self.assertGreater(len(opportunity.path), 0)
            self.assertGreater(opportunity.net_profit, Decimal('0'))

if __name__ == "__main__":
    unittest.main() 