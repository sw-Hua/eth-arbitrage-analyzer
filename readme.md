2.2 ERC-20 基础上的套利识别与路径提取算法实现

#### 函数：`get_addr_to_movements(txns)`

##### 功能描述

为了支持后续对 DEX 交换行为的识别，我们首先需要构建每个地址的资金流向图。该图通过识别标准 ERC-20 转账中每个地址的 in/out 行为，建立 `address → MovementDesc` 的映射结构，为后续路径分析提供语义基础。

输入数据结构采用标准化的ERC-20转账记录格式（也就是我们之前生成的ERC-20的数据结构）:

```json
ERC20Transaction = {
    'address': str,        # 代币合约地址
    'transactionHash': str,# 交易哈希
    'args': {
        'from': str,       # 发送方地址
        'to': str,        # 接收方地址
        'value': int      # 转账金额
    }
}
```

输出采用双向资金流动映射结构:

```json
MovementDesc = {
    'in': List[ERC20Transaction],  # 转入记录
    'out': List[ERC20Transaction]  # 转出记录
}
```

test_addr_movements.p: [eth-arbitrage-analyzer/src/test_addr_movements.py at main · sw-Hua/eth-arbitrage-analyzer · GitHub](https://github.com/sw-Hua/eth-arbitrage-analyzer/blob/main/src/test_addr_movements.py)

在中`test_addr_movements.py`我们实现调用了`get_addr_to_movements`输入数据`block_17518743_transfers.json`

```python
addr_movements = get_addr_to_movements(transfers)
```

最终生成`block_17518743_movements.json`:

[eth-arbitrage-analyzer/block_17518743_movements.json at main · sw-Hua/eth-arbitrage-analyzer · GitHub](https://github.com/sw-Hua/eth-arbitrage-analyzer/blob/main/block_17518743_movements.json)

```json
{
  "0x403e5994e97d065d659de527ba2e6cdcb3d2eb2e": {
    "in": [
      {
        "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "transactionHash": "0x804eacc90d5a3c165b96344ddad0149e32df095d37597b17f2d678f44394e010",
        "args": {
          "from": "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",
          "to": "0x403e5994e97d065d659de527ba2e6cdcb3d2eb2e",
          "value": 120000000000000000
        }
      },
      {
        "address": "0x904F1B26ddBd7ed3Bc6b7A8a55ff22BeBa2279c3",
        "transactionHash": "0x3566164077f3e6d4fb27077c8cc156d5a2f788e51f1ba26a346f6550335efc56",
        "args": {
          "from": "0x6b75d8af000000e20b7a7ddf000ba900b4009a80",
          "to": "0x403e5994e97d065d659de527ba2e6cdcb3d2eb2e",
          "value": 2793998044714765209567232
        }
      },
      {
        "address": "0x904F1B26ddBd7ed3Bc6b7A8a55ff22BeBa2279c3",
        "transactionHash": "0x2f0ed1572dbb9efd294f3e7beae89aae694e585dfba709f023b53a62ff04efcd",
        "args": {
          "from": "0xb2eca60a2712b1669aaeae7c521f32984712a75b",
          "to": "0x403e5994e97d065d659de527ba2e6cdcb3d2eb2e",
          "value": 1377221154860771388203367
        }
      },
      {
        "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "transactionHash": "0xcb341a944bc345ccc37d351636aaa4b53ab912728b86acd08d9870467c73dcd8",
        "args": {
          "from": "0x6b75d8af000000e20b7a7ddf000ba900b4009a80",
          "to": "0x403e5994e97d065d659de527ba2e6cdcb3d2eb2e",
          "value": 304647807036293120
        }
      },
      {
        "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "transactionHash": "0xb6f1e2747f2c67fc32bc3d5b966585e7f85270ea70daf3143d5785defb3368fc",
        "args": {
          "from": "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",
          "to": "0x403e5994e97d065d659de527ba2e6cdcb3d2eb2e",
          "value": 200000000000000000
        }
      },
      {
        "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "transactionHash": "0xfe0529c043206596b5203cb31a407f276a2a02814900aca25b2d07a617bc2f27",
        "args": {
          "from": "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad",
          "to": "0x403e5994e97d065d659de527ba2e6cdcb3d2eb2e",
          "value": 200000000000000000
        }
      }
    ],
    "out": [
      {
        "address": "0x904F1B26ddBd7ed3Bc6b7A8a55ff22BeBa2279c3",
        "transactionHash": "0x804eacc90d5a3c165b96344ddad0149e32df095d37597b17f2d678f44394e010",
        "args": {
          "from": "0x403e5994e97d065d659de527ba2e6cdcb3d2eb2e",
          "to": "0x5fdbb55e098028acc1e0ab3f7864eda802897d8b",
          "value": 1064022450341622239458155
        }
      },
      {
        "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "transactionHash": "0x3566164077f3e6d4fb27077c8cc156d5a2f788e51f1ba26a346f6550335efc56",
        "args": {
          "from": "0x403e5994e97d065d659de527ba2e6cdcb3d2eb2e",
          "to": "0x6b75d8af000000e20b7a7ddf000ba900b4009a80",
          "value": 309152252772220928
        }
      },
      {
        "address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "transactionHash": "0x2f0ed1572dbb9efd294f3e7beae89aae694e585dfba709f023b53a62ff04efcd",
        "args": {
          "from": "0x403e5994e97d065d659de527ba2e6cdcb3d2eb2e",
          "to": "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",
          "value": 147640326480710670
        }
      },
      {
        "address": "0x904F1B26ddBd7ed3Bc6b7A8a55ff22BeBa2279c3",
        "transactionHash": "0xcb341a944bc345ccc37d351636aaa4b53ab912728b86acd08d9870467c73dcd8",
        "args": {
          "from": "0x403e5994e97d065d659de527ba2e6cdcb3d2eb2e",
          "to": "0x6b75d8af000000e20b7a7ddf000ba900b4009a80",
          "value": 2794553536707203596025856
        }
      },
      {
        "address": "0x904F1B26ddBd7ed3Bc6b7A8a55ff22BeBa2279c3",
        "transactionHash": "0xb6f1e2747f2c67fc32bc3d5b966585e7f85270ea70daf3143d5785defb3368fc",
        "args": {
          "from": "0x403e5994e97d065d659de527ba2e6cdcb3d2eb2e",
          "to": "0xc5ff15336bd6e4b9130dd5aee1af0ecdabb7e01c",
          "value": 1771871419454551360422652
        }
      },
      {
        "address": "0x904F1B26ddBd7ed3Bc6b7A8a55ff22BeBa2279c3",
        "transactionHash": "0xfe0529c043206596b5203cb31a407f276a2a02814900aca25b2d07a617bc2f27",
        "args": {
          "from": "0x403e5994e97d065d659de527ba2e6cdcb3d2eb2e",
          "to": "0x9e71b33f2a6b11a970261fa759edd56312b61056",
          "value": 1724499225447941170501325
        }
      }
    ]
  },
......
  "0xa7888f85bd76deef3bd03d4dbcf57765a49883b3": {
    "in": [
      {
        "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "transactionHash": "0xaf73748220881ed084fe7cec2c3ac183eaf1659a331bec0455cd37abe596f52f",
        "args": {
          "from": "0xba12222222228d8ba445958a75a0704d566bf2c8",
          "to": "0xa7888f85bd76deef3bd03d4dbcf57765a49883b3",
          "value": 467403880
        }
      }
    ],
    "out": [
      {
        "address": "0xA13a9247ea42D743238089903570127DdA72fE44",
        "transactionHash": "0xaf73748220881ed084fe7cec2c3ac183eaf1659a331bec0455cd37abe596f52f",
        "args": {
          "from": "0xa7888f85bd76deef3bd03d4dbcf57765a49883b3",
          "to": "0xba12222222228d8ba445958a75a0704d566bf2c8",
          "value": 464495562420716910169
        }
      }
    ]
  }
}
```

该结构的设计极大地方便了后续判断一个地址是否为 DEX。特别是在 `get_potential_exchanges()` 中，我们通过检查地址是否同时有 in/out 且换币行为明显（不同 token），从而识别出潜在交换节点。

#### 函数 `get_potential_exchanges()`：识别潜在的 DEX 交换节点

##### 功能描述

基于地址的资金流向图,我们需要识别出可能的DEX(去中心化交易所)节点。该函数通过分析地址的代币转入转出模式,排除已知的路由合约和零地址,识别出在单笔交易中进行代币交换的潜在DEX节点。

输入参数:

```python
full_txn: web3.types.TxReceipt  # 交易收据
addr_to_movements: Dict[str, MovementDesc]  # 地址资金流向图
```

输出结构:

```python
Set[str]  # 潜在DEX地址集合
```

##### 实现与验证

test_potential_exchanges.py:

https://github.com/sw-Hua/eth-arbitrage-analyzer/blob/main/src/test_potential_exchanges.py

我们在`test_potential_exchanges.py`中实现了验证:

```python
potential_exchanges = get_potential_exchanges(receipt, addr_movements)
```

该函数分析`block_17518743_movements.json`中的地址行为,生成`potential_exchanges.json`,记录每个交易中识别出的DEX节点及其交易特征。

potential_exchanges.json：

https://github.com/sw-Hua/eth-arbitrage-analyzer/blob/main/potential_exchanges.json

```json
{
  "0x804eacc90d5a3c165b96344ddad0149e32df095d37597b17f2d678f44394e010": {
    "addresses": [
      "0x49d72b6fada7865ce5ed8c5137e73ff5fc18279e",
      "0xa7888f85bd76deef3bd03d4dbcf57765a49883b3",
      "0x7426274e92478c7ba306a48b46a6fbefce6c7099",
      "0xac63436b092b944cadea9243f9aff315421d4fee",
      "0x31dadf3238518c299a4b87fc47f2714d0f922577",
      "0xc2966fea60df1641ed2f9a09a892a9af751a3928",
      "0xb7379da1e9aa5943fbdb7b8163e7f8bf36a3f8bc",
      "0x96aa22baedc5a605357e0b9ae20ab6b10a472e03",
      "0xd300c31a23c2300c601a44da8e3df36c4e1b78c5",
      "0x893e963fc72609c585f09e8cb791ce52c254d7e8",
      "0xf64e49c1d1d2b1cfa570b1da6481dc8dc95cd093",
      "0x8bf1ace6c89e5f207ae372ba2d2bce7467891cf0",
      "0xaed84cf0f0c27a261244960ba6b983305fee840a",
      "0xb77c2290c5e5acd8ca4778876b3caae593741bab",
      "0xd1d5a4c0ea98971894772dcd6d2f1dc71083c44e",
      "0x4a6670b0afb21b2770541c4c9bd678323f7d84c4",
      "0x9349dc9fc1d692f95ee0f43d8135ad18cf59d6db",
      "0x8e9b6a8849d1e4e0080f04b8e6598fa4269635ae",
      "0x92ab871abb9d567aa276b2ce58d0203d84e0181e",
      "0xcd83055557536eff25fd0eafbc56e74a1b4260b3",
      "0xbb49c74e64b8a49525c52474169851c7e6c3f1f4",
      "0xba12222222228d8ba445958a75a0704d566bf2c8"
    ],
    "movements": {
      "0x49d72b6fada7865ce5ed8c5137e73ff5fc18279e": {
        "in_tokens": [
          "0x28c6cE090BF0D534815C59440a197e92B4Cf718f"
        ],
        "out_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0xa7888f85bd76deef3bd03d4dbcf57765a49883b3": {
        "in_tokens": [
          "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        ],
        "out_tokens": [
          "0xA13a9247ea42D743238089903570127DdA72fE44"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0x7426274e92478c7ba306a48b46a6fbefce6c7099": {
        "in_tokens": [
          "0xC7a2572fA8FDB0f7E81d6D3c4e3CCF78FB0DC374"
        ],
        "out_tokens": [
          "0x511686014F39F487E5CDd5C37B4b37606B795ae3"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0xac63436b092b944cadea9243f9aff315421d4fee": {
        "in_tokens": [
          "0x511686014F39F487E5CDd5C37B4b37606B795ae3"
        ],
        "out_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0x31dadf3238518c299a4b87fc47f2714d0f922577": {
        "in_tokens": [
          "0x590f00eDc668D5af987c6076c7302C42B6FE9DD3"
        ],
        "out_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "in_count": 2,
        "out_count": 2
      },
      "0xc2966fea60df1641ed2f9a09a892a9af751a3928": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0x14e7E4F8dbfD8AA9856e45352Aa872ea42929B29"
        ],
        "in_count": 1,
        "out_count": 2
      },
      "0xb7379da1e9aa5943fbdb7b8163e7f8bf36a3f8bc": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0x7b205727EA104d3807EF5FD89Be02D47dcb67a90"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0x96aa22baedc5a605357e0b9ae20ab6b10a472e03": {
        "in_tokens": [
          "0x7f792db54B0e580Cdc755178443f0430Cf799aCa"
        ],
        "out_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0xd300c31a23c2300c601a44da8e3df36c4e1b78c5": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0xdA86006036540822e0cd2861dBd2fD7FF9CAA0e8"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0x893e963fc72609c585f09e8cb791ce52c254d7e8": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0x1936C91190E901B7dD55229A574AE22B58Ff498a"
        ],
        "in_count": 1,
        "out_count": 2
      },
      "0xf64e49c1d1d2b1cfa570b1da6481dc8dc95cd093": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0x0E9cc0F7E550BD43BD2af2214563C47699F96479"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0x8bf1ace6c89e5f207ae372ba2d2bce7467891cf0": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0x3352eB2fEbcA75E40aB8B028701473E2D7DE680A"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0xaed84cf0f0c27a261244960ba6b983305fee840a": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0xdc8Ae66764cd61dD90A0cd9B54C478ee5Dfd83E2"
        ],
        "in_count": 1,
        "out_count": 2
      },
      "0xb77c2290c5e5acd8ca4778876b3caae593741bab": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0x86Eab36585EDDb7a949a0B4771BA733D942A8AA7"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0xd1d5a4c0ea98971894772dcd6d2f1dc71083c44e": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0x6DEA81C8171D0bA574754EF6F8b412F2Ed88c54D"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0x4a6670b0afb21b2770541c4c9bd678323f7d84c4": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0xC7a2572fA8FDB0f7E81d6D3c4e3CCF78FB0DC374"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0x9349dc9fc1d692f95ee0f43d8135ad18cf59d6db": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0xF699D4f4F9e3009b2307D887Ba84C54aFfDf6a55"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0x8e9b6a8849d1e4e0080f04b8e6598fa4269635ae": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0xeb81CEfa37e8B666aAD56313DFacEB1a5792196a"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0x92ab871abb9d567aa276b2ce58d0203d84e0181e": {
        "in_tokens": [
          "0x5283D291DBCF85356A21bA090E6db59121208b44"
        ],
        "out_tokens": [
          "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0xcd83055557536eff25fd0eafbc56e74a1b4260b3": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0xbC396689893D065F41bc2C6EcbeE5e0085233447"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0xbb49c74e64b8a49525c52474169851c7e6c3f1f4": {
        "in_tokens": [
          "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        ],
        "out_tokens": [
          "0x919992A9614b6C3BecBe011f9dBADC3b789Ca79f"
        ],
        "in_count": 1,
        "out_count": 1
      },
      "0xba12222222228d8ba445958a75a0704d566bf2c8": {
        "in_tokens": [
          "0xA13a9247ea42D743238089903570127DdA72fE44"
        ],
        "out_tokens": [
          "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        ],
        "in_count": 1,
        "out_count": 1
      }
    }
  },
......
}
```

这段数据可以帮助判断交易中的哪些地址是“可能的去中心化交易所（DEX）”或交换节点，从而用于构建套利路径图（Directed Token Graph）

函数 `get_potential_exchanges()` 所生成的地址-Token 流动结构，不仅标注了每个地址在单笔交易中的 Token 输入与输出方向，还统计了对应的 Token 类型及数量。相较于仅依据地址交互频率的启发式方法，该实现结合了 Token 层级的约束逻辑（如 in/out Token 的唯一性与差异性），有效提升了 DEX 识别的准确性与判别能力。同时，该结构为后续构建 Token 转换图（Directed Token Graph）和识别套利闭环提供了关键的结构化输入。

#### 函数 `get_arbitrage_from_receipt_if_exists()`：构建路径图并识别套利环

##### 功能描述

该函数是 Goldphish 系统中用于识别套利路径的核心组件之一。其基本思路为：

利用 `get_addr_to_movements()` 与 `get_potential_exchanges()` 提取交易中的资金流动信息与潜在 DEX。

```python
addr_to_movements = get_addr_to_movements(txns)
potential_exchanges = get_potential_exchanges(full_txn, addr_to_movements)
```

构建 Token → Token 的有向图（Directed Graph），图中的每条边代表一个 DEX 完成的一次 Token 交换。

```python
g = nx.DiGraph()
for addr in potential_exchanges:
    # 分析每个地址的代币转入转出
    ins = addr_to_movements[addr]['in']
    outs = addr_to_movements[addr]['out']
    # 添加边和交换信息
    g.add_edge(coin_in, coin_out, exchange=ArbitrageCycleExchange(...))
```

使用 NetworkX 的 `simple_cycles()` 函数枚举可能存在的 Token 闭环路径（Cycle）。

判断路径是否构成套利环，并计算获利地址、Token 和数额。

```python
def is_arb_cycle(cycle):
    sold_tokens = set()
    bought_tokens = set()
    for u, v in zip(cycle, cycle[1:] + [cycle[0]]):
        exchange = g.get_edge_data(u, v)['exchange']
        sold_tokens.add(exchange.token_in)
        bought_tokens.add(exchange.token_out)
    return sold_tokens == bought_tokens
```

复用：

[eth-arbitrage-analyzer/src/test_arbitrage.py at main · sw-Hua/eth-arbitrage-analyzer · GitHub](https://github.com/sw-Hua/eth-arbitrage-analyzer/blob/main/src/test_arbitrage.py)

输入参数：

```python
full_txn: web3.types.TxReceipt  # 交易收据
txns: List[ERC20Transaction]    # ERC-20转账记录列表 就是block_17518743_transfers.json
least_profitable: bool = False  # 是否寻找最小利润路径
```

输出：

```python
Arbitrage(
    txn_hash: bytes,      # 交易哈希
    block_number: int,    # 区块号
    gas_used: int,        # gas消耗
    gas_price: int,       # gas价格
    shooter: str,         # 交易发起地址
    n_cycles: int,        # 识别到的环路数量
    only_cycle: Optional[ArbitrageCycle]  # 详细的环路信息
)
```

从block_17518743_arbitrages.json中可以看到识别结果:

[eth-arbitrage-analyzer/potential_exchanges.json at main · sw-Hua/eth-arbitrage-analyzer · GitHub](https://github.com/sw-Hua/eth-arbitrage-analyzer/blob/main/potential_exchanges.json)

```json
[
  {
    "txn_hash": "0x804eacc90d5a3c165b96344ddad0149e32df095d37597b17f2d678f44394e010",
    "block_number": 17518743,
    "gas_used": 150084,
    "gas_price": 15352546136,
    "shooter": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
    "n_cycles": 2
  },
  {
    "txn_hash": "0x3566164077f3e6d4fb27077c8cc156d5a2f788e51f1ba26a346f6550335efc56",
    "block_number": 17518743,
    "gas_used": 349189,
    "gas_price": 15252546136,
    "shooter": "0x6b75d8AF000000e20B7a7DDf000Ba900b4009A80",
    "n_cycles": 2
  },
  {
    "txn_hash": "0x2985cab40e976e1b41d2862b5d387be1592e95857ac2922fe07fdb58875fb8a4",
    "block_number": 17518743,
    "gas_used": 147249,
    "gas_price": 16140109216,
    "shooter": "0x1111111254EEB25477B68fb85Ed929f73A960582",
    "n_cycles": 2
  },
......
  {
    "txn_hash": "0xa8abab499042066ae741558a2643571e59da5bafc6aa5148d92741fb96c936b5",
    "block_number": 17518743,
    "gas_used": 21000,
    "gas_price": 15292644628,
    "shooter": "0xa442D20f92229D3026921095cd1b29a8d0a5A08d",
    "n_cycles": 2
  }
]
```

这些闭环路径揭示了 Token 在多个地址之间流转形成的闭合结构，其本质对应了潜在的套利可能。但需要强调的是，**闭环的存在仅是套利的** *必要非充分条件*。即使路径闭合，若买入价格与卖出价格之间无正向价差，或 Gas 成本超过价差本身，也无法构成有效套利。

因此，函数 `get_arbitrage_from_receipt_if_exists()` 提供的是**候选套利路径的结构化数据基础**。后续的套利判定还需要:

1. 分析环路中的代币数量变化
  
2. 考虑gas成本
  
3. 验证套利的实际盈利性
  

#### 套利结果分析：区块 #17518743

在完成了 ERC-20 转账解析与 DEX 节点识别之后，本文对区块 #17518743 中的交易执行了系统化的套利路径检测与分析。该流程以 `get_arbitrage_from_receipt_if_exists()` 为核心算法，结合自定义增强版的 `EnhancedArbitrageAnalysis` 类，实现了交易级别的套利机会识别、可视化与收益归属评估。

##### 复用与重构分析

enhanced_arbitrage_analysis.py: [eth-arbitrage-analyzer/src/enhanced_arbitrage_analysis.py at main · sw-Hua/eth-arbitrage-analyzer · GitHub](https://github.com/sw-Hua/eth-arbitrage-analyzer/blob/main/src/enhanced_arbitrage_analysis.py)

在`enhanced_arbitrage_analysis.py`中,我们复用了analyses.py的以下核心函数：

- `get_addr_to_movements`: 构建地址到资金流动的映射
  
- `get_potential_exchanges`: 识别潜在的交易所地址
  
- `get_arbitrage_from_receipt_if_exists`: 基础的环路检测逻辑
  

我们也重构了代币识别增强， DEX 路由识别和路径分析增强（核心重构模块）还有可视化模块构建（matplotlib）。

- **代币识别增强**：定义 `COMMON_TOKENS`，自动将主流代币（如 `WETH`、`USDC`、`LOYAL`）进行人类可读的符号识别，提升输出解释性。
  
- **DEX 路由识别**：加入 `KNOWN_DEX_ROUTERS` 地址集，在路径中标记交易所归属（Uniswap V2/V3、Sushiswap 等），提升节点语义表达能力。
  
- **路径分析增强**：引入 `EnhancedArbitrageAnalysis` 类，对每笔交易进行封装分析，记录套利路径、长度、利润分布、收益代币及套利账户等。
  

```python
class EnhancedArbitrageAnalysis:
    def __init__(self):
        self.token_graph = nx.DiGraph()
        self.dex_usage = defaultdict(int)
        self.path_lengths = []
        self.token_profits = defaultdict(float)
        self.profit_takers = defaultdict(float)
        self.arbitrage_details = []

    def analyze_transaction(self, receipt: Dict, transfers: List[Dict]) -> Optional[Dict]:
        arb_info = get_arbitrage_from_receipt_if_exists(receipt, transfers)
        if not arb_info:
            return None

        # 图合并
        self.token_graph = nx.compose(self.token_graph, arb_info['token_graph'])

        # 数据更新
        for path in arb_info['arbitrage_paths']:
            self.path_lengths.append(path['length'])
            self.profit_takers[receipt['from']] += path['profit']
            self.arbitrage_details.append(path)
```

得到`enhanced_arbitrage_analysis_results.json`结果：

[eth-arbitrage-analyzer/enhanced_arbitrage_analysis_results.json at main · sw-Hua/eth-arbitrage-analyzer · GitHub](https://github.com/sw-Hua/eth-arbitrage-analyzer/blob/main/enhanced_arbitrage_analysis_results.json)

```json
{
  "arbitrage_count": 8,
  "miner_revenue": 24431865433053519,
  "dex_usage": {},
  "path_length_stats": {
    "mean": 3.0,
    "max": 3,
    "min": 3
  },
  "token_profits": {
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": 4.363668294601897e+21,
    "0x5283D291DBCF85356A21bA090E6db59121208b44": -3.8991727321816477e+21,
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2": 8.781203599323606e+24,
    "0xC7a2572fA8FDB0f7E81d6D3c4e3CCF78FB0DC374": -4.7028203412357866e+24,
    "0x511686014F39F487E5CDd5C37B4b37606B795ae3": -4.07300173079209e+24,
    "0xbC396689893D065F41bc2C6EcbeE5e0085233447": -3.2318170301610024e+21,
    "0x6DEA81C8171D0bA574754EF6F8b412F2Ed88c54D": -2.1497102655684338e+21,
    "0x1936C91190E901B7dD55229A574AE22B58Ff498a": 0.0,
    "0x14e7E4F8dbfD8AA9856e45352Aa872ea42929B29": 0.0,
    "0x3352eB2fEbcA75E40aB8B028701473E2D7DE680A": 0.0,
    "0xA13a9247ea42D743238089903570127DdA72fE44": -4.644955624202495e+20
  },
  "profit_takers": {
    "0x76F36d497b51e48A288f03b4C1d7461e92247d5e": 3232594787303424.0
  },
  "detailed_arbitrage_paths": [
    {
      "transaction_hash": "0xb615b9abfb1540fd0c058f61f528bb9d0a786e5c5564b15be7ca474396b48989",
      "block_number": 17518743,
      "profit_taker": "0x76F36d497b51e48A288f03b4C1d7461e92247d5e",
      "path_length": 3,
      "total_profit": 3232594787303424.0,
      "profit_token": "0xC7a2572fA8FDB0f7E81d6D3c4e3CCF78FB0DC374",
      "profit_token_symbol": "FINALE",
      "profit_amount": 8.775822075260472e+24,
      "steps": [
        {
          "from_token": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
          "from_token_symbol": "WETH",
          "to_token": "0xC7a2572fA8FDB0f7E81d6D3c4e3CCF78FB0DC374",
          "to_token_symbol": "FINALE",
          "exchange": "0x4a6670b0afb21b2770541c4c9bd678323f7d84c4",
          "dex_name": "0x4a6670...",
          "in_amount": 8.588683038866112e+16,
          "out_amount": 8.775822161147302e+24,
          "profit": 8.775822075260472e+24
        },
        {
          "from_token": "0xC7a2572fA8FDB0f7E81d6D3c4e3CCF78FB0DC374",
          "from_token_symbol": "FINALE",
          "to_token": "0x511686014F39F487E5CDd5C37B4b37606B795ae3",
          "to_token_symbol": "LOYAL",
          "exchange": "0x7426274e92478c7ba306a48b46a6fbefce6c7099",
          "dex_name": "0x742627...",
          "in_amount": 8.775822161147302e+24,
          "out_amount": 4.0730018199115153e+24,
          "profit": -4.7028203412357866e+24
        },
        {
          "from_token": "0x511686014F39F487E5CDd5C37B4b37606B795ae3",
          "from_token_symbol": "LOYAL",
          "to_token": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
          "to_token_symbol": "WETH",
          "exchange": "0xac63436b092b944cadea9243f9aff315421d4fee",
          "dex_name": "0xac6343...",
          "in_amount": 4.0730018199115153e+24,
          "out_amount": 8.911942539831974e+16,
          "profit": -4.07300173079209e+24
        }
      ]
    }
  ]
}
```

##### 分析结果：区块 #17518743

在区块 `#17518743` 的套利路径分析中，我们得出如下统计结论：

##### 套利规模

- 共识别出 `8` 条有效代币流转环路。
  
- 对应交易中，**套利环路数量（n_cycles） ≥ 2**，存在明显的高频交易特征。
  
- 矿工因相关交易的 gas 费用共计获得约 **24.43 ETH**，显示 MEV 活跃。
  

##### 套利路径特征

所有检测出的套利路径均为**3步结构**，对应经典的三角套利：

```python
Token A → Token B → Token C → Token A
```

以下为一条实际套利路径的结构化表示：

```python
WETH ➝ FINALE ➝ LOYAL ➝ WETH
```

- 初始投入：`8.59e16` WETH
  
- 最终回收：`8.91e16` WETH
  
- 净收益：`+3.23e15` WETH 等值
  
- 中间兑换所涉及 DEX 合约地址及 Token 金额变化已在 `detailed_arbitrage_paths` 中完整记录
  

##### 外部验证：Etherscan & MEV Explore 支持

为验证 `enhanced_arbitrage_analysis.py` 所识别出的套利路径的真实有效性，我们选取了代表性交易 `0xb615b9abfb1540fd0c058f61f528bb9d0a786e5c5564b15be7ca474396b48989`，通过 MEV Explore 平台进行了链上验证，发现该路径完全一致、数据高度吻合。

本次从区块#17518743的套利就是： https://etherscan.io/tx/0xb615b9abfb1540fd0c058f61f528bb9d0a786e5c5564b15be7ca474396b48989

![截屏20250403 下午62341png](file:///var/folders/8f/k9wzkkd53nxdvmc4l45m9nrr0000gn/T/TemporaryItems/NSIRD_screencaptureui_0yDmXI/截屏2025-04-03%20下午6.23.41.png?msec=1743681440801)

###### 套利路径对应

在本地系统识别出的路径为：`WETH ➝ FINALE ➝ LOYAL ➝ WETH`，由三次 ERC-20 token swap 构成，路径长度为 3，对应典型的三角套利模式。其交易地址与合约交互信息可在 Etherscan 的 Logs 与 Internal Txns 部分清晰复现。

###### 金额与方向一致

本地提取的 token 转账金额与 MEV Explore 公布的 swap 信息一致：

| 步骤  | Token | 转出地址/平台 | 转入地址/平台 | 数量 / 金额（估） | 验证平台 |
| --- | --- | --- | --- | --- | --- |
| 1   | WETH | 用户 → Uniswap V2 | FINALE（8.59e16） | $156.08 | MEV Explore |
| 2   | FINALE | Uniswap V2 → V3 | LOYAL（~8.77e24） | $19.88 | MEV Explore |
| 3   | LOYAL | Uniswap V3 → 用户 | WETH（8.91e16） | $161.96 | MEV Explore |

我们系统计算的起始投入为 `8.588e16` WETH，最终回收为 `8.911e16` WETH，对应净收益约 `+3.23e15` WETH，

![截屏20250403 下午62954png](file:///var/folders/8f/k9wzkkd53nxdvmc4l45m9nrr0000gn/T/TemporaryItems/NSIRD_screencaptureui_uamM25/截屏2025-04-03%20下午6.29.54.png?msec=1743681440802)

按当日 ETH 市价折算（**$1,736.88 / ETH**，2023年6月20日），约为：

`(3.23e15 / 1e18) × 1736.88 ≈ $5.61`

该值与 MEV Explore 报告的 **Gross Profit ≈ $5.89** 高度接近，显示本地套利估算误差极小（约 ±5% 以内），可能来源于价格浮动、滑点或 DEX 内部 fee。

与 MEV Explore 公布的 **Gross Profit ≈ $5.89** 的结论高度一致。

##### 利润归属一致

系统识别出的套利发起者为：

`profit_taker = 0x76F36d497b51e48A288f03b4C1d7461e92247d5e`

该地址与 MEV Explore 上记录的交易发起者完全一致。说明：

- 本地分析正确定位了**套利地址**；
  
- 所识别路径为真实链上套利路径；
  
- Goldphish 框架 + 自研增强逻辑可**有效复现 MEV 链上套利现象**。
  

```
      [Uniswap V2]
WETH ─────────────▶ FINALE
                      │
                      │ Uniswap V3 (LOYAL-FINALE Pool)
                      ▼
                   LOYAL
                      │
                      │ Uniswap V3
                      ▼
                    WETH
```

```
          [Uniswap V2]
     ┌────────────────────────┐
     │                        ▼
[WETH]
  │
  │  🔄 输入: 0.08589 ETH
  ▼
[FINALE]
  │
  │  🔄 输出: 8,775,822.1611 FINALE
  │
  │  [Uniswap V3]
  ▼
[LOYAL]
  │
  │  🔄 输出: 4,073,001.8199 LOYAL
  │
  │  [Uniswap V3]
  ▼
[WETH]
  🔄 输出: 0.08912 ETH
```

通过`visualize_arbitrage` 结合论文内容我做了一些可视化

在区块17518743中

![dexusagepng](file:///Users/huasongwen/Desktop/Arbitrage/output/dex_usage.png?msec=1743681475516)

在区块17518743内仅有 1 个套利者，利润为 3.23e15 wei（约 0.0032 ETH）。

![profittakerdistributionpng](file:///Users/huasongwen/Desktop/Arbitrage/output/profit_taker_distribution.png?msec=1743681515908)

![outputpng](file:///Users/huasongwen/Downloads/output.png?msec=1743681841483)

![minervsprofitpng](file:///Users/huasongwen/Desktop/Arbitrage/output/miner_vs_profit.png?msec=1743682610826)

在我们对区块 `#17518743` 中的套利交易分析中，发现了一种高度紧绷的利润分配格局。以某笔三角套利为例：

- 套利者利润约为：`0.00323 ETH`
  
- 矿工收入（Gas Fee）约为：`0.00319 ETH`
  

两者数值接近，几乎呈现 1:1 的比例，这并非偶然。

根据 McLaughlin 等人在《A Large-Scale Study of the Ethereum Arbitrage Ecosystem》中的统计研究发现：

> “In many cases, arbitrage profits are increasingly routed to block producers, especially under the FLASHBOTS system, where only **7.6%** of revenue is retained by arbitrageurs. In contrast, non-FLASHBOTS arbitrages retain **42%** on median.”​

换句话说，套利者在高度竞争的环境中往往通过“渐进式Gas竞价”相互内卷，为了优先入块，不惜将几乎所有收益 回赠给区块生产者（miner/proposer）。

此外，作者进一步指出：

> “Block producers’ share of the revenue from arbitrage is rapidly approaching 100% in recent times.”​

这意味着在 **高频低利套利（HFT-style arbitrage）交易中，套利者更像是在为区块构建者（Builder）或提议者（Proposer）“打工”，而真实的 MEV 利润极可能 **通过 Gas Fee 或 coinbase.transfer() 被 builder 捕获，用作排序权的“隐性贿赂”。

从你本次实证来看，这一趋势已经在链上具体交易中得到体现。

### 结论

本项目基于区块 `#17518743` 的链上交易日志数据，构建了完整的代币资金流图与 DEX 交互路径，并实现了对三角套利路径的自动识别与可视化分析。通过图结构建模、路径闭环检测与利润估算，我们识别出 8 条有效套利路径，其中典型路径如 `WETH ➝ FINALE ➝ LOYAL ➝ WETH`，回到了原始 Token 并实现了净收益。

我们进一步对套利交易的执行者（套利者地址）和矿工收入进行了对比，发现：

- 该套利交易中，套利者利润约为 `0.0032 ETH`，而矿工因 Gas 收费获得 `0.0031 ETH`，两者极为接近，显示出高频低利套利中常见的“利润共享”格局；
  
- 如结合 MEV-Boost 环境，这部分 Gas Fee 很可能已被“定向支付”给出块者，进一步增强了协议层的提取能力。
  

本研究不仅验证了链上套利行为的可识别性与可视化路径追踪的可行性，也为后续 MEV 策略、Flashbots 分析、路径优化等研究提供了坚实基础。

如能继续深入，未来可拓展至更复杂的套利形态（多跳、多 Token、Flashloan 等），并结合链下定价信息，实现全链套利行为的系统性分析与建模。