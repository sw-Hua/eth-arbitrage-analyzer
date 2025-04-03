import os
import json
from web3 import Web3
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 初始化 Web3
w3 = Web3(Web3.HTTPProvider(os.getenv('ALCHEMY_API_URL')))

def hexbytes_to_str(obj):
    if isinstance(obj, bytes):
        return obj.hex()
    return obj

def get_block_receipts(block_number):
    # 获取区块信息
    block = w3.eth.get_block(block_number, full_transactions=True)
    
    # 获取所有交易的收据
    receipts = []
    for tx in block.transactions:
        try:
            receipt = w3.eth.get_transaction_receipt(tx['hash'])
            # 转换为可序列化的格式
            serializable_receipt = {
                'transactionHash': hexbytes_to_str(receipt['transactionHash']),
                'from': receipt['from'],
                'to': receipt['to'] if receipt['to'] else None,
                'blockNumber': receipt['blockNumber'],
                'gasUsed': receipt['gasUsed'],
                'effectiveGasPrice': receipt['effectiveGasPrice'],
                'status': receipt['status'],
                'logs': [{
                    'address': log['address'],
                    'topics': [hexbytes_to_str(topic) for topic in log['topics']],
                    'data': hexbytes_to_str(log['data']),
                    'blockNumber': log['blockNumber'],
                    'transactionHash': hexbytes_to_str(log['transactionHash']),
                    'logIndex': log['logIndex'],
                    'blockHash': hexbytes_to_str(log['blockHash'])
                } for log in receipt['logs']]
            }
            receipts.append(serializable_receipt)
        except Exception as e:
            print(f"Error getting receipt for tx {tx['hash'].hex()}: {str(e)}")
    
    return receipts

if __name__ == "__main__":
    block_number = 17518743
    receipts = get_block_receipts(block_number)
    
    # 保存到文件
    output_file = f"block_{block_number}_receipts.json"
    with open(output_file, 'w') as f:
        json.dump(receipts, f, indent=2)
    
    print(f"已保存 {len(receipts)} 个交易收据到 {output_file}") 