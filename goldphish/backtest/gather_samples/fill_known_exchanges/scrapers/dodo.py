"""
Scrapes Balancer exchange addresses from the factory contract
"""


import typing
import logging
import psycopg2.extensions
import web3
import web3.logs
import web3.contract
import web3._utils.events
from eth_utils import event_abi_to_log_topic

from utils import get_abi
from .base_log_scraper import BaseLogScraper, ScrapeResult, PrimeResult


l = logging.getLogger(__name__)

class DodoScraper(BaseLogScraper):
    exchange_abi: typing.Dict
    factory: web3.contract.Contract

    def __init__(self) -> None:
        super().__init__()
        self.factory = web3.Web3().eth.contract(
            address = '0x3A97247DF274a17C59A3bd12735ea3FcDFb49950',
            abi = get_abi('dodo/zoo.abi.json'),
        )
        self.pool_added_topic = event_abi_to_log_topic(self.factory.events.DODOBirth().abi)

    def prime(self, curr: psycopg2.extensions.cursor):
        curr.execute(
            """
            CREATE TABLE IF NOT EXISTS dodo_exchanges (
                id SERIAL PRIMARY KEY NOT NULL,
                address BYTEA NOT NULL,
                origin_txn BYTEA NOT NULL,
                origin_block INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dodo_ex_addr ON dodo_exchanges USING hash (address);
            """
        )

        return PrimeResult(['0x3A97247DF274a17C59A3bd12735ea3FcDFb49950'])


    def scrape(
                self,
                curr: psycopg2.extensions.cursor,
                w3: web3.Web3,
                logs: typing.List[typing.Dict]
            ) -> ScrapeResult:
        relevant_logs = []
        for log in logs:
            # filter out irrelevant logs
            if log['address'] == self.factory.address and len(log['topics']) > 0 and log['topics'][0] == self.pool_added_topic:
                relevant_logs.append(log)

        l.debug(f'Have {len(relevant_logs)} relevant logs')

        for log in relevant_logs:
            self.process_factory_event(
                w3,
                curr,
                log
            )
        return ScrapeResult(set())

    def process_factory_event(
                self,
                w3: web3.Web3,
                curr: psycopg2.extensions.cursor,
                log
            ) -> typing.Optional[str]:
        receipt = self.factory.events.DODOBirth().processLog(log)

        # if we already know about this exchange then skip
        curr.execute(
            "SELECT id FROM dodo_exchanges WHERE address = %s",
            (bytes.fromhex(receipt['args']['newBorn'][2:]),)
        )
        if curr.rowcount > 0:
            id_ = curr.fetchone()[0]
            l.debug(f'Already know about this exchange, id={id_}')
            return

        # record the exchange info
        curr.execute(
            """
            INSERT INTO dodo_exchanges (
                address, origin_txn, origin_block
            )
            VALUES (%s, %s, %s)
            RETURNING (id)
            """,
            (
                bytes.fromhex(receipt['args']['newBorn'][2:]),
                receipt['transactionHash'],
                receipt['blockNumber'],
            )
        )
        l.info(f'Registered dodo_exchanges exchange id={curr.fetchone()[0]}')
        return receipt['args']['newBorn']
