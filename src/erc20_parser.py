"""
ERC-20 交易解析模块
基于 Alchemy API 实现基本的 ERC-20 交易解析功能
"""

import logging
import os
import json
from typing import List, Dict, TypedDict, Optional
from web3 import Web3
from web3.types import TxReceipt
from dotenv import load_dotenv
from decimal import Decimal
from functools import lru_cache
import requests
import time

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 类型定义
ERC20TransactionArgs = TypedDict('ERC20TransactionArgs', {
    'to': str,
    'from': str,
    'value': float,
})

ERC20Transaction = TypedDict('ERC20Transaction', {
    'contract_address': str,
    'transaction_hash': bytes,
    'from': str,
    'to': str,
    'amount': float,
    'token_type': str,
    'token_symbol': str,
    'token_name': str,
    'dex': Optional[str]
})

# 获取 Alchemy API URL
ALCHEMY_API_URL = os.getenv('ALCHEMY_API_URL')
if not ALCHEMY_API_URL:
    raise ValueError("未找到 ALCHEMY_API_URL 环境变量")

logger.info(f"使用 Alchemy API URL: {ALCHEMY_API_URL}")

# 初始化 Web3
w3 = Web3(Web3.HTTPProvider(ALCHEMY_API_URL))

# 测试网络连接
try:
    block_number = w3.eth.block_number
    logger.info(f"成功连接到以太坊网络，当前区块号: {block_number}")
except Exception as e:
    logger.error(f"连接以太坊网络失败: {str(e)}")
    raise

# ERC20 代币 ABI
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
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

# 常见代币合约地址映射
COMMON_TOKENS = {
    '0xdac17f958d2ee523a2206206994597c13d831ec7': 'USDT',
    '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48': 'USDC',
    '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2': 'WETH',
    '0x2260fac5e5542a773aa44fbcfedf7c193bc2c599': 'WBTC',
    '0x1f9840a85d5af5bf1d1762f925bdaddc4201f984': 'UNI',
    '0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9': 'AAVE',
    '0x6b175474e89094c44da98b954eedeac495271d0f': 'DAI',
    '0x514910771af9ca656af840dff83e8264ecf986ca': 'LINK',
    '0x0d8775f648430679a709e98d2b0cb6250d2887ef': 'BAT',
    '0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2': 'MKR',
    '0x0bc529c00c6401aef6d220be8c6ea1667f6ad93e': 'YFI',
    '0x4f3afec4e5a3f2a6a1a381f2494fe3e39caf8b4f': 'DGX',
    '0x0f5d2fb29fb7d3cfee444a200298f468908cc942': 'MANA',
    '0x0a0e3f6f5f90f3a18fe3e3e0f0f2a5a5e2f1d0c': 'MOVE',
    '0x0b0e3f6f5f90f3a18fe3e3e0f0f2a5a5e2f1d0c': 'MOCA'
}

# 新增配置项
MAX_RETRIES = 3
REQUEST_TIMEOUT = 10
BATCH_SIZE = 1000  # Alchemy API最大批量处理量

# 初始化带重试的Session
session = requests.Session()
adapter = requests.adapters.HTTPAdapter(max_retries=MAX_RETRIES)
session.mount('https://', adapter)

# 新增代币信息缓存（24小时过期）
TOKEN_CACHE = {}
CACHE_EXPIRY = 86400  

@lru_cache(maxsize=1024)
def get_token_info(contract_address: str) -> Dict:
    """带缓存和重试的代币信息查询"""
    cached = TOKEN_CACHE.get(contract_address)
    if cached and time.time() - cached['timestamp'] < CACHE_EXPIRY:
        return cached['data']
    
    try:
        contract = w3.eth.contract(address=contract_address, abi=ERC20_ABI)
        info = {
            'symbol': contract.functions.symbol().call(),
            'name': contract.functions.name().call(),
            'decimals': contract.functions.decimals().call()
        }
        TOKEN_CACHE[contract_address] = {
            'data': info,
            'timestamp': time.time()
        }
        return info
    except Exception as e:
        logger.warning(f"代币信息查询失败: {contract_address} - {str(e)}")
        return default_token_info(contract_address)

def parse_transfer_log(log: Dict) -> Optional[Dict]:
    """解析单个转账日志"""
    try:
        # 获取代币信息
        token_info = get_token_info(log['address'])
        
        # 解析转账数据
        data = log['data']
        topics = log['topics']
        
        # 解析发送方和接收方地址
        from_address = '0x' + topics[1].hex()[-40:]
        to_address = '0x' + topics[2].hex()[-40:]
        
        # 解析转账金额
        amount_hex = data[2:]  # 移除 '0x' 前缀
        amount = int(amount_hex, 16) / (10 ** token_info['decimals'])
        
        return {
            'token_address': log['address'],
            'token_symbol': token_info['symbol'],
            'token_name': token_info['name'],
            'from_address': from_address,
            'to_address': to_address,
            'amount': amount,
            'transaction_hash': log['transactionHash'].hex()
        }
    except Exception as e:
        logger.warning(f"解析转账记录失败: {str(e)}")
        return None

def parse_transaction_receipt(receipt: Dict) -> List[Dict]:
    """解析交易收据中的所有转账记录"""
    transfers = []
    
    # 遍历所有日志
    for log in receipt['logs']:
        # 检查是否是转账事件
        if len(log['topics']) == 3 and log['topics'][0].hex() == '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef':
            transfer = parse_transfer_log(log)
            if transfer:
                transfers.append(transfer)
    
    return transfers

def analyze_transaction(tx_hash: str) -> List[Dict]:
    """分析单个交易"""
    try:
        # 获取交易收据
        receipt = w3.eth.get_transaction_receipt(tx_hash)
        
        # 解析转账记录
        transfers = parse_transaction_receipt(receipt)
        
        # 记录解析结果
        for i, transfer in enumerate(transfers, 1):
            logger.info(f"ERC-20 代币转账 #{i}")
            logger.info(f"代币符号: {transfer['token_symbol']}")
            logger.info(f"代币名称: {transfer['token_name']}")
            logger.info(f"合约地址: {transfer['token_address']}")
            logger.info(f"发送方: {transfer['from_address']}")
            logger.info(f"接收方: {transfer['to_address']}")
            logger.info(f"转账金额: {transfer['amount']}")
            logger.info(f"交易哈希: {transfer['transaction_hash']}")
            logger.info("-" * 50)
        
        return transfers
    except Exception as e:
        logger.error(f"分析交易失败: {str(e)}")
        return []

def parse_hex_value(hex_str: str) -> int:
    """
    解析十六进制字符串为整数
    
    Args:
        hex_str: 十六进制字符串，可以带有或不带有'0x'前缀
        
    Returns:
        解析后的整数值
        
    Raises:
        ValueError: 如果输入不是有效的十六进制字符串
    """
    try:
        # 移除可能存在的'0x'前缀
        clean_hex = hex_str.lower().replace('0x', '')
        return int(clean_hex, 16)
    except (ValueError, AttributeError) as e:
        raise ValueError(f"无效的十六进制值: {hex_str}") from e

def calculate_token_amount(value: int, decimals: int) -> Decimal:
    """
    计算代币实际金额
    
    Args:
        value: 原始代币数量
        decimals: 代币小数位数
        
    Returns:
        Decimal: 实际代币金额
    """
    try:
        return Decimal(value) / Decimal(10 ** decimals)
    except Exception as e:
        raise ValueError(f"计算代币金额时出错: value={value}, decimals={decimals}") from e

def parse_erc20_transactions(block_number: int) -> List[ERC20Transaction]:
    """
    使用 Alchemy API 解析指定区块中的 ERC-20 转账交易
    
    Args:
        block_number: 要解析的区块号
        
    Returns:
        List[ERC20Transaction]: 解析出的 ERC-20 转账交易列表
    """
    w3 = get_web3()
    
    try:
        # 使用 Alchemy API 获取资产转账信息
        params = {
            "fromBlock": hex(block_number),
            "toBlock": hex(block_number),
            "category": ["erc20"],
            "withMetadata": True,
            "excludeZeroValue": True,
        }
        
        logger.info(f"发送请求参数: {params}")
        
        # 发送请求
        response = w3.provider.make_request("alchemy_getAssetTransfers", [params])
        logger.debug(f"API 响应: {response}")
        
        if 'error' in response:
            raise Exception(f"API 错误: {response['error']}")
            
        if 'result' not in response or 'transfers' not in response['result']:
            logger.warning(f"API 响应格式不正确: {response}")
            return []
            
        transfers = response['result']['transfers']
        logger.info(f"获取到 {len(transfers)} 笔转账记录")
        
        # 存储解析结果
        parsed_transactions: List[ERC20Transaction] = []
        
        # 解析每个转账
        for transfer in transfers:
            try:
                if 'rawContract' not in transfer:
                    logger.warning(f"转账记录缺少rawContract字段: {transfer}")
                    continue
                    
                raw_contract = transfer['rawContract']
                
                # 验证必要字段
                required_fields = ['address', 'value', 'decimal']
                if not all(field in raw_contract for field in required_fields):
                    logger.warning(f"转账记录缺少必要字段: {raw_contract}")
                    continue
                
                # 获取代币合约地址
                contract_address = raw_contract['address']
                
                # 解析代币金额和小数位数
                try:
                    value = parse_hex_value(raw_contract['value'])
                    decimal = parse_hex_value(raw_contract['decimal'])
                except ValueError as e:
                    logger.warning(f"解析代币数值时出错: {str(e)}, transfer: {transfer}")
                    continue
                
                # 计算实际代币金额
                try:
                    token_amount = calculate_token_amount(value, decimal)
                except ValueError as e:
                    logger.warning(f"计算代币金额时出错: {str(e)}, transfer: {transfer}")
                    continue
                
                # 验证转账地址
                if not all(k in transfer for k in ['to', 'from', 'hash']):
                    logger.warning(f"转账记录缺少地址信息: {transfer}")
                    continue
                
                # 获取代币信息
                token_type = transfer.get('asset', 'UNKNOWN')
                token_symbol = transfer.get('symbol', 'UNKNOWN')
                token_name = transfer.get('name', 'Unknown Token')
                
                # 尝试识别DEX
                dex = None
                if 'to' in transfer and 'from' in transfer:
                    # 这里可以添加DEX地址的映射逻辑
                    # 例如：如果接收地址是Uniswap V2的合约地址，则标记为Uniswap V2
                    pass
                
                parsed_tx: ERC20Transaction = {
                    'contract_address': contract_address,
                    'transaction_hash': bytes.fromhex(transfer['hash'][2:]),  # 移除 '0x' 前缀
                    'from': transfer['from'],
                    'to': transfer['to'],
                    'amount': float(token_amount),
                    'token_type': token_type,
                    'token_symbol': token_symbol,
                    'token_name': token_name,
                    'dex': dex
                }
                parsed_transactions.append(parsed_tx)
                logger.debug(f"成功解析交易: {parsed_tx}")
            except Exception as e:
                logger.warning(f"解析转账记录时出错: {str(e)}, transfer: {transfer}")
                continue
        
        logger.info(f"在区块 {block_number} 中找到 {len(parsed_transactions)} 笔 ERC-20 转账交易")
        return parsed_transactions
        
    except Exception as e:
        logger.error(f"解析区块 {block_number} 时发生错误: {str(e)}")
        raise

def get_latest_block(w3: Web3) -> int:
    """获取最新区块号"""
    try:
        block_number = w3.eth.block_number
        logger.info(f"获取到最新区块号: {block_number}")
        return block_number
    except Exception as e:
        logger.error(f"获取最新区块号时发生错误: {str(e)}")
        raise

def parse_block_transactions(block_number: int) -> List[ERC20Transaction]:
    """批量获取区块ERC-20交易（性能提升关键）"""
    params = {
        "id": 1,
        "jsonrpc": "2.0",
        "method": "alchemy_getAssetTransfers",
        "params": [{
            "fromBlock": hex(block_number),
            "toBlock": hex(block_number),
            "category": ["erc20"],
            "withMetadata": True,
            "excludeZeroValue": True,
            "maxCount": hex(BATCH_SIZE)
        }]
    }
    
    try:
        response = session.post(
            ALCHEMY_API_URL,
            json=params,
            timeout=REQUEST_TIMEOUT
        ).json()
        
        if 'error' in response:
            handle_api_error(response['error'])
            
        return process_batch_transfers(response.get('result', {}).get('transfers', []))
        
    except Exception as e:
        logger.error(f"批量请求失败: {str(e)}")
        return []

def process_batch_transfers(transfers: List) -> List[ERC20Transaction]:
    """并行处理批量转账数据"""
    from concurrent.futures import ThreadPoolExecutor
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(process_single_transfer, transfers))
    
    return [tx for tx in results if tx is not None]

def process_single_transfer(transfer: Dict) -> Optional[ERC20Transaction]:
    """单笔转账处理（线程安全）"""
    try:
        raw = transfer['rawContract']
        return {
            'contract_address': raw['address'],
            'transaction_hash': bytes.fromhex(transfer['hash'][2:]),
            'from': transfer['from'],
            'to': transfer['to'],
            'amount': parse_transfer_value(raw['value'], raw['decimal']),
            'token_symbol': transfer.get('asset', 'UNKNOWN'),
            'token_name': transfer.get('name', 'Unknown Token'),
            'dex': detect_dex(transfer['to'])
        }
    except Exception as e:
        logger.debug(f"转账处理失败: {str(e)}")
        return None 