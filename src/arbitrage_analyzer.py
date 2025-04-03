"""
套利分析进阶模块
实现更复杂的套利路径分析和利润计算
"""

import os
import sys
import logging
from typing import List, Dict, Optional, TypedDict
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from web3 import Web3
from dotenv import load_dotenv
from tqdm import tqdm

# 加载环境变量
load_dotenv()

# 获取项目根目录的绝对路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 将 goldphish 目录添加到 Python 路径
sys.path.append(os.path.join(project_root, 'goldphish'))

from backtest.gather_samples.models import Arbitrage, ArbitrageCycle, ArbitrageCycleExchange
from backtest.gather_samples.analyses import get_arbitrage_from_receipt_if_exists, get_addr_to_movements, get_potential_exchanges
from erc20_parser import parse_transaction_receipt

# 配置日志
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# 初始化 Web3
w3 = Web3(Web3.HTTPProvider(os.getenv('ALCHEMY_API_URL')))

# 已知的 DEX 合约地址
KNOWN_DEX_ADDRESSES = {
    '0x7a250d5630b4cf539739df2c5dacb4c659f2488d': 'Uniswap V2 Router',
    '0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45': 'Uniswap V3 Router',
    '0xe592427a0aece92de3edee1f18e0157c05861564': 'Uniswap V3 Router',
    '0x1b02da8cb0d097eb8d57a175b88c7d8b47997506': 'SushiSwap Router',
    '0x8c4b866f3c9f9cc5ef62a5a6ced0a12d95b7b9b5': 'SushiSwap Router'
}

# 将DEX地址转换为集合，用于快速查找
KNOWN_DEX_SET = set(addr.lower() for addr in KNOWN_DEX_ADDRESSES.keys())

@dataclass
class Transfer:
    """转账记录数据类"""
    from_address: str
    to_address: str
    token_address: str
    amount: float
    block_number: int
    transaction_hash: str
    is_dex: bool = False
    dex_name: Optional[str] = None

@dataclass
class ArbitragePath:
    """套利路径数据类"""
    token_in: str
    token_out: str
    exchange: str
    amount_in: Decimal
    amount_out: Decimal
    timestamp: datetime

@dataclass
class ArbitrageOpportunity:
    """套利机会数据类"""
    block_number: int
    transaction_hash: str
    timestamp: datetime
    token_address: str
    token_symbol: str
    token_name: str
    amount: Decimal
    profit: Decimal
    path: List[ArbitragePath]
    gas_cost: Decimal
    net_profit: Decimal
    execution_time: float  # 执行时间（毫秒）

def format_transaction_hash(hash_str: str) -> bytes:
    """
    格式化交易哈希为字节格式
    
    Args:
        hash_str: 交易哈希字符串
        
    Returns:
        bytes: 格式化后的交易哈希
    """
    try:
        # 移除 '0x' 前缀
        hash_str = hash_str.replace('0x', '')
        # 补齐到64个字符
        hash_str = hash_str.zfill(64)
        return bytes.fromhex(hash_str)
    except Exception as e:
        logger.error(f"格式化交易哈希时出错: {str(e)}, hash: {hash_str}")
        return b''

def safe_int_conversion(value: str) -> int:
    """
    安全地将字符串转换为整数，处理大数和异常情况
    
    Args:
        value: 要转换的字符串值
        
    Returns:
        int: 转换后的整数值，如果转换失败则返回0
    """
    try:
        # 先转换为 Decimal 以处理科学记数法
        decimal_value = Decimal(value)
        # 检查是否超出范围
        if decimal_value > 2**256 - 1:  # 以太坊uint256的最大值
            logger.warning(f"数值超出范围: {value}")
            return 0
        return int(decimal_value)
    except Exception as e:
        logger.warning(f"转换数值失败: {str(e)}, value: {value}")
        return 0

def analyze_complex_path(transfers: List[Dict]) -> Optional[ArbitrageOpportunity]:
    """
    分析复杂的套利路径
    
    Args:
        transfers: 代币转账记录列表
        
    Returns:
        Optional[ArbitrageOpportunity]: 如果找到套利机会则返回套利信息，否则返回 None
    """
    try:
        logger.info(f"开始分析 {len(transfers)} 笔转账记录")
        
        # 转换转账记录格式以匹配 analyses.py 的要求
        formatted_transfers = []
        dex_addresses = set()
        
        for transfer in transfers:
            try:
                # 统一使用小写地址进行比较
                from_addr = transfer['from_address'].lower()
                to_addr = transfer['to_address'].lower()
                
                logger.info(f"处理转账记录: from={from_addr}, to={to_addr}")
                
                # 检查是否是已知的 DEX 地址
                is_dex = False
                dex_name = None
                for dex_address, name in KNOWN_DEX_ADDRESSES.items():
                    logger.info(f"比较地址: {from_addr} 和 {to_addr} 与 DEX 地址 {dex_address}")
                    if from_addr == dex_address or to_addr == dex_address:
                        is_dex = True
                        dex_name = name
                        dex_addresses.add(dex_address)  # 保持原始大小写
                        logger.info(f"找到 DEX 交易: {dex_name} ({dex_address})")
                        break
                
                if not is_dex:
                    logger.info(f"不是 DEX 交易，跳过")
                    continue
                
                formatted_transfer = {
                    'address': str(transfer['token_address']).lower(),
                    'transactionHash': format_transaction_hash(transfer['transaction_hash']),
                    'args': {
                        'to': to_addr,
                        'from': from_addr,
                        'value': int(float(transfer['amount']) * (10 ** 18))
                    },
                    'dex': dex_name
                }
                formatted_transfers.append(formatted_transfer)
                logger.info(f"添加格式化转账记录: {formatted_transfer}")
            except Exception as e:
                logger.warning(f"转换转账记录格式时出错: {str(e)}, transfer: {transfer}")
                continue

        if not formatted_transfers:
            logger.info("没有找到有效的 DEX 交易")
            return None

        # 使用 analyses.py 中的函数分析套利路径
        addr_to_movements = get_addr_to_movements(formatted_transfers)
        logger.info(f"获取到地址移动记录: {addr_to_movements}")
        
        # 确保所有已知的 DEX 地址都被包含在 potential_exchanges 中
        potential_exchanges = list(dex_addresses)
        logger.info(f"找到潜在的交易所地址: {potential_exchanges}")
        
        if not potential_exchanges:
            logger.info("未找到潜在的交易所地址")
            return None
            
        # 构建套利路径
        paths = []
        for exchange in potential_exchanges:
            if exchange not in addr_to_movements:
                logger.warning(f"交易所地址 {exchange} 不在移动记录中")
                continue
                
            movements = addr_to_movements[exchange]
            logger.info(f"分析交易所 {exchange} 的移动记录: {movements}")
            
            for in_transfer in movements.get('in', []):
                for out_transfer in movements.get('out', []):
                    path = ArbitragePath(
                        token_in=in_transfer['address'],
                        token_out=out_transfer['address'],
                        exchange=exchange,
                        amount_in=Decimal(in_transfer['args']['value']) / Decimal(10**18),
                        amount_out=Decimal(out_transfer['args']['value']) / Decimal(10**18),
                        timestamp=datetime.now()
                    )
                    paths.append(path)
                    logger.info(f"添加套利路径: {path}")
        
        if not paths:
            logger.info("未找到有效的套利路径")
            return None
            
        # 计算利润
        profit = calculate_profit(paths)
        logger.info(f"计算得到的利润: {profit}")
        
        if profit <= 0:
            logger.info("利润不为正数")
            return None
            
        # 获取区块信息
        block_number = 0  # 从交易中获取
        transaction_hash = ""  # 从交易中获取
        
        # 构建套利机会对象
        opportunity = ArbitrageOpportunity(
            block_number=block_number,
            transaction_hash=transaction_hash,
            timestamp=datetime.now(),
            token_address=paths[0].token_in,
            token_symbol="",  # 从代币信息中获取
            token_name="",  # 从代币信息中获取
            amount=paths[0].amount_in,
            profit=profit,
            path=paths,
            gas_cost=Decimal('0'),  # 从交易中获取
            net_profit=profit,  # 需要减去 gas 成本
            execution_time=0.0  # 需要实际测量
        )
        logger.info(f"找到套利机会: {opportunity}")
        return opportunity
        
    except Exception as e:
        logger.error(f"分析复杂套利路径时发生错误: {str(e)}")
        return None

def calculate_profit(path: List[ArbitragePath]) -> Decimal:
    """
    计算套利路径的利润
    
    Args:
        path: 套利路径列表
        
    Returns:
        Decimal: 计算得到的利润
    """
    try:
        if not path:
            return Decimal('0')
            
        # 计算初始投入
        initial_amount = path[0].amount_in
        
        # 计算最终获得
        final_amount = path[-1].amount_out
        
        # 计算利润
        profit = final_amount - initial_amount
        
        return profit
        
    except Exception as e:
        logger.error(f"计算利润时发生错误: {str(e)}")
        return Decimal('0')

def optimize_performance(transfers: List[Dict]) -> List[Dict]:
    """
    优化套利检测性能
    
    Args:
        transfers: 原始转账记录列表
        
    Returns:
        List[Dict]: 优化后的转账记录列表
    """
    try:
        # 1. 过滤掉金额过小的转账
        min_amount = Decimal('0.0001')
        filtered_transfers = [
            transfer for transfer in transfers 
            if Decimal(str(transfer['amount'])) >= min_amount
        ]
        
        # 2. 按代币地址分组，减少重复查询
        token_groups = {}
        for transfer in filtered_transfers:
            token_address = transfer['token_address']
            if token_address not in token_groups:
                token_groups[token_address] = []
            token_groups[token_address].append(transfer)
            
        # 3. 按时间戳排序，便于后续分析
        sorted_transfers = []
        for token_address, token_transfers in token_groups.items():
            sorted_transfers.extend(sorted(
                token_transfers,
                key=lambda x: x.get('timestamp', datetime.min)
            ))
            
        return sorted_transfers
        
    except Exception as e:
        logger.error(f"优化性能时发生错误: {str(e)}")
        return transfers

def find_arbitrage_opportunities(
    block_number: int,
    transfers: List[Dict],
    min_profit: Decimal = Decimal('0.01')
) -> List[ArbitrageOpportunity]:
    """
    在指定区块中寻找套利机会
    
    Args:
        block_number: 区块号
        transfers: 代币转账记录列表
        min_profit: 最小利润阈值
        
    Returns:
        List[ArbitrageOpportunity]: 套利机会列表
    """
    try:
        # 1. 优化性能
        optimized_transfers = optimize_performance(transfers)
        
        # 2. 分析套利路径
        opportunities = []
        for i in range(0, len(optimized_transfers), 10):  # 每次处理10笔交易
            batch = optimized_transfers[i:i+10]
            opportunity = analyze_complex_path(batch)
            if opportunity and opportunity.profit >= min_profit:
                opportunities.append(opportunity)
                
        # 3. 按利润排序
        opportunities.sort(key=lambda x: x.profit, reverse=True)
        
        return opportunities
        
    except Exception as e:
        logger.error(f"寻找套利机会时发生错误: {str(e)}")
        return []

def get_latest_block_data() -> Optional[Dict]:
    """
    获取最新区块数据
    
    Returns:
        Optional[Dict]: 区块数据，如果获取失败则返回 None
    """
    try:
        # 获取最新区块号
        latest_block_number = w3.eth.block_number
        logger.info(f"获取到最新区块号: {latest_block_number}")
        
        # 获取区块数据
        block = w3.eth.get_block(latest_block_number, full_transactions=True)
        if not block:
            logger.error("获取区块数据失败")
            return None
            
        # 获取区块中的交易收据
        transactions = []
        for tx in block['transactions']:
            try:
                receipt = w3.eth.get_transaction_receipt(tx['hash'])
                if receipt:
                    transactions.append(receipt)
            except Exception as e:
                logger.warning(f"获取交易收据失败: {str(e)}")
                continue
                
        logger.info(f"获取到 {len(transactions)} 笔交易")
        return {
            'block_number': latest_block_number,
            'transactions': transactions,
            'timestamp': datetime.fromtimestamp(block['timestamp'])
        }
        
    except Exception as e:
        logger.error(f"获取区块数据时发生错误: {str(e)}")
        return None

def process_block_transactions(block_data: Dict) -> List[Dict]:
    """
    处理区块中的交易数据
    
    Args:
        block_data: 区块数据
        
    Returns:
        List[Dict]: 处理后的转账记录列表
    """
    transfers = []
    try:
        # 常见代币精度映射
        token_decimals = {
            '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2': 18,  # WETH
            '0xdAC17F958D2ee523a2206206994597C13D831ec7': 6,   # USDT
            '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48': 6,   # USDC
            '0x6B175474E89094C44Da98b954EedeAC495271d0F': 18,  # DAI
        }
        
        def format_address(hex_str: str) -> str:
            """格式化地址"""
            try:
                # 移除 '0x' 前缀
                hex_str = hex_str.replace('0x', '')
                # 补齐到40个字符
                hex_str = hex_str.zfill(40)
                return '0x' + hex_str
            except Exception:
                return ''
                
        def extract_transfer_amount(data: str) -> Optional[int]:
            """从转账事件数据中提取金额"""
            try:
                # 移除 '0x' 前缀
                data = data.replace('0x', '')
                # 转账金额是前32字节（64个十六进制字符）
                amount_hex = data[:64]
                return int(amount_hex, 16)
            except Exception:
                return None
        
        for tx in block_data['transactions']:
            # 处理 ERC20 转账事件
            for log in tx['logs']:
                try:
                    if len(log['topics']) == 3:  # ERC20 Transfer 事件
                        token_address = format_address(log['address'])
                        if not token_address:
                            continue
                            
                        from_address = format_address(log['topics'][1].hex())
                        to_address = format_address(log['topics'][2].hex())
                        
                        if not from_address or not to_address:
                            continue
                            
                        # 获取代币精度，默认为18
                        decimals = token_decimals.get(token_address, 18)
                        
                        # 提取转账金额
                        amount = extract_transfer_amount(log['data'])
                        if amount is None:
                            continue
                            
                        # 转换为可读格式
                        amount_str = str(amount / (10 ** decimals))
                        
                        transfer = {
                            'transaction_hash': tx['transactionHash'].hex(),
                            'block_number': block_data['block_number'],
                            'timestamp': block_data['timestamp'],
                            'token_address': token_address,
                            'from_address': from_address,
                            'to_address': to_address,
                            'amount': amount_str,
                            'decimals': decimals
                        }
                        transfers.append(transfer)
                except Exception as e:
                    logger.warning(f"处理转账事件失败: {str(e)}")
                    continue
                    
        logger.info(f"处理完成，共 {len(transfers)} 笔转账记录")
        return transfers
        
    except Exception as e:
        logger.error(f"处理交易数据时发生错误: {str(e)}")
        return []

def process_transfers(transfers: List[Dict]) -> List[Transfer]:
    """处理转账记录"""
    processed_transfers = []
    
    # 使用analyses.py中的函数组织转账记录
    formatted_transfers = []
    for transfer in transfers:
        formatted_transfer = {
            'address': transfer['token_address'].lower(),
            'transactionHash': format_transaction_hash(transfer['transaction_hash']),
            'args': {
                'to': transfer['to_address'].lower(),
                'from': transfer['from_address'].lower(),
                'value': int(float(transfer['amount']) * (10 ** 18))
            }
        }
        formatted_transfers.append(formatted_transfer)
    
    # 获取地址移动记录
    addr_to_movements = get_addr_to_movements(formatted_transfers)
    
    # 获取潜在的交易所地址
    potential_exchanges = get_potential_exchanges(
        {'transactionHash': b'', 'from': ''},  # 简化版本
        addr_to_movements
    )
    
    # 合并已知DEX地址和潜在DEX地址
    all_dex_addresses = KNOWN_DEX_SET.union(potential_exchanges)
    
    for transfer in transfers:
        try:
            from_addr = transfer['from_address'].lower()
            to_addr = transfer['to_address'].lower()
            
            # 检查是否是DEX交易
            is_dex = False
            dex_name = None
            
            # 首先检查是否是已知DEX地址
            if from_addr in KNOWN_DEX_SET or to_addr in KNOWN_DEX_SET:
                is_dex = True
                dex_name = KNOWN_DEX_ADDRESSES.get(from_addr) or KNOWN_DEX_ADDRESSES.get(to_addr)
            # 然后检查是否是潜在DEX地址
            elif from_addr in potential_exchanges or to_addr in potential_exchanges:
                is_dex = True
                dex_name = "Unknown DEX"
            
            # 创建Transfer对象
            processed_transfer = Transfer(
                from_address=transfer['from_address'],
                to_address=transfer['to_address'],
                token_address=transfer['token_address'],
                amount=float(transfer['amount']),
                block_number=transfer['block_number'],
                transaction_hash=transfer['transaction_hash'],
                is_dex=is_dex,
                dex_name=dex_name
            )
            processed_transfers.append(processed_transfer)
            
            if is_dex:
                logger.info(f"发现DEX交易: {dex_name}")
                logger.info(f"交易详情: 从 {transfer['from_address']} 到 {transfer['to_address']}")
                logger.info(f"代币地址: {transfer['token_address']}")
                logger.info(f"转账金额: {transfer['amount']}")
            
        except Exception as e:
            logger.error(f"处理转账记录时出错: {str(e)}")
            continue
            
    logger.info(f"处理完成，共 {len(processed_transfers)} 笔转账记录")
    logger.info(f"其中DEX交易数量: {len([t for t in processed_transfers if t.is_dex])}")
    return processed_transfers

def main():
    """
    主函数：按步骤分析套利机会
    """
    try:
        # 步骤 0: 获取最新区块
        logger.info("\n步骤 0: 获取最新区块")
        latest_block = w3.eth.block_number
        logger.info(f"获取到最新区块号: {latest_block}")
        logger.info("正在获取区块数据...")
        
        # 获取区块数据
        block = w3.eth.get_block(latest_block, full_transactions=True)
        if not block:
            logger.error("获取区块数据失败")
            return
            
        # 获取区块中的交易收据
        logger.info("正在获取交易收据...")
        transactions = []
        failed_tx_count = 0
        for tx in tqdm(block['transactions'], desc="获取交易收据"):
            try:
                receipt = w3.eth.get_transaction_receipt(tx['hash'])
                if receipt and receipt['status'] == 1:  # 只处理成功的交易
                    transactions.append(receipt)
            except Exception as e:
                failed_tx_count += 1
                logger.debug(f"获取交易收据失败: {str(e)}")
                continue
                
        logger.info(f"区块中包含 {len(transactions)} 笔成功交易，{failed_tx_count} 笔失败交易")
        
        # 步骤 1: 数据收集 - 解析 ERC-20 转账记录
        logger.info("\n步骤 1: 数据收集 - 解析 ERC-20 转账记录")
        all_transfers = []
        failed_parse_count = 0
        for tx in tqdm(transactions, desc="解析 ERC-20 转账"):
            try:
                # 只提取当前交易的 transfer 事件
                transfer_records = parse_transaction_receipt(tx)
                if transfer_records:
                    valid_records = [record for record in transfer_records if all(key in record for key in ['token_address', 'from_address', 'to_address', 'amount'])]
                    all_transfers.extend(valid_records)
            except Exception as e:
                failed_parse_count += 1
                logger.debug(f"解析转账记录失败: {str(e)}")
                continue
                
        logger.info(f"解析出 {len(all_transfers)} 笔有效 ERC-20 转账记录，{failed_parse_count} 笔解析失败")
        
        # 格式化转账记录以匹配 analyses.py 的要求
        formatted_transfers = []
        failed_format_count = 0
        for transfer in all_transfers:
            try:
                # 安全地转换数值
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
                failed_format_count += 1
                logger.debug(f"格式化转账记录时出错: {str(e)}")
                continue
                
        logger.info(f"成功格式化 {len(formatted_transfers)} 笔转账记录，{failed_format_count} 笔格式化失败")
        
        if not formatted_transfers:
            logger.warning("没有有效的转账记录可以分析")
            return
            
        # 步骤 2: 数据预处理
        logger.info("\n步骤 2: 数据预处理")
        addr_to_movements = get_addr_to_movements(formatted_transfers)
        
        # 输出每个地址的转账记录
        for addr, movements in addr_to_movements.items():
            logger.info(f"- 地址 {addr[:6]}...{addr[-4:]}")
            if 'in' in movements:
                in_tokens = [{t['address']: t['args']['value']} for t in movements['in']]
                logger.info(f"  转入: {in_tokens}")
            if 'out' in movements:
                out_tokens = [{t['address']: t['args']['value']} for t in movements['out']]
                logger.info(f"  转出: {out_tokens}")
        
        # 步骤 3: DEX识别
        logger.info("\n步骤 3: DEX识别")
        for tx in transactions:
            potential_exchanges = get_potential_exchanges(tx, addr_to_movements)
            if potential_exchanges:
                for dex in potential_exchanges:
                    logger.info(f"- DEX: {dex[:6]}...{dex[-4:]}")
                    if dex in addr_to_movements:
                        movements = addr_to_movements[dex]
                        if 'in' in movements:
                            logger.info(f"  转入代币: {movements['in'][0]['address']}")
                        if 'out' in movements:
                            logger.info(f"  转出代币: {movements['out'][0]['address']}")
                
                # 步骤 4: 套利分析
                logger.info("\n步骤 4: 套利分析")
                logger.info(f"开始分析交易 {tx['transactionHash'].hex()} 中的DEX地址")
                logger.info(f"交易发送者: {tx['from']}")
                
                arbitrage = get_arbitrage_from_receipt_if_exists(tx, formatted_transfers)
                
                # 步骤 5: 套利确认
                if arbitrage and arbitrage.only_cycle:
                    logger.info("\n步骤 5: 套利确认")
                    logger.info("✅ 套利机会确认")
                    logger.info(f"- 交易哈希: {arbitrage.txn_hash.hex()}")
                    logger.info(f"- 获利代币: {arbitrage.only_cycle.profit_token}")
                    logger.info(f"- 获利地址: {arbitrage.only_cycle.profit_taker}")
                    logger.info(f"- 获利金额: {arbitrage.only_cycle.profit_amount}")
                    
                    # 输出交易路径详情
                    logger.info("\n交易路径详情:")
                    for i, exchange in enumerate(arbitrage.only_cycle.cycle, 1):
                        logger.info(f"第{i}步: {exchange.token_in} -> {exchange.token_out}")
                        for item in exchange.items:
                            logger.info(f"  DEX地址: {item.address}")
                            logger.info(f"  输入金额: {item.amount_in}")
                            logger.info(f"  输出金额: {item.amount_out}")
                            
                    # 输出交易详情
                    logger.info("\n交易详情:")
                    logger.info(f"- 区块号: {tx['blockNumber']}")
                    logger.info(f"- Gas使用: {tx['gasUsed']}")
                    logger.info(f"- Gas价格: {tx['effectiveGasPrice']}")
                else:
                    logger.info("未发现套利机会")
            
    except Exception as e:
        logger.error(f"分析过程中出错: {str(e)}")
        raise

if __name__ == "__main__":
    main() 