import collections
import os
import subprocess
import time
import typing
import psycopg2.extensions
import tempfile
import web3
import pika
import web3.types
import logging
from eth_utils import event_abi_to_log_topic
from eth_account import Account
from eth_account.signers.local import LocalAccount

l = logging.getLogger(__name__)

from utils import erc20

ERC20_TRANSFER_TOPIC = event_abi_to_log_topic(erc20.events.Transfer().abi)
ERC20_TRANSFER_TOPIC_HEX = '0x' + ERC20_TRANSFER_TOPIC.hex()

def parse_logs_for_net_profit(logs: typing.List[web3.types.LogReceipt]) -> typing.Dict[str, typing.Dict[str, int]]:
    """
    Parse ERC20 Transfer events into net flows.
    Maps (ERC20 address, owner) to net flow (as int)
    """
    ret = collections.defaultdict(lambda: collections.defaultdict(lambda: 0))
    for log in logs:
        if len(log['topics']) > 0 and log['topics'][0] == ERC20_TRANSFER_TOPIC:
            xfer = erc20.events.Transfer().processLog(log)
            ret[log['address']][xfer['args']['from']] -= xfer['args']['value']
            ret[log['address']][xfer['args']['to']] += xfer['args']['value']
    return {k: dict(v) for k, v in ret.items()}


def funded_deployer() -> LocalAccount:
    ret: LocalAccount = Account.from_key(bytes.fromhex('0xab1179084d3336336d60b2ed654d99a21c2644cadd89fd3034ee592e931e4a77'[2:]))
    return ret


def connect_rabbit() -> pika.BlockingConnection:
    rabbit_host = os.getenv('RABBITMQ_HOST', 'ethereum-measurement-rabbitmq')
    rabbit_port = int(os.getenv('RABBITMQ_PORT', '5672'))

    cxn = pika.BlockingConnection(pika.ConnectionParameters(
        host=rabbit_host,
        port=rabbit_port,
        credentials=pika.PlainCredentials(
            username='guest',
            password='guest',
        )
    ))

    assert cxn.is_open

    l.debug('connected to rabbitmq')

    return cxn

def connect_db() -> psycopg2.extensions.connection:
    pg_host = os.getenv('PSQL_HOST', 'ethereum-measurement-pg')
    pg_port = int(os.getenv('PSQL_PORT', '5432'))
    pg_user = os.getenv('PSQL_USER', 'measure')
    pg_pass = os.getenv('PSQL_PASS', 'password')
    pg_db   = os.getenv('PSQL_DB', 'eth_measure_db')
    db = psycopg2.connect(
        host = pg_host,
        port = pg_port,
        user = pg_user,
        password = pg_pass,
        database = pg_db,
    )
    db.autocommit = False
    l.debug(f'connected to postgresql')
    return db


def mine_block(w3: web3.Web3):
    block = w3.eth.get_block('latest')
    block_num_before = block['number']

    resp = w3.provider.make_request('evm_mine', [block['timestamp'] + 12])

    bn_result = w3.provider.make_request('eth_blockNumber', [])
    block_num_after = int(bn_result['result'][2:], base=16)

    assert block_num_before + 1 == block_num_after, f'expected {block_num_before} + 1 == {block_num_after}'


_GANACHE_DIR = os.path.abspath(os.path.dirname(__file__) + '/../vend/ganache/dist')

_ganache_port = (os.getpid() % (65535 - 1200)) + 1200
class GanacheContextManager:

    def __init__(self, w3: web3.Web3, target_block: int, unlock: typing.Optional[typing.List[str]] = None) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        old_block = w3.eth.get_block(target_block)
        old_ts = old_block['timestamp']

        assert isinstance(target_block, int)
        assert target_block > 0
        l.debug(f'Forking at block {target_block:,}')
        
        extra_args = []
        if unlock is not None:
            extra_args = ['--wallet.unlockedAccounts', ','.join(unlock)]

        fork_url = os.getenv('WEB3_HOST', 'ws://172.17.0.1:8546')

        self.p = subprocess.Popen(
            [
                'node',
                'cli.js',
                '--database.dbPath', self.tmpdir.name,
                '--fork.url', fork_url,
                '--server.ws',
                '--server.port', str(_ganache_port),
                '--fork.blockNumber', str(target_block),
                '--wallet.accounts', f'{funded_deployer().key.hex()},{web3.Web3.toWei(100, "ether")}',
                '--chain.chainId', '1',
                '--chain.time', str(old_ts * 1_000), # unit conversion needed for some reason -- blame javascript
                '--miner.coinbase', web3.Web3.toChecksumAddress(b'\xa0' * 20) + ' ',
                '--miner.blockTime', '100',
                '--miner.blockGasLimit', str(60_000_000),
                *extra_args
            ],
            cwd = _GANACHE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        provider = web3.WebsocketProvider(
            f'ws://127.0.0.1:{_ganache_port}',
            websocket_timeout=60 * 5,
            websocket_kwargs={
                'max_size': 10 * 1024 * 1024 * 1024, # 10 Gb max payload
                'ping_timeout': 60 * 5,
            },
        )

        w3 = web3.Web3(
            provider
        )

        while not w3.isConnected():
            time.sleep(0.1)

        assert w3.isConnected()
        tip = w3.eth.get_block('latest')
        l.debug(f'tip after fork {tip["number"]:,}')

        w3.provider.make_request('miner_stop', [])

        old_str = w3.provider.__str__
        def new_str(*args, **kwargs):
            s = old_str(*args, **kwargs)
            print('GANACHE ' + s)
        w3.provider.__str__ = new_str

        # patch wait to make a mine block request
        old_wait = w3.eth.wait_for_transaction_receipt
        def new_wait(*args, **kwargs):
            mine_block(w3)
            return old_wait(*args, **kwargs)
        w3.eth.wait_for_transaction_receipt = new_wait

        self.w3 = w3

    def __enter__(self) -> web3.Web3:
        return self.w3
    
    def __exit__(self, exc_type, exc_value, exc_traceback):
        l.debug(f'Killing ganache')
        # force kill node, sorry
        self.p.kill()
        self.p.wait()
        self.tmpdir.cleanup()
        l.debug(f'Ganache killed')


class CancellationToken:
    """
    Utility class for reporting cancellations.
    """
    HEARTBEAT_PERIOD_SECONDS = 60

    def __init__(self, jobname: str, workername: str, conn: psycopg2.extensions.connection) -> None:
        self.jobname = jobname
        self.workername = workername
        self.last_heartbeat = 0
        self.cancel_requested_cache = None
        self.conn = conn
        self.curr = conn.cursor()

        self.curr.execute(
            '''
            CREATE TABLE IF NOT EXISTS job_control (
                id                  SERIAL NOT NULL PRIMARY KEY,
                job_name            TEXT NOT NULL,
                worker_name         TEXT NOT NULL,
                is_cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
                last_heartbeat      TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()::timestamp
            );

            INSERT INTO job_control (job_name, worker_name) VALUES (%s, %s) RETURNING id;
            ''',
            (self.jobname, self.workername)
        )
        self._id = self.curr.fetchone()[0]
        l.info(f'Assigned job_control id={self._id}')
        self.conn.commit()

    def heartbeat(self):
        self.curr.execute(
            '''
            UPDATE job_control SET last_heartbeat = NOW()::timestamp WHERE id = %s
            ''',
            (self._id,)
        )
        self.conn.commit()

    def cancel_requested(self) -> bool:
        if self.cancel_requested_cache is None or self.last_heartbeat + CancellationToken.HEARTBEAT_PERIOD_SECONDS < time.time():
            # query for cancellation
            self.curr.execute(
                '''
                UPDATE job_control SET last_heartbeat = NOW()::timestamp WHERE id = %s RETURNING is_cancel_requested
                ''',
                (self._id,)
            )
            (is_cancel_requested,) = self.curr.fetchone()
            assert isinstance(is_cancel_requested, bool)
            if is_cancel_requested and not self.cancel_requested_cache:
                l.info(f'Received requested cancellation!')
            self.cancel_requested_cache = is_cancel_requested

            self.conn.commit()
            self.last_heartbeat = time.time()

        return self.cancel_requested_cache
