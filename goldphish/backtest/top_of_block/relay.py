import asyncio
import collections
import datetime
import decimal
import itertools
import json
import math
import os
import pathlib
import backoff
import subprocess
import sys
import tempfile
import time
import numpy as np
import web3
import web3.types
import web3.contract
import web3._utils.filters
import typing
import logging
import argparse
import networkx as nx
import psycopg2
import psycopg2.extensions
import psycopg2.extras
from backtest.top_of_block.constants import MIN_PROFIT_PREFILTER
from find_circuit.find import DEFAULT_FEE_TRANSFER_CALCULATOR, BuiltinFeeTransferCalculator, FeeTransferCalculator, FoundArbitrage, PricingCircuit, detect_arbitrages_bisection
import pricers

from backtest.utils import connect_db, erc20
from eth_account import Account
from eth_account.signers.local import LocalAccount
from pricers.balancer import BalancerPricer, TokenNotAvailable
from pricers.balancer_v2.liquidity_bootstrapping_pool import BalancerV2LiquidityBootstrappingPoolPricer
from pricers.balancer_v2.weighted_pool import BalancerV2WeightedPoolPricer
from pricers.base import BaseExchangePricer, NotEnoughLiquidityException
from pricers.uniswap_v2 import UniswapV2Pricer
from pricers.uniswap_v3 import UniswapV3Pricer
from shooter.composer import construct_arbitrage
from shooter.encoder import serialize

from utils import BALANCER_VAULT_ADDRESS, DAI_ADDRESS, TETHER_ADDRESS, USDC_ADDRESS, WBTC_ADDRESS, WETH_ADDRESS, connect_web3, decode_trace_calls, get_abi, get_block_timestamp, parse_ganache_call_trace, pretty_print_trace
from utils.profiling import inc_measurement, maybe_log, profile


l = logging.getLogger(__name__)

DEBUG = False
DEBUG_RESERVATION = 128839
DEBUG_CANDIDATE = 1303660660

RELAYER_BALANCE_WEI = 10_000 * (10 ** 18)

SHOOTER_ARTIFACT_PATH = pathlib.Path(__file__).parent.parent.parent / 'artifacts' / 'contracts' / 'shooter.sol' / 'Shooter.json'

BLOCKS_PER_DAY = 6_646

if os.path.isfile(SHOOTER_ARTIFACT_PATH):
    with open(SHOOTER_ARTIFACT_PATH) as fin:
        SHOOTER_ARTIFACT = json.load(fin)

    generic_shooter: web3.contract.Contract = web3.Web3().eth.contract(address=web3.Web3.toChecksumAddress(b'\x00'*20), abi=SHOOTER_ARTIFACT['abi'])

    DO_APPROVE_SELECTOR = bytes.fromhex(
        generic_shooter.functions.doApprove(
            web3.Web3.toChecksumAddress(b'\x00'*20),
            web3.Web3.toChecksumAddress(b'\x00'*20),
        ).selector[2:]
    )

else:
    print('WARNING cannot load shooter here')
    SHOOTER_ARTIFACT = None

BANNED_TOKENS = frozenset((
    '0xD46bA6D942050d489DBd938a2C909A5d5039A161', # Ampleforth -- rebasing token, fucks up with DEX
))

KNOWN_TOKENS = frozenset((
    WETH_ADDRESS,
    TETHER_ADDRESS,
    USDC_ADDRESS,
    WBTC_ADDRESS,
    DAI_ADDRESS,
))

N_PORTS_PER_SLICE = 20

def add_args(subparser: argparse._SubParsersAction) -> typing.Tuple[str, typing.Callable[[web3.Web3, argparse.Namespace], None]]:
    parser_name = 'do-relay'
    parser: argparse.ArgumentParser = subparser.add_parser(parser_name)

    parser.add_argument('--setup-db', action='store_true', help='Setup the database (before run)')
    parser.add_argument('--reset-db', action='store_true', help='Reset the database (before run)')
    parser.add_argument('--fixup-db', action='store_true', help='Reset the database (before run)')

    parser.add_argument('--id', type=int, default=0)
    parser.add_argument('--n-workers', type=int, default=0)

    parser.add_argument('--top-arbs', action='store_true', help='Do the run for top arbitrages')

    return parser_name, relay


def relay(w3: web3.Web3, args: argparse.Namespace):
    db = connect_db()
    curr = db.cursor()

    if args.setup_db:
        setup_db(curr)
        fill_relay_queue(curr)
        fill_relay_top_arbs_queue(curr)
        # input('commit?')
        db.commit()
        return

    if args.reset_db:
        reset_db(curr)
        db.commit()
        return
    
    if args.fixup_db:
        if args.top_arbs:
            dedupe_top_arbs(curr)
            # splitup_work_top_arbs(curr)
        else:
            fixup_db(curr)
        db.commit()
        return

    if args.top_arbs:
        relay_top_arbs(w3, curr, args.id)
        return

    if args.worker_name is None:
        print('Must supply worker_name', file=sys.stderr)
        exit(1)

    l.info('Starting arbitrage relaying')

    time.sleep(4)

    fee_calculator = InferredTokenTransferFeeCalculator()

    def reconnect_db(_):
        nonlocal db
        nonlocal curr
        nonlocal w3
        db = connect_db()
        curr = db.cursor()
        w3 = connect_web3()

    @backoff.on_exception(
        backoff.expo,
        (psycopg2.OperationalError, OSError, asyncio.exceptions.TimeoutError),
        max_time = 10 * 60,
        factor = 4,
        on_backoff = reconnect_db,
    )
    def wrapped_do_process_reservation(w3, block_number, fee_calculator, tmpdir, id_):
        process_reservation(w3, curr, block_number, fee_calculator, tmpdir, id_)

    while True:
        maybe_rez = get_reservation(curr, args.worker_name)

        if maybe_rez is None:
            break
        
        reservation_id, block_number = maybe_rez
        try:
            with tempfile.TemporaryDirectory(dir='/mnt/goldphish/tmp') as tmpdir:
                wrapped_do_process_reservation(w3, block_number, fee_calculator, tmpdir, args.id)
        except:
            l.critical(f'Reservation id={reservation_id} failed')
            raise

        curr.execute(
            '''
            UPDATE candidate_arbitrage_reshoot_blocks
            SET completed_on = now()::timestamp
            WHERE id = %s
            ''',
            (reservation_id,)
        )
        assert curr.rowcount == 1

        if not DEBUG:
            curr.connection.commit()
        else:
            l.info('Quitting because DEBUG = True')
            return


def process_reservation(
        w3: web3.Web3,
        curr: psycopg2.extensions.cursor,
        block_number: int,
        fee_calculator: 'InferredTokenTransferFeeCalculator',
        tmpdir: str,
        worker_id: int,
    ):
    fee_calculator.sync(curr, block_number)

    timestamp_to_use = w3.eth.get_block(block_number + 1)['timestamp']

    candidates = get_candidates_in_block(curr, block_number)

    l.debug(f'Have {len(candidates):,} arbitrages to test')

    if len(candidates) == 0:
        return

    # for keeping progress
    t_start = time.time()
    percent_marks = [len(candidates) * x // 100 for x in range(20, 100, 20)]

    # some stats
    n_banned_skipped = 0
    n_no_arb_on_fee = 0

    # cache of pricers at current block
    pricer_cache: typing.Dict[str, BaseExchangePricer] = {}

    new_banned_tokens    = set()
    new_banned_exchanges = set()
    interfering_tokens: typing.Dict[str, typing.Set[str]] = {}
    incompatible_tokens: typing.Dict[str, typing.Set[str]] = {}

    banned_tokens     = set(BANNED_TOKENS)
    banned_exchanges  = set()

    results_success: typing.Dict[int, AutoAdaptShootSuccess] = {}
    results_failure: typing.Dict[int, str]                   = {}

    proc, w3_ganache, acct, shooter_address = open_ganache(block_number, tmpdir, worker_id)

    try:

        for i, candidate in enumerate(candidates):
            if DEBUG:
                maybe_log()

            l.debug(f'relaying id={candidate.id_}')

            # report progress
            if i > 0 and i in percent_marks:
                elapsed = time.time() - t_start
                nps = i / elapsed
                remain = len(candidates) - i
                eta_seconds = remain / nps
                eta = datetime.timedelta(seconds=eta_seconds)

                l.debug(f'Working, {i:,}/{len(candidates):,} ({round(i / len(candidates) * 100)}%) complete, ETA {eta}')

            # find if we should skip bc of a banned exchange
            these_banned_exchanges = set(candidate.exchanges).intersection(banned_exchanges)
            if len(these_banned_exchanges) > 0:
                # this is a banned arbitrage, ignore it
                n_banned_skipped += 1
                sz_banned_exchanges = ','.join(sorted(these_banned_exchanges))
                results_failure[candidate.id_] = f'Broken exchange/s: {sz_banned_exchanges}'
                l.debug(f'Skipping arbitrage id={candidate.id_} because it uses banned tokens {sz_banned_exchanges}')
                continue

            # find if we should skip bc of a banned token
            all_tokens = set(x for x, _ in candidate.directions)

            these_banned_tokens = all_tokens.intersection(banned_tokens)
            if len(these_banned_tokens) > 0:
                # this is a banned arbitrage, ignore it
                n_banned_skipped += 1
                sz_banned_tokens = ','.join(sorted(these_banned_tokens))
                results_failure[candidate.id_] = f'Broken token/s: {sz_banned_tokens}'
                l.debug(f'Skipping arbitrage id={candidate.id_} because it uses banned tokens {sz_banned_tokens}')
                continue

            # find if we should skip bc of token interference
            does_interfere = False
            for t in all_tokens:
                maybe_interferers = interfering_tokens.get(t, None)
                if maybe_interferers is not None:
                    interferers = maybe_interferers.intersection(candidate.exchanges)
                    if len(interferers) > 0:
                        sz_interferers = ','.join(sorted(interferers))
                        results_failure[candidate.id_] = f'Token {t} interferes with {sz_interferers}'
                        l.debug(f'Skipping arbitrage id={candidate.id_} because {t} interferes with {sz_interferers}')
                        does_interfere = True
                        break

            for exchange, (t1, t2) in zip(candidate.exchanges, candidate.directions):
                if exchange in incompatible_tokens and len(incompatible_tokens[exchange].intersection([t1, t2])) > 0:
                    l.debug(f'Skipping arbitrage id={candidate.id_} because {exchange} has an incompatible token in the circuit')

            if does_interfere:
                n_banned_skipped += 1
                continue

            # get pricers
            circuit = []
            for x in candidate.exchanges:
                if x in pricer_cache:
                    circuit.append(pricer_cache[x])
                else:
                    pricer = load_pricer_for(w3_ganache, curr, x)
                    assert pricer is not None
                    pricer_cache[x] = pricer
                    circuit.append(pricer)

            fa = FoundArbitrage(
                circuit = circuit,
                directions = candidate.directions,
                pivot_token = WETH_ADDRESS,
                amount_in = candidate.amount_in,
                profit = candidate.profit_before_fee,
            )

            if False and DEBUG:
                for p in circuit:
                    l.debug(str(p))
                pc = PricingCircuit(
                    [load_pricer_for(w3, curr, x) for x in candidate.exchanges],
                    candidate.directions
                )
                # pc.sample(fa.amount_in, block_number, timestamp_to_use, debug = True, fee_transfer_calculator=fee_calculator)
                maybe_fa = detect_arbitrages_bisection(
                    pc,
                    block_number,
                    timestamp = timestamp_to_use,
                    try_all_directions = False,
                    fee_transfer_calculator = fee_calculator
                )

            fee_calculator.infer_relays_and_aliases(fa, shooter_address)
            
            has_fees = any(fee_calculator.has_fee(t) for t in all_tokens)

            if False and DEBUG:
                if len(maybe_fa) == 0:
                    # no arbitrages found, is it because we had fees?
                    if has_fees:
                        l.debug('No arbitrage after applying inferred fees')
                    else:
                        l.critical(f'Could not replicate arbitrage!!!!!')
                        l.critical(f'id = {candidate.id_}')
                        l.critical(f'block_number = {block_number}')
                        l.critical(f'found_arbitrage = {fa}')
                        raise Exception('could not replicate!!')

                (rep_fa,) = maybe_fa

                if not has_fees:
                    if rep_fa.profit != candidate.profit_before_fee:
                        l.critical(f'profit changed!!! old: {candidate.profit_before_fee / 10 ** 18:.5f} vs new {rep_fa.profit / 10 ** 18:.5f} ETH')
                    else:
                        l.debug(f'replicated arbitrage')
                    
                    if rep_fa.amount_in != fa.amount_in:
                        l.critical('amount_in changed')
                        l.critical(f'old {fa.amount_in}')
                        l.critical(f'new {rep_fa.amount_in}')


            try:
                result = auto_adapt_attempt_shoot_candidate(
                    w3_ganache,
                    acct,
                    shooter_address,
                    fa,
                    fee_calculator,
                    timestamp=timestamp_to_use,
                    must_recompute = has_fees,
                )
            except NotEnoughLiquidityException:
                l.critical(f'Problem with relaying ... trying with recompute on')
                result = auto_adapt_attempt_shoot_candidate(
                    w3_ganache,
                    acct,
                    shooter_address,
                    fa,
                    fee_calculator,
                    timestamp=timestamp_to_use,
                    must_recompute = True,
                )
            if isinstance(result, DiagnosisBrokenToken):
                assert result.token_address not in KNOWN_TOKENS, f'Token {result.token_address} should not be banned ever'
                banned_tokens.add(result.token_address)
                new_banned_tokens.add(result.token_address)
                n_banned_skipped += 1
                results_failure[candidate.id_] = f'Broken token: {result.token_address}'
            elif isinstance(result, DiagnosisBadExchange):
                banned_exchanges.add(result.exchange)
                new_banned_exchanges.add(result.exchange)
                n_banned_skipped += 1
                results_failure[candidate.id_] = f'Bad exchange: {result.exchange}'
            elif isinstance(result, DiagnosisOther):
                results_failure[candidate.id_] = f'Other {result.reason}'
            elif isinstance(result, DiagnosisNoArbitrageOnFeeApplied):
                n_no_arb_on_fee += 1
                results_failure[candidate.id_] = f'No arbitrage after fee'
            elif isinstance(result, DiagnosisExchangeInterference):
                if result.token_address in interfering_tokens:
                    interfering_tokens[result.token_address].add(result.exchange_address)
                else:
                    interfering_tokens[result.token_address] = set((result.exchange_address,))
                results_failure[candidate.id_] = f'token-exchange interference'
            elif isinstance(result, DiagnosisIncompatibleToken):
                if result.exchange_address in incompatible_tokens:
                    incompatible_tokens[result.exchange_address].add(result.token_address)
                else:
                    interfering_tokens[result.exchange_address] = set((result.token_address,))
                results_failure[candidate.id_] = f'incompatible token {result.token_address} / {result.exchange_address}'
            elif isinstance(result, AutoAdaptShootSuccess):
                results_success[candidate.id_] = result
            else:
                raise Exception(f'Not sure what this is: {result}')

        l.debug(f'Skipped {n_banned_skipped:,} arbitrages due to banned token or exchange use')
        l.debug(f'Had {n_no_arb_on_fee:,} arbitrages that diasappeared on applying fee')
        l.debug(f'Had {len(results_success):,} successful arbitrages in {block_number}')
    finally:
        try:
            proc.kill()
            proc.wait()
        except:
            l.exception('could not kill proc')

    # assert len(results_success) + len(results_failure) == len(candidates)
    psycopg2.extras.execute_values(
        curr,
        '''
        INSERT INTO candidate_arbitrage_relay_results (candidate_arbitrage_id, shoot_success, failure_reason) VALUES %s
        ''',
        ((id_, False, reason) for id_, reason in results_failure.items()),
    )

    # assign IDs to all the tokens fees we just inferred
    inferred_fee_with_ids: typing.Dict[TokenFee, TokenFee] = {}
    for success in results_success.values():
        for i, tf in enumerate(list(success.token_fees_used)):
            # already known
            if tf.id_ is not None:
                continue

            # we just set the id
            if tf in inferred_fee_with_ids:
                success.token_fees_used[i] = inferred_fee_with_ids[tf]

            curr.execute(
                '''
                INSERT INTO inferred_token_fee_on_transfer (token_id, fee, round_down, from_address, to_address, block_number_inferred, updated_on)
                SELECT t.id, %(fee)s, %(round_down)s, %(from_address)s, %(to_address)s, %(block_number_inferred)s, now()::timestamp
                FROM tokens t
                WHERE t.address = %(token_address)s
                RETURNING inferred_token_fee_on_transfer.id
                ''',
                {
                    'fee': tf.fee,
                    'round_down': tf.round_down,
                    'from_address': bytes.fromhex(tf.from_address[2:]),
                    'to_address': bytes.fromhex(tf.to_address[2:]),
                    'block_number_inferred': block_number,
                    'token_address': bytes.fromhex(tf.token[2:]),
                }
            )
            assert curr.rowcount == 1, f'expected rowcount = 1 but got {curr.rowcount} for address = {tf.token}'
            (id_,) = curr.fetchone()
            with_id = tf._replace(id_ = id_)
            inferred_fee_with_ids[tf] = with_id
            success.token_fees_used[i] = with_id

    for id_, success in results_success.items():
        l.debug(f'inserting id_={id_}')
        curr.execute(
            '''
            INSERT INTO candidate_arbitrage_relay_results
            (candidate_arbitrage_id, shoot_success, gas_used, had_fee_on_xfer_token, real_profit_before_fee)
            VALUES (%(id)s, true, %(gas_used)s, %(had_fee_on_transfer)s, %(profit)s)
            ''',
            {
                'id': id_,
                'gas_used': success.gas,
                'had_fee_on_transfer': len(success.token_fees_used) > 0,
                'profit': success.profit_no_fee,
            }
        )
        assert curr.rowcount == 1

        for tf in success.token_fees_used:
            assert tf.id_ is not None
            curr.execute(
                '''
                INSERT INTO candidate_arbitrage_relay_results_used_fees (candidate_arbitrage_id, fee_used)
                VALUES (%s, %s)
                ''',
                (id_, tf.id_)
            )
            assert curr.rowcount == 1

    return

def splitup_work_top_arbs(curr: psycopg2.extensions.cursor):
    resp = input('Split remaining work into smaller chunks? Type yes to continue: ')
    if resp.lower() != 'yes':
        print('bye')
        return

    curr.execute(
        '''
        SELECT id, start_block, end_block
        FROM top_candidate_arbitrage_reservations
        WHERE claimed_on is null
        '''
    )
    l.info(F'Splitting up {curr.rowcount} reservations')
    for id_, start_block, end_block in curr.fetchall():
        curr.execute(
            '''
            DELETE FROM top_candidate_arbitrage_reservations WHERE id = %s
            ''',
            (id_,)
        )
        midpoint = (start_block + end_block) // 2
        curr.execute(
            '''
            INSERT INTO top_candidate_arbitrage_reservations (start_block, end_block)
            VALUES (%s, %s)
            ''',
            (start_block, midpoint)
        )
        curr.execute(
            '''
            INSERT INTO top_candidate_arbitrage_reservations (start_block, end_block)
            VALUES (%s, %s)
            ''',
            (midpoint + 1, end_block)
        )
    input('ENTER to continue')


def fixup_db(curr: psycopg2.extensions.cursor):
    resp = input('Fixup DB????? Type yes to continue: ')
    if resp.lower() != 'yes':
        print('bye')
        return

    # if True:
    #     assert time.time() < 1665213115.1549613 + 60 * 20
    #     curr.execute(
    #         '''
    #         CREATE TEMP TABLE tmp_needed_blocks AS
    #         SELECT DISTINCT block_number
    #         FROM (
    #             SELECT *
    #             FROM carr_dedup cd
    #             WHERE NOT EXISTS(SELECT FROM candidate_arbitrage_relay_results carr WHERE carr.candidate_arbitrage_id = cd.candidate_arbitrage_id)
    #         ) x
    #         JOIN candidate_arbitrages ca ON ca.id = x.candidate_arbitrage_id
    #         '''
    #     )
    #     print(f'Have {curr.rowcount:,} blocks to re-queue')
    #     curr.execute(
    #         '''
    #         UPDATE candidate_arbitrage_reshoot_blocks carb
    #         SET claimed_on = Null, worker = null, completed_on = null
    #         WHERE EXISTS(SELECT FROM tmp_needed_blocks tnb WHERE tnb.block_number = carb.block_number)
    #         '''
    #     )
    #     print(f'Updated {curr.rowcount} reservations')
    #     ans = input('type yes to continue')
    #     if ans.strip().lower() != 'yes':
    #         curr.connection.rollback()
    #         exit()
    #     return

    curr.execute(
        '''
        UPDATE candidate_arbitrage_reshoot_blocks
        SET claimed_on = NULL
        WHERE completed_on IS NULL AND claimed_on IS NOT NULL AND (now()::timestamp - claimed_on) > interval '100 minutes'
        '''
    )
    resp = input(f'Requeued {curr.rowcount} entries, continue? type yes to continue:')
    if resp.lower() != 'yes':
        curr.connection.rollback()
        print('bye')
        return

def reset_db(curr: psycopg2.extensions.cursor):
    resp = input('THIS WILL DELETE DATA. Type yes to continue: ')
    if resp.lower() != 'yes':
        print('bye')
        return

    curr.execute(
        '''
        UPDATE candidate_arbitrage_reshoot_blocks SET claimed_on = null, completed_on = null
        WHERE claimed_on IS NOT NULL OR completed_on IS NOT NULL
        '''
    )
    l.debug(f'reset {curr.rowcount:,} block-reservations')

    curr.execute(
        '''
        TRUNCATE TABLE inferred_token_fee_on_transfer CASCADE;
        '''
    )

    curr.execute(
        '''
        TRUNCATE TABLE candidate_arbitrage_relay_results;
        '''
    )

def setup_db(curr: psycopg2.extensions.cursor):
    curr.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_arbitrage_reshoot_blocks (
            id                 SERIAL PRIMARY KEY NOT NULL,
            block_number       INTEGER NOT NULL,
            worker             TEXT,
            claimed_on         TIMESTAMP WITHOUT TIME ZONE,
            completed_on       TIMESTAMP WITHOUT TIME ZONE,
            priority           INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_candidate_arbitrage_reshoot_blocks_claimed_on ON candidate_arbitrage_reshoot_blocks(claimed_on);
        CREATE INDEX IF NOT EXISTS idx_candidate_arbitrage_reshoot_blocks_sample_group ON candidate_arbitrage_reshoot_blocks(priority);

        CREATE TABLE IF NOT EXISTS inferred_token_fee_on_transfer (
            id SERIAL             PRIMARY KEY NOT NULL,
            token_id              INTEGER NOT NULL REFERENCES tokens (id) ON DELETE CASCADE,
            fee                   NUMERIC(20, 20),
            round_down            BOOLEAN,
            from_address          BYTEA,
            to_address            BYTEA,
            block_number_inferred INTEGER NOT NULL,
            updated_on            TIMESTAMP WITHOUT TIME ZONE
        );

        CREATE INDEX IF NOT EXISTS idx_inferred_token_fee_on_transfer_updated_on ON inferred_token_fee_on_transfer (updated_on);
        CREATE INDEX IF NOT EXISTS idx_inferred_token_fee_on_transfer_token_id ON inferred_token_fee_on_transfer (token_id);

        CREATE TABLE IF NOT EXISTS top_candidate_arbitrage_reservations (
            id           SERIAL PRIMARY KEY NOT NULL,
            start_block  INTEGER NOT NULL,
            end_block    INTEGER NOT NULL,
            worker       TEXT,
            progress     INTEGER,
            claimed_on   TIMESTAMP WITHOUT TIME ZONE,
            heartbeat    TIMESTAMP WITHOUT TIME ZONE,
            completed_on TIMESTAMP WITHOUT TIME ZONE
        );

        CREATE INDEX IF NOT EXISTS idx_top_candidate_arbitrage_reservations ON top_candidate_arbitrage_reservations (claimed_on);

        CREATE TABLE IF NOT EXISTS candidate_arbitrage_relay_results (
            candidate_arbitrage_id BIGINT NOT NULL REFERENCES candidate_arbitrages (id) ON DELETE CASCADE,
            shoot_success          BOOLEAN NOT NULL,
            failure_reason         TEXT,
            gas_used               INTEGER CHECK ((shoot_success = true and gas_used is not null) OR (shoot_success = false and gas_used is null)),
            had_fee_on_xfer_token  BOOLEAN,
            real_profit_before_fee NUMERIC(78, 0)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_arbitrage_relay_results_candidate_arbitrage_id ON candidate_arbitrage_relay_results (candidate_arbitrage_id);
        CREATE INDEX IF NOT EXISTS idx_candidate_arbitrage_relay_results_shoot_success ON candidate_arbitrage_relay_results (shoot_success);

        CREATE TABLE IF NOT EXISTS candidate_arbitrage_relay_results_used_fees (
            candidate_arbitrage_id BIGINT NOT NULL REFERENCES candidate_arbitrages (id) ON DELETE CASCADE,
            fee_used               INTEGER NOT NULL REFERENCES inferred_token_fee_on_transfer (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS top_candidate_arbitrage_campaigns (
            id                     BIGSERIAL PRIMARY KEY NOT NULL,
            reservation_id         INTEGER NOT NULL REFERENCES top_candidate_arbitrage_reservations (id) ON DELETE CASCADE,
            exchanges              BYTEA[] NOT NULL,
            directions             BYTEA[] NOT NULL,
            start_block            INTEGER NOT NULL,
            end_block              INTEGER NOT NULL,
            terminated             BOOLEAN DEFAULT TRUE
        );

        CREATE TABLE IF NOT EXISTS top_candidate_arbitrage_relay_results (
            id                     BIGSERIAL PRIMARY KEY NOT NULL,
            reservation_id         INTEGER NOT NULL REFERENCES top_candidate_arbitrage_reservations (id) ON DELETE CASCADE,
            campaign_id            BIGINT REFERENCES top_candidate_arbitrage_campaigns (id) CHECK ((shoot_success = true and campaign_id is not null) OR (shoot_success = false and campaign_id is null)),
            candidate_arbitrage_id BIGINT NOT NULL REFERENCES candidate_arbitrages (id) ON DELETE CASCADE,
            shoot_success          BOOLEAN NOT NULL,
            failure_reason         TEXT,
            gas_used               INTEGER CHECK ((shoot_success = true and gas_used is not null) OR (shoot_success = false and gas_used is null)),
            had_fee_on_xfer_token  BOOLEAN,
            real_profit_before_fee NUMERIC(78, 0)
        );

        CREATE INDEX IF NOT EXISTS idx_top_candidate_arbitrage_relay_results_candidate_arbitrage_id ON top_candidate_arbitrage_relay_results (candidate_arbitrage_id);
        CREATE INDEX IF NOT EXISTS idx_top_candidate_arbitrage_relay_results_shoot_success ON top_candidate_arbitrage_relay_results (shoot_success);

        CREATE TABLE IF NOT EXISTS top_candidate_arbitrage_relay_results_used_fees (
            top_candidate_arbitrage_relay_result_id BIGINT NOT NULL REFERENCES top_candidate_arbitrage_relay_results (id) ON DELETE CASCADE,
            fee_used                                INTEGER NOT NULL REFERENCES inferred_token_fee_on_transfer (id) ON DELETE CASCADE
        );


        CREATE TABLE IF NOT EXISTS broken_tokens (
            token_id               INTEGER NOT NULL PRIMARY KEY REFERENCES tokens (id) ON DELETE CASCADE,
            broken_at_block_number INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_broken_tokens_block_number ON broken_tokens (broken_at_block_number);

        CREATE TABLE IF NOT EXISTS banned_exchanges (
            address                BYTEA NOT NULL,
            broken_at_block_number INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_banned_exchanges ON banned_exchanges (broken_at_block_number);
        """
    )
    pass

def fill_relay_top_arbs_queue(curr: psycopg2.extensions.cursor):
    curr.execute(
        '''
        INSERT INTO top_candidate_arbitrage_reservations (start_block, end_block)
        SELECT start_block, end_block FROM block_samples
        '''
    )
    l.info(f'inserted {curr.rowcount} reservations for top arbitrages')


def fill_relay_queue(curr: psycopg2.extensions.cursor):
    curr.execute(
        '''
        SELECT block_number_start, block_number_end, priority
        FROM candidate_arbitrage_reservations
        WHERE claimed_on IS NOT NULL AND completed_on IS NOT NULL
        '''
    )

    n_inserted = 0
    n_skipped = 0
    for start_block, end_block, priority in list(curr):
        curr.execute(
            '''
            SELECT block_number
            FROM candidate_arbitrage_reshoot_blocks
            WHERE %s <= block_number AND block_number <= %s
            ''',
            (start_block, end_block)
        )
        existing_blocks = set(x for (x,) in curr)
        for block_number in range(start_block, end_block + 1):
            if block_number in existing_blocks:
                n_skipped += 1
                continue
            curr.execute(
                '''
                INSERT INTO candidate_arbitrage_reshoot_blocks (block_number, priority)
                VALUES (%s, %s)
                ''',
                (block_number, priority)
            )
            n_inserted += 1

    l.info(f'skipped inserting {n_skipped:,} blocks (already have reservation for it)')
    l.info(f'filled queue with {n_inserted:,} blocks')


def get_reservation(curr: psycopg2.extensions.cursor, worker_name: str) -> typing.Optional[typing.Tuple[int, int]]:
    curr.execute('BEGIN TRANSACTION')

    if True: # not DEBUG:
        curr.execute(
            '''
            SELECT id, block_number
            FROM candidate_arbitrage_reshoot_blocks
            WHERE claimed_on IS NULL AND completed_on IS NULL
            ORDER BY priority, block_number ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            '''
        )
    else:
        curr.execute(
            '''
            SELECT id, block_number
            FROM candidate_arbitrage_reshoot_blocks
            WHERE id = %s
            ORDER BY priority, block_number ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            ''',
            (DEBUG_RESERVATION,)
        )
    if curr.rowcount < 1:
        l.info('Finished queue')
        return

    id_, block_number = curr.fetchone()
    curr.execute(
        '''
        UPDATE candidate_arbitrage_reshoot_blocks
        SET claimed_on = NOW()::timestamp, worker = %s
        WHERE id = %s
        ''',
        (worker_name, id_),
    )
    assert curr.rowcount == 1

    if not DEBUG:
        curr.connection.commit()

    l.info(f'Processing reservation id={id_:,} block_number={block_number:,}')

    return id_, block_number


_port = collections.defaultdict(lambda: 0)
def open_ganache(
        block_number: int,
        tmpdir: str,
        worker_id: int,
    ) -> typing.Tuple[subprocess.Popen, web3.Web3, LocalAccount, str]:
    acct: LocalAccount = Account.from_key(bytes.fromhex('f96003b86ed95cb86eae15653bf4b0bc88691506141a1a9ae23afd383415c268'))

    bin_loc = '/opt/ganache-fork/src/packages/ganache/dist/node/cli.js'
    cwd_loc = '/opt/ganache-fork/'

    my_slice = worker_id
    slice_start = 10000 + my_slice * N_PORTS_PER_SLICE
    port_slice = _port[worker_id]
    ganache_port = slice_start + port_slice
    _port[worker_id] = (port_slice + 1) % N_PORTS_PER_SLICE

    web3_host = os.getenv('WEB3_HOST', 'ws://172.17.0.1:8546')
    p = subprocess.Popen(
        [
            'node',
            bin_loc,
            '--database.dbPath', tmpdir,
            '--fork.url', web3_host,
            '--fork.blockNumber', str(block_number),
            '--server.port', str(ganache_port),
            '--chain.chainId', '1',
            '--miner.timestampIncrement', '1',
            # '--chain.time', str(next_timestamp * 1_000),
            # '--chain.hardfork', 'arrowGlacier',
            '--wallet.accounts', f'{acct.key.hex()},{web3.Web3.toWei(100_000, "ether")}',
        ],
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        cwd=cwd_loc,
    )

    l.debug(f'spawned ganache on PID={p.pid} port={ganache_port}')

    w3 = web3.Web3(web3.WebsocketProvider(
            f'ws://localhost:{ganache_port}',
            websocket_timeout=60 * 10,
            websocket_kwargs={
                'max_size': 1024 * 1024 * 1024, # 1 Gb max payload
            },
        )
    )

    def patch_make_batch_request(requests: typing.Tuple[str, typing.Any]):
        ret = []
        for method, args in requests:
            ret.append(w3.provider.make_request(method, args))
        return ret

    w3.provider.make_request_batch = patch_make_batch_request

    while not w3.isConnected():
        time.sleep(0.01)

    assert p.poll() is None

    for _ in range(10):
        if w3.eth.get_balance(acct.address) == web3.Web3.toWei(100_000, 'ether'):
            break
        time.sleep(0.01)

    #
    # deploy the relayer
    #
    relayer = w3.eth.contract(
        bytecode = SHOOTER_ARTIFACT['bytecode'],
        abi = SHOOTER_ARTIFACT['abi'],
    )

    constructor_txn = relayer.constructor().buildTransaction({'from': acct.address})
    txn_hash = w3.eth.send_transaction(constructor_txn)
    receipt = w3.eth.wait_for_transaction_receipt(txn_hash)

    relayer_addr = receipt['contractAddress']
    l.debug(f'deployed relayer to {relayer_addr} with admin key {acct.address}')

    relayer = w3.eth.contract(
        address = relayer_addr,
        abi = SHOOTER_ARTIFACT['abi'],
    )

    #
    # fund the shooter with some wrapped ether
    #
    weth: web3.contract.Contract = w3.eth.contract(
        address=WETH_ADDRESS,
        abi=get_abi('weth9/WETH9.json')['abi'],
    )
    wrap = weth.functions.deposit().buildTransaction({'value': RELAYER_BALANCE_WEI, 'from': acct.address})
    wrap_hash = w3.eth.send_transaction(wrap)
    wrap_receipt = w3.eth.wait_for_transaction_receipt(wrap_hash)
    assert wrap_receipt['status'] == 1

    # transfer to shooter
    xfer = weth.functions.transfer(relayer_addr, RELAYER_BALANCE_WEI).buildTransaction({'from': acct.address})
    xfer_hash = w3.eth.send_transaction(xfer)
    xfer_receipt = w3.eth.wait_for_transaction_receipt(xfer_hash)
    assert xfer_receipt['status'] == 1

    l.debug(f'Transferred {RELAYER_BALANCE_WEI / (10 ** 18):.2f} ETH to relayer')

    exit_code = p.poll()
    assert exit_code is None, f'Expected ganache to be running but it exited with code {exit_code}'

    return p, w3, acct, relayer_addr


class CandidateArbitrage(typing.NamedTuple):
    id_: int
    exchanges: typing.List[str]
    directions: typing.List[typing.Tuple[str, str]]
    amount_in: int
    profit_before_fee: int
    block_number: int

    @property
    def campaign_key(self) -> typing.Tuple[typing.Tuple, typing.Tuple]:
        return (tuple(self.exchanges), tuple(self.directions))


def get_candidates_in_block(
        curr: psycopg2.extensions.cursor,
        block_number: int,
    ) -> typing.List[
        CandidateArbitrage
    ]:
    if True: # not DEBUG:
        curr.execute(
            '''
            SELECT id, exchanges, directions, amount_in, profit_no_fee
            FROM candidate_arbitrages ca
            WHERE block_number = %s AND
                NOT EXISTS(
                    SELECT 1
                    FROM candidate_arbitrage_relay_results carr
                    WHERE carr.candidate_arbitrage_id = ca.id
                )
            ''',
            (block_number,)
        )
    else:
        curr.execute(
            '''
            SELECT id, exchanges, directions, amount_in, profit_no_fee
            FROM candidate_arbitrages
            WHERE block_number = %s AND id = %s
            ''',
            (block_number, DEBUG_CANDIDATE)
        )

    ret = []
    for id_, bexchanges, bdirections, amount_in, profit in curr:
        exchanges = [web3.Web3.toChecksumAddress(x.tobytes()) for x in bexchanges]
        directions = [web3.Web3.toChecksumAddress(x.tobytes()) for x in bdirections]
        directions = list(zip(directions, directions[1:] + [directions[0]]))
        
        assert len(exchanges) == len(directions)

        ret.append(CandidateArbitrage(
            id_ = id_,
            exchanges = exchanges,
            directions = directions,
            amount_in = int(amount_in),
            profit_before_fee = int(profit),
            block_number=block_number
        ))
    
    return ret

class AutoAdaptShootSuccess(typing.NamedTuple):
    amount_in: int
    profit_no_fee: int
    gas: int
    token_fees_used: typing.List['TokenFee']

class DiagnosisBrokenToken(typing.NamedTuple):
    token_address: str
    reason: str

class DiagnosisFeeOnTransfer(typing.NamedTuple):
    from_address: str
    to_address: str
    token_address: str
    fee: decimal.Decimal
    round_down: bool

class DiagnosisNoArbitrageOnFeeApplied(typing.NamedTuple):
    pass

class DiagnosisBadExchange(typing.NamedTuple):
    exchange: str
    reason: str

class DiagnosisExchangeInterference(typing.NamedTuple):
    token_address: str
    exchange_address: str

class DiagnosisIncompatibleToken(typing.NamedTuple):
    exchange_address: str
    token_address: str

class DiagnosisOther(typing.NamedTuple):
    reason: str

def auto_adapt_attempt_shoot_candidate(
        w3_ganache: web3.Web3,
        account: LocalAccount,
        shooter_address: str,
        fa: FoundArbitrage,
        fee_transfer_calculator: 'InferredTokenTransferFeeCalculator',
        timestamp: int,
        must_recompute = False
    ) -> typing.Union[AutoAdaptShootSuccess, DiagnosisBrokenToken]:
    """
    Attempt the arbitrage by all means possible, reverting and re-shooting
    when a tax (fee) on transfer is detected.
    """
    # holds the list of inferred fees, so if we keep getting it wrong we can
    # start to make some reasonable conclusions
    inferred_fees: typing.Dict[typing.Any, typing.List[decimal.Decimal]] = collections.defaultdict(lambda: [])

    while True:
        with profile('evm_snapshot'):
            result = w3_ganache.provider.make_request('evm_snapshot', [])
        snapshot_id = int(result['result'][2:], base=16)

        if must_recompute:
            l.debug(f'recomputing arbitrage (fresh pricers)')
            pc = PricingCircuit(
                fa.circuit,
                fa.directions
            )
            with profile('recompute arbitrage'):
                maybe_fa = detect_arbitrages_bisection(
                    pc,
                    'latest',
                    try_all_directions=False,
                    fee_transfer_calculator=fee_transfer_calculator,
                    timestamp=timestamp,
                )
            assert len(maybe_fa) <= 1
            if len(maybe_fa) == 0:
                l.debug(f'No arbitrage on re-shoot')
                return DiagnosisNoArbitrageOnFeeApplied()
            l.debug(f'Expect to make {fa.profit / (10 ** 18):.5f} ETH on re-shoot')
            fa = maybe_fa[0]

        try:
            with profile('attempt_relay'):
                maybe_receipt = attempt_relay_candidate(
                    w3_ganache,
                    account,
                    shooter_address,
                    fa,
                    fee_transfer_calculator,
                    timestamp
                )

            if isinstance(maybe_receipt, DiagnosisBrokenToken):
                return maybe_receipt

            receipt = maybe_receipt

            if receipt['status'] != 1:
                with profile('diagnose'):
                    diagnosis = diagnose_failure(w3_ganache, account, shooter_address, fa, fee_transfer_calculator, receipt, timestamp)
                if isinstance(diagnosis, (DiagnosisBrokenToken, DiagnosisBadExchange, DiagnosisExchangeInterference, DiagnosisIncompatibleToken, DiagnosisOther)):
                    return diagnosis
                elif isinstance(diagnosis, DiagnosisFeeOnTransfer):
                    diagnosis: DiagnosisFeeOnTransfer
                    k = (diagnosis.token_address, diagnosis.from_address, diagnosis.to_address)
                    all_inferences = inferred_fees[k]

                    if len(all_inferences) > 0:
                        median_fee = np.median(all_inferences + [diagnosis.fee])
                        min_fee = min(diagnosis.fee, np.min(all_inferences))
                        max_fee = max(diagnosis.fee, np.max(all_inferences))

                        l.debug(f'n_failures {len(all_inferences) + 1}')
                        l.debug(f'min_fee    {min_fee}')
                        l.debug(f'median_fee {median_fee}')
                        l.debug(f'max_fee    {max_fee}')

                        # adjust fee down gradually if we keep failing repeatedly
                        using_fee = min_fee - decimal.Decimal('0.00001')
                        l.debug(f'using fee {using_fee}')
                    else:
                        all_inferences.append(diagnosis.fee)
                        using_fee = diagnosis.fee

                    # plug-in fee and attempt to shoot again
                    fee_transfer_calculator.propose(
                        diagnosis.token_address,
                        diagnosis.from_address,
                        diagnosis.to_address,
                        using_fee,
                        diagnosis.round_down
                    )
                must_recompute = True
                l.debug(f'Diagnosed, re-shooting...')
            else:
                # shoot success
                weth: web3.contract.Contract = w3_ganache.eth.contract(
                    address=WETH_ADDRESS,
                    abi=get_abi('erc20.abi.json'),
                )
                relayer_balance = weth.functions.balanceOf(shooter_address).call()
                profit = relayer_balance - RELAYER_BALANCE_WEI
                if profit < 0:
                    l.critical('------------------------')
                    l.critical(f'Profit was negative! {profit / (10 ** 18):.5f} ETH')
                    l.critical(f'amount_in .......... {fa.amount_in}')
                    l.critical(f'expected profit .... {fa.profit} wei')
                    for p in fa.circuit:
                        l.critical(str(p))
                    l.critical('------------------------')
                    diagnose_failure(w3_ganache, account, shooter_address, fa, fee_transfer_calculator, receipt, timestamp)
                    raise Exception('what')
                return AutoAdaptShootSuccess(
                    fa.amount_in,
                    profit_no_fee = profit,
                    gas = receipt['gasUsed'],
                    token_fees_used = fee_transfer_calculator.get_fees_used(fa)
                )
        finally:
            with profile('evm_revert'):
                result = w3_ganache.provider.make_request('evm_revert', [snapshot_id])
            assert result['result'] == True, 'snapshot revert should be success'


def attempt_relay_candidate(
        w3_ganache: web3.Web3,
        account: LocalAccount,
        shooter_address: str,
        fa: FoundArbitrage,
        fee_transfer_calculator: FeeTransferCalculator,
        timestamp: int
    ) -> web3.types.TxReceipt:
    if DEBUG:
        l.debug(f'relaying candidate with timestamp={timestamp}')
    encoded, approvals_required = construct_arbitrage(
        fa,
        shooter_address,
        'latest',
        fee_transfer_calculator,
        timestamp = timestamp
    )

    for addr, token in approvals_required:
        token_extended = bytes.fromhex(token[2:]).rjust(32, b'\x00')
        addr_extended = bytes.fromhex(addr[2:]).rjust(32, b'\x00')
        payload = DO_APPROVE_SELECTOR + token_extended + addr_extended
        txn = {
            'from': account.address,
            'to': shooter_address,
            'data': payload,
            'chainId': 1,
            'gas': 1_000_000,
            'nonce': w3_ganache.eth.get_transaction_count(account.address),
            'gasPrice': 10_000 * (10 ** 9)
        }
        signed = w3_ganache.eth.account.sign_transaction(txn, account.key)

        txn_hash = w3_ganache.eth.send_raw_transaction(signed['rawTransaction'])
        receipt = w3_ganache.eth.wait_for_transaction_receipt(txn_hash)

        if receipt['status'] != 1:
            l.debug(f'Failed to send approval transaction for {token}')
            return DiagnosisBrokenToken(token_address=token, reason='failed to send approval')

    result = w3_ganache.provider.make_request('miner_stop', [])
    assert result['result'] == True

    payload = serialize(encoded)

    # selector 0 for arbitrage
    payload = b'\x00' * 4 + payload

    txn = {
        'from': account.address,
        'to': shooter_address,
        'data': payload,
        'chainId': 1,
        'gas': 1_500_000,
        'nonce': w3_ganache.eth.get_transaction_count(account.address),
        'gasPrice': 10_000 * (10 ** 9)
    }
    signed = w3_ganache.eth.account.sign_transaction(txn, account.key)

    txn_hash = w3_ganache.eth.send_raw_transaction(signed['rawTransaction'])
    
    with profile('evm_mine'):
        w3_ganache.provider.make_request('evm_mine', [timestamp])
        receipt = w3_ganache.eth.wait_for_transaction_receipt(txn_hash)

    result = w3_ganache.provider.make_request('miner_start', [])
    assert result['result'] == True

    return receipt


def diagnose_failure(
        w3_ganache: web3.Web3,
        account: LocalAccount,
        shooter_address: str,
        fa: FoundArbitrage,
        fee_transfer_calculator: FeeTransferCalculator,
        receipt: web3.types.TxReceipt,
        timestamp: int,
    ) -> DiagnosisBrokenToken:
    if DEBUG:
        txn = w3_ganache.eth.get_transaction(receipt['transactionHash'])
        with tempfile.NamedTemporaryFile() as f:
            fname = f.name
            got = w3_ganache.provider.make_request(
                'debug_traceTransactionToFile',
                [
                    receipt['transactionHash'].hex(),
                    {'disableStorage': True, 'file_name': fname},
                ]
            )
            assert got['result'] == 'OK', f'expected OK result but got {got}'

            stat = os.stat(fname)
            l.debug(f'File size was {stat.st_size // 1024 // 1024} MB')

            trace = {'result': json.load(f)}

        if 'result' not in trace:
            print(trace)

        with open('/mnt/goldphish/trace.txt', mode='w') as fout:
            for sl in trace['result']['structLogs']:
                fout.write(str(sl) + '\n')

        decoded_old = decode_trace_calls(trace['result']['structLogs'], txn, receipt)
        pretty_print_trace(decoded_old, txn, receipt)


    l.debug('diagnosing shoot failure...')
    l.debug(f'gas usage: {receipt["gasUsed"]:,}')
    if receipt['gasUsed'] > 1_000_000:
        return DiagnosisOther('too much gas')

    # gather /expected/ token transfer calls out of exchanges
    expected_transfers: typing.Dict[typing.Tuple[str, str], int] = {}
    last_token = fa.pivot_token
    curr_amt = fa.amount_in
    for i, (p, (t_in, t_out)) in enumerate(zip(fa.circuit, fa.directions)):
        assert last_token == t_in
        curr_amt, _ = p.token_out_for_exact_in(t_in, t_out, curr_amt, receipt['blockNumber'] - 1, timestamp=timestamp)
        last_token = t_out

        if i + 1 < len(fa.circuit):
            next_exchange_addr = fa.circuit[i + 1].address
        else:
            next_exchange_addr = None

        if isinstance(p, (BalancerV2WeightedPoolPricer, BalancerV2LiquidityBootstrappingPoolPricer)):
            sender = BALANCER_VAULT_ADDRESS
        else:
            sender = p.address

        amt_transferred = curr_amt

        if i + 1 < len(fa.circuit):
            if isinstance(fa.circuit[i + 1], (BalancerV2WeightedPoolPricer, BalancerV2LiquidityBootstrappingPoolPricer)):
                next_exchange_addr = BALANCER_VAULT_ADDRESS
            else:
                next_exchange_addr = fa.circuit[i + 1].address
        else:
            next_exchange_addr = shooter_address

        # If the next exchange is Balancer V1 or V2, or if the current exchange is Balancer V1, then we need to account for two fees:
        # (1) to self, (2) to next exchange
        if isinstance(p, BalancerPricer) or \
            (i + 1 < len(fa.circuit) and \
                isinstance(fa.circuit[i + 1], (BalancerPricer, BalancerV2WeightedPoolPricer, BalancerV2LiquidityBootstrappingPoolPricer))
            ):
            # transfer #1
            curr_amt = fee_transfer_calculator.out_from_transfer(t_out, sender, shooter_address, curr_amt)
            expected_transfers[(sender, t_out)] = (amt_transferred, curr_amt)

            # transfer #2 only happens if this token is not the end of the circuit (ie, there is no more exchanges)
            if i + 1 < len(fa.circuit):
                amt_transferred_2 = curr_amt
                curr_amt = fee_transfer_calculator.out_from_transfer(t_out, shooter_address, next_exchange_addr, curr_amt)
                expected_transfers[(shooter_address, t_out)] = (amt_transferred_2, curr_amt)
        else:
            curr_amt = fee_transfer_calculator.out_from_transfer(t_out, sender, next_exchange_addr, curr_amt)
            expected_transfers[(sender, t_out)] = (amt_transferred, curr_amt)


        assert curr_amt >= 0, 'negative token balance is not possible'
    l.debug(f'Expect {(curr_amt - fa.amount_in) / (10 ** 18):.5f} ETH profit')
    assert last_token == fa.pivot_token

    prior_balances: typing.Dict[typing.Tuple[str, str], int] = {}
    # gather token balances of exchanges to ensure transfers /should/ succeed
    for address, token in sorted(expected_transfers.keys()):
        this_erc20: web3.contract.Contract = w3_ganache.eth.contract(address=token, abi=get_abi('erc20.abi.json'))
        try:
            with profile('get_balance'):
                bal = this_erc20.functions.balanceOf(address).call(block_identifier=receipt['blockNumber'] - 1)
        except ValueError as e:
            if 'VM Exception while processing transaction: revert' in str(e):
                return DiagnosisBrokenToken(
                    token_address = token,
                    reason = 'balanceOf query reverts',
                )
            raise
        prior_balances[(address, token)] = bal

    for (address, token), (amount, _) in expected_transfers.items():
        # ignore shooter, which is merely relaying and has no start balance
        if address == shooter_address:
            continue

        prior_bal = prior_balances[(address, token)]
        # We thought we could pull more token out than existed in the DeX at the start of execution.
        # This probably means that we have a 'rebasing' token or something, which means your balance
        # can go _down_ without ever interacting with the token.
        for p in fa.circuit:
            if p.address == address and isinstance(p, UniswapV2Pricer):
                bal = p.get_value_locked(token_address=token, block_identifier = receipt['blockNumber'] - 1)
                if bal > prior_bal:
                    l.debug(f'Uniswap V2 exchange {p.address} reports token={token} balance of {bal} but actual balance is {prior_bal} !!')
                    return DiagnosisBrokenToken(token_address=token, reason='Unexpected balance decrease')

        if amount >= prior_bal:
            # something about this token is broken
            return DiagnosisBrokenToken(token, reason='Balance mismatch, need too much out')
        l.debug(f'Expect {address} to transfer {amount} of {token} out (prior balance {prior_bal})')

    all_tokens: typing.Set[str] = set(x for x, _ in fa.directions)
    all_uniswap_v2: typing.Set[str] = set(x.address for x in fa.circuit if isinstance(x, UniswapV2Pricer))
    all_balancer_v1: typing.Set[str] = set(x.address for x in fa.circuit if isinstance(x, BalancerPricer))

    txn = w3_ganache.eth.get_transaction(receipt['transactionHash'])

    with profile('debug_callTrace'):
        result = w3_ganache.provider.make_request('debug_callTrace', [receipt['transactionHash'].hex()])

    decoded = parse_ganache_call_trace(result['result'])

    #
    # DFS through call tree to see if source of revert was a token
    #

    if DEBUG:
        pretty_print_trace(decoded, txn, receipt)

    # Also note transfer recipients for phase 2 below, just to save on traversals
    xfer_recipients: typing.Set[typing.Tuple[str, str]] = set()

    # Also record all Token calls, used before phase 2 below
    token_calls = []

    q = [decoded]
    while len(q) > 0:
        item = q.pop()
        assert w3_ganache.isChecksumAddress(item['callee'])

        if item['callee'] in all_balancer_v1 and \
            len(item['actions']) > 0 and \
            item['actions'][-1]['type'] == 'REVERT' and \
            item['actions'][-1]['message'].strip() == b'ERR_MATH_APPROX':
            l.debug(f'Exchange {item["callee"]} reverted due to approx math error')
            return DiagnosisOther(
                reason = 'Balancer v1: ERR_MATH_APPROX'
            )

        if item['callee'] in all_uniswap_v2 and \
            len(item['actions']) > 0 and \
            item['actions'][-1]['type'] == 'REVERT' and \
            item['actions'][-1]['message'].strip() == b'UniswapV2: OVERFLOW':

            # overflowed
            l.debug(f'Exchange {item["callee"]} overflowed, marking as bad...')
            return DiagnosisBadExchange(
                exchange = item['callee'],
                reason = 'Uniswap V2: OVERFLOW'
            )

        was_token_call = False
        if item['callee'] in all_tokens and item['callee'] != WETH_ADDRESS:
            token: str = item['callee']
            try:
                f, decoded_func_input = erc20.decode_function_input(item['args'])
            except ValueError as e:
                if 'Could not find any function with matching selector' in str(e):
                    f = None
                    decoded_func_input = None
                else:
                    raise

            if f is not None:
                was_token_call = True
                token_calls.append(item)

            # we don't care if this isn't a transfer
            if f is not None and f.fn_name in ['transfer', 'transferFrom']:
                if f.fn_name == 'transfer':
                    from_addr = item['from']
                elif f.fn_name == 'transferFrom':
                    from_addr = w3_ganache.toChecksumAddress(decoded_func_input['_from'])

                # balancer v1 needs a 'true' response or else it reverts
                if item['from'] in all_balancer_v1 and (len(item['actions']) == 0 or item['actions'][-1]['type'] != 'RETURN'):
                    l.debug(f'Token {token} is incompatible with balancer v1 (no return)')
                    return DiagnosisIncompatibleToken(exchange_address=item['from'], token_address=token)

                # ensure that the amount transferred is as expected
                maybe_xfer = expected_transfers.get((from_addr, token), None)
                assert maybe_xfer is not None, f'Unexpected transfer from {from_addr} of {token}'
                (expected_amount, _) = maybe_xfer

                # sometimes can send more, than expected that's ok
                assert decoded_func_input['_value'] >= expected_amount, f'expected {from_addr} to send {expected_amount} of {token} but got {decoded_func_input["_value"]}'

                did_revert = (len(item['actions']) > 0) and (item['actions'][-1]['type'] == 'REVERT')

                # Still may have indicated non-success -- if this is a call to transfer or transferFrom, we need to check
                # the return boolean value
                if not did_revert and len(item['actions']) > 0 and item['actions'][-1]['type'] == 'RETURN':
                    returned = item['actions'][-1]['data']
                    if len(returned) > 0 and all(x == 0 for x in returned[:32]):
                        # returned False from transfer (i.e., reverted)
                        l.debug(f'Token {token} returned False from transfer() call')
                        did_revert = True

                if did_revert:
                    # see if this is a transfer call
                    l.debug(f'Broken token: {token}')
                    return DiagnosisBrokenToken(token_address=token, reason=f'token reverts on transfer')
                else:
                    # did not revert, but we still want to note the transfer recipients
                    recipient = w3_ganache.toChecksumAddress(decoded_func_input['_to'])
                    xfer_recipients.add((recipient, token))

        
        # add all outbound calls but do NOT descend into a token's internals
        if not was_token_call:
            for sub_item in reversed(item['actions']):
                if 'CALL' in sub_item['type']:
                    q.append(sub_item)

    #
    # Attempt to diagnose token-exchange interference
    #
    for token_call in token_calls:
        q = [token_call]

        while len(q) > 0:
            item = q.pop()

            if item['callee'] in all_uniswap_v2:
                # this should not happen within a token transfer, we have interference
                token_address = token_call['callee']
                exchange_address = item['callee']
                l.warning(f'Token {token_address} interferes with {exchange_address} on transfer')
                return DiagnosisExchangeInterference(
                    token_address = token_address,
                    exchange_address = exchange_address,
                )

            for sub_item in reversed(item['actions']):
                if 'CALL' in sub_item['type']:
                    q.append(sub_item)

    # Add record of prior balances for all transfer recipients
    for address, token in xfer_recipients:
        if (address, token) not in prior_balances:
            # must query
            this_erc20: web3.contract.Contract = w3_ganache.eth.contract(address=token, abi=get_abi('erc20.abi.json'))
            try:
                with profile('get_balance'):
                    bal = this_erc20.functions.balanceOf(address).call(block_identifier=receipt['blockNumber'] - 1)
            except ValueError as e:
                if 'VM Exception while processing transaction: revert' in str(e):
                    return DiagnosisBrokenToken(
                        token_address = token,
                        reason = 'balanceOf query reverts',
                    )
                raise
            prior_balances[(address, token)] = bal

    for (address, token), val in prior_balances.items():
        for p in fa.circuit:
            if isinstance(p, UniswapV2Pricer) and p.address == address:
                tvl = p.get_value_locked(token, receipt['blockNumber'] - 1)
                if tvl != val:
                    l.warning(f'uniswap v2 {address} reserves-balance mismatch - balanceOf={val} vs reserves={tvl}')
                if val < tvl:
                    # Uniswap thinks that it has more in reserves than in reality, we can't handle this type of token
                    l.debug(f'Marking token {token} as broken')
                    return DiagnosisBrokenToken(token_address=token, reason='Unexpected balance decrease')

    #
    # DFS through call tree to see if we have unexpected fee-on-transfer
    #

    did_receive_transfer_from: typing.Dict[typing.Tuple[str, str], str] = {}
    did_send_transfer: typing.Set[typing.Tuple[str, str]] = set()
    expected_amount_recieved: typing.Dict[typing.Tuple[str, str], int] = {}
    expected_amount_sent: typing.Dict[typing.Tuple[str, str], int] = {}
    transfer_amount_attempted: typing.Dict[typing.Tuple[str, str], int] = {}

    q = [decoded]
    while len(q) > 0:
        item = q.pop()

        if 'callee' not in item:
            print(item)

        was_token_call = False

        if item['callee'] in all_tokens and item['callee'] != WETH_ADDRESS:
            token_calls.append(item)

            token = item['callee']
            try:
                f, decoded_func_input = erc20.decode_function_input(item['args'])
            except ValueError as e:
                if 'Could not find any function with matching selector' in str(e):
                    f = None
                    decoded_func_input = None
                else:
                    raise

            if f is not None:
                was_token_call = True

                if f.fn_name in ['transfer', 'transferFrom']:
                    if f.fn_name == 'transfer':
                        from_addr = item['from']
                    elif f.fn_name == 'transferFrom':
                        from_addr = w3_ganache.toChecksumAddress(decoded_func_input['_from'])

                    recipient = decoded_func_input['_to']

                    expected_amount_sent[(from_addr, token)] = decoded_func_input['_value']
                    did_send_transfer.add((from_addr, token))

                    expected_amount_recieved[(recipient, token)] = fee_transfer_calculator.out_from_transfer(
                        token,
                        from_addr,
                        recipient,
                        decoded_func_input['_value']
                    )
                    transfer_amount_attempted[(recipient, token)] = decoded_func_input['_value']

                    # This is a transfer -- just note that the transfer occurred
                    assert (recipient, token) not in did_receive_transfer_from
                    did_receive_transfer_from[(recipient, token)] = from_addr

                elif f.fn_name == 'balanceOf':
                    assert len(item['actions']) > 0
                    if item['actions'][-1]['type'] == 'REVERT':
                        return DiagnosisBrokenToken(
                            token_address = token,
                            reason = 'balanceOf reverts'
                        )
                    assert item['actions'][-1]['type'] == 'RETURN', f'expected last item to be return but got {item["actions"][-1]["type"]}'

                    owner = w3_ganache.toChecksumAddress(decoded_func_input['_owner'])

                    if (owner, token) in did_send_transfer:
                        # ensure that after send the balance is as expected

                        prior_balance = prior_balances[(owner, token)]

                        new_balance = int.from_bytes(item["actions"][-1]['data'][:32], byteorder='big', signed=False)

                        expected_new_balance = prior_balance - expected_amount_sent[(owner, token)]

                        if new_balance < expected_new_balance:
                            l.debug(f'Balance decreased too much on send token {token}: expected {expected_new_balance} but got {new_balance}')
                            return DiagnosisBrokenToken(
                                token_address = token,
                                reason = 'Balance decreased too much on transfer out'
                            )

                    #
                    # This may be what we want to check in this stage, see if this is /before/ or /after/ the transfer
                    if (owner, token) in did_receive_transfer_from:

                        # we want to check the balance here!
                        prior_balance = prior_balances[(owner, token)]

                        new_balance = int.from_bytes(item["actions"][-1]['data'][:32], byteorder='big', signed=False)

                        # see if this matches what we expect
                        expected_balance = prior_balances[(owner, token)] + expected_amount_recieved[(owner, token)]
                        if new_balance > (expected_balance * 110 // 100):
                            l.warning(f'Balance went much higher than expected after transfer for token {token} actual_balance={new_balance} expected_balance={expected_balance}')

                        if new_balance < expected_balance:
                            l.debug(f'saw unexpected balance {new_balance} ({hex(new_balance)}) on owner {owner} for token {token}')
                            # there was probably a fee -- let's figure it out
                            real_amount_received = new_balance - prior_balance
                            dtransfer_amount = decimal.Decimal(transfer_amount_attempted[(owner, token)])
                            fee_rate_before_round = decimal.Decimal(real_amount_received) / dtransfer_amount
                            fee_rate = round(fee_rate_before_round, 5)

                            if fee_rate > 2:
                                raise Exception('fee_rate too big!!!')

                            # attempt to see if that fee_rate would have worked -- and if so, which way to round
                            amt_out = dtransfer_amount * fee_rate
                            amt_out_floor = math.floor(amt_out)
                            amt_out_ceil = math.ceil(amt_out)
                            if amt_out_floor == real_amount_received:
                                round_down = True
                            elif amt_out_ceil == real_amount_received:
                                round_down = False
                            else:
                                # neither of these worked, just guess we need to round down
                                l.warning(f'Token {token} did not have clean inferred fee_rate ({fee_rate_before_round}) -- assuming rounding down')
                                round_down = True

                            l.debug(f'Computed fee_rate={fee_rate} (round_down={round_down}) on token {token} from {did_receive_transfer_from[(owner, token)]} to {owner}')
                            return DiagnosisFeeOnTransfer(
                                from_address = did_receive_transfer_from[(owner, token)],
                                to_address = owner,
                                token_address = token,
                                fee = fee_rate,
                                round_down = round_down
                            )

        if not was_token_call:
            # add all outbound calls, but DO NOT dive into a tokens' internal calls
            for sub_item in reversed(item['actions']):
                if 'CALL' in sub_item['type']:
                    q.append(sub_item)


    for p in fa.circuit:
        l.info(str(p))

    raise Exception('Failure to diagnose')


class TopArbCampaign(typing.NamedTuple):
    start_block: int
    end_block: typing.Optional[int]
    members: typing.List[typing.Tuple[int, AutoAdaptShootSuccess]]
    exchanges: typing.List[str]
    directions: typing.List[typing.Tuple[str, str]]
    terminated: bool


def relay_top_arbs(w3: web3.Web3, curr: psycopg2.extensions.cursor, worker_id: int):
    l.info(f'relaying top arbitrages as worker #{worker_id}')

    curr.execute(
        '''
        CREATE TEMP TABLE active_campaigns (
            exchanges       BYTEA[] NOT NULL,
            directions      BYTEA[] NOT NULL,
            block_last_seen INTEGER NOT NULL
        );
        '''
    )

    curr.execute(
        '''
        SELECT DISTINCT address FROM balancer_exchanges
        '''
    )
    balv1_exchanges = set(w3.toChecksumAddress(x.tobytes()) for (x,) in curr)

    curr.execute(
        '''
        SELECT DISTINCT address FROM balancer_v2_exchanges
        '''
    )
    balv2_exchanges = set(w3.toChecksumAddress(x.tobytes()) for (x,) in curr)

    while True:
        if not DEBUG: curr.connection.commit()

        curr.execute(
            '''
            SELECT id, start_block, end_block
            FROM top_candidate_arbitrage_reservations
            WHERE claimed_on IS NULL AND completed_on IS NULL
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            '''
        )
        if curr.rowcount == 0:
            l.info('out of work, done')
            break

        (reservation_id, start_block, end_block) = curr.fetchone()
        curr.execute(
            'UPDATE top_candidate_arbitrage_reservations SET claimed_on = now()::timestamp WHERE id = %s',
            (reservation_id,)
        )
        if not DEBUG: curr.connection.commit()

        l.info(f'Working on reservation id={reservation_id:,} from block {start_block:,} to {end_block:,}')

        relay_top_arbs_in_range(connect_web3(), curr, balv1_exchanges, balv2_exchanges, reservation_id, start_block, end_block, worker_id)

        curr.execute(
            'UPDATE top_candidate_arbitrage_reservations SET completed_on = now()::timestamp WHERE id = %s',
            (reservation_id,)
        )
        if not DEBUG: curr.connection.commit()


def relay_top_arbs_in_range(
        w3: web3.Web3,
        curr: psycopg2.extensions.cursor,
        balv1_exchanges: typing.Set[str],
        balv2_exchanges: typing.Set[str],
        reservation_id: int,
        reservation_start_block: int,
        reservation_end_block: int,
        worker_id: int,
    ):
    rolling_window = collections.deque()

    active_campaigns: typing.Dict[typing.Any, TopArbCampaign] = {}
    curr.execute(
        '''
        TRUNCATE TABLE active_campaigns;
        '''
    )

    curr.execute(
        '''
        SELECT MIN(block_number)
        FROM large_candidate_arbitrages
        WHERE block_number >= %s
        ''',
        (reservation_start_block,)
    )
    (lowest_block_with_large_arb,) = curr.fetchone()
    if lowest_block_with_large_arb is None:
        l.info('no large arbitrages in this reservation, done')
        return

    l.info(f'starting at block {lowest_block_with_large_arb:,}')

    curr.execute('SELECT MAX(end_block) FROM block_samples')
    (global_end_block_inclusive,) = curr.fetchone()

    def pop_rolling_window():
        _, proc, tmpd, _ = rolling_window.popleft()
        proc: subprocess.Popen
        tmpd: tempfile.TemporaryDirectory
        proc.kill()
        proc.wait()
        assert proc.poll() is not None
        tmpd.cleanup()

    account = None
    relayer_address = None
    def push_rolling_window(needed_block: int):
        nonlocal account, relayer_address
        tmpd = tempfile.TemporaryDirectory(dir='/mnt/goldphish/tmp')
        proc, w3_ganache, account, relayer_address = open_ganache(needed_block, tmpd.name, worker_id * 2)

        if needed_block >= w3_ganache.eth.block_number:
            l.critical(subprocess.check_output(['ps', 'aux']).decode('ascii'))
        assert w3_ganache.eth.block_number > needed_block
        rolling_window.append((needed_block, proc, tmpd, w3_ganache))


    curr.execute(
        '''
        SELECT candidate_arbitrage_id
        FROM top_candidate_arbitrage_relay_results
        WHERE reservation_id = %(reservation_id)s
        ''',
        {
            'reservation_id': reservation_id,
        }
    )
    already_relayed_ids = set(x for (x,) in curr)


    # go through blocks, maintaining the rolling window
    for block_number in itertools.count(lowest_block_with_large_arb):
        # global hard stop
        if block_number > global_end_block_inclusive:
            l.info('Reached global end; done')
            break

        # reservation stop conditions
        if block_number > reservation_end_block and len(active_campaigns) == 0:
            l.info('Reached end of reservation; done')
            break

        # secondary reservation stop condition -- we went way too far
        assert block_number <= reservation_end_block + 100

        #
        # some setup
        l.debug(f'Processing block {block_number:,}')
        failed_relays: typing.List[typing.Tuple[int, str]] = []
        timestamp_to_use = w3.eth.get_block(block_number + 1)['timestamp']

        # construct the query
        # we need to decide whether to include new campaigns as we see existing ones to completion
        query = '''
            SELECT ca.id, ca.exchanges, ca.directions, ca.amount_in, ca.profit_no_fee
            FROM candidate_arbitrages ca
            JOIN active_campaigns ac ON ac.exchanges = ca.exchanges AND ac.directions = ca.directions
            WHERE ca.block_number = %(block_number)s
        '''
        if block_number <= reservation_end_block:
            query += '''
                UNION
                SELECT ca.id, ca.exchanges, ca.directions, ca.amount_in, ca.profit_no_fee
                FROM candidate_arbitrages ca
                JOIN large_candidate_arbitrages la ON ca.id = la.candidate_arbitrage_id
                WHERE la.block_number = %(block_number)s
            '''

        curr.execute(
            query,
            {'block_number': block_number}
        )

        candidate_fas: typing.List[CandidateArbitrage] = []
        used_keys = set()
        for id_, bexchanges, bdirections, amount_in, profit in curr.fetchall():
            if id_ in already_relayed_ids:
                continue

            exchanges = [web3.Web3.toChecksumAddress(x.tobytes()) for x in bexchanges]
            directions = [web3.Web3.toChecksumAddress(x.tobytes()) for x in bdirections]
            directions = list(zip(directions, directions[1:] + [directions[0]]))
            assert len(exchanges) == len(directions)
            assert directions[0][0] == WETH_ADDRESS

            candidate = CandidateArbitrage(
                id_ = id_,
                exchanges = exchanges,
                directions = directions,
                amount_in = int(amount_in),
                profit_before_fee = int(profit),
                block_number=block_number
            )

            if candidate.campaign_key in used_keys:
                l.warning(f'skipping duplicate candidate id={id_}')
                continue
            used_keys.add(candidate.campaign_key)


            candidate_fas.append(candidate)

        #
        # repair the rolling window, if it is needed
        if len(candidate_fas) > 0:
            # start by removing anything from a block more than 10 behind this one
            while len(rolling_window) > 0 and rolling_window[0][0] < block_number - 10:
                pop_rolling_window()
            
            assert len(rolling_window) == 0 or rolling_window[0][0] == block_number - 10

            # start filling in the window with blocks that we need
            for needed_block in range(block_number - (10 - len(rolling_window)), block_number + 1):
                push_rolling_window(needed_block)

            assert set(x[0] for x in rolling_window) == set(range(block_number - 10, block_number + 1))

        # remove unneeded stuff for simplification of the process below
        simplified_window = [(bn, w3_ganache) for bn, _, _, w3_ganache in rolling_window]

        pricer_cache: typing.Dict[str, BaseExchangePricer] = {}
        completed_campaigns: typing.List[TopArbCampaign] = []

        def reset_window(_):
            nonlocal w3
            # destroys the rolling window and re-creates it
            l.warning(f'Resetting rolling window')
            w3 = connect_web3()
            pricer_cache.clear()
            simplified_window.clear()

            while len(rolling_window) > 0:
                pop_rolling_window()

            for needed_block in range(block_number - 10, block_number + 1):
                push_rolling_window(needed_block)

            assert set(x[0] for x in rolling_window) == set(range(block_number - 10, block_number + 1))
            simplified_window.extend([(bn, w3_ganache) for bn, _, _, w3_ganache in rolling_window])

        @backoff.on_exception(
            backoff.expo,
            asyncio.exceptions.TimeoutError,
            max_time = 10 * 60,
            factor = 4,
            on_backoff = reset_window,
        )
        def relay_candidate(candidate: CandidateArbitrage):
            # look up what campaign this belongs to
            campaign = active_campaigns.get(candidate.campaign_key, None)

            # construct the FoundArbitrage
            # start by creating the circuit
            circuit = []
            for exchange in candidate.exchanges:
                maybe_pricer = pricer_cache.get(exchange, None)
                if maybe_pricer is None:
                    maybe_pricer = load_pricer_for(simplified_window[10][1], curr, exchange)
                    assert maybe_pricer is not None, f'could not load pricer for {exchange}'
                    pricer_cache[exchange] = maybe_pricer
                circuit.append(maybe_pricer)

            fa = FoundArbitrage(
                amount_in   = candidate.amount_in,
                circuit     = circuit,
                directions  = candidate.directions,
                pivot_token = candidate.directions[0][0],
                profit      = candidate.profit_before_fee,
            )

            #
            # attempt to do the relaying
            l.debug(f'Relaying candidate id={candidate.id_}')
            try:
                new_campaign_or_failure_reason = relay_top_candidate(w3, curr, account, relayer_address, timestamp_to_use, simplified_window, candidate, fa, campaign, worker_id)
            except TokenNotAvailable as e:
                new_campaign_or_failure_reason = f'Balancer v1: {str(e)}'
                l.critical(f'Balancer v1 token not available: {str(e)}')

            assert isinstance(new_campaign_or_failure_reason, (str, TopArbCampaign)), f'unexpected new_campaign={repr(new_campaign)}'

            if isinstance(new_campaign_or_failure_reason, str):
                failure_reason = new_campaign_or_failure_reason
                # the campaign either didnt start or just ended
                failed_relays.append((candidate.id_, failure_reason))
                if campaign is not None:
                    # the campaign just ended
                    campaign = campaign._replace(end_block = candidate.block_number - 1, terminated = True)
                    completed_campaigns.append(campaign)
                    del active_campaigns[candidate.campaign_key]
                    curr.execute(
                        'DELETE FROM active_campaigns WHERE exchanges = %s AND directions = %s',
                        (
                            [bytes.fromhex(x[2:]) for x in candidate.exchanges],
                            [bytes.fromhex(x[2:]) for x, _ in candidate.directions],
                        )
                    )
                    assert curr.rowcount == 1
            else:
                # the campaign continued
                assert isinstance(new_campaign_or_failure_reason, TopArbCampaign)
                new_campaign = new_campaign_or_failure_reason

                if campaign is None:
                    curr.execute(
                        'INSERT INTO active_campaigns (exchanges, directions, block_last_seen) VALUES (%s, %s, %s)',
                        (
                            [bytes.fromhex(x[2:]) for x in candidate.exchanges],
                            [bytes.fromhex(x[2:]) for x, _ in candidate.directions],
                            block_number,
                        )
                    )
                    assert curr.rowcount == 1
                else:
                    curr.execute(
                        'UPDATE active_campaigns SET block_last_seen = %s WHERE exchanges = %s AND directions = %s',
                        (
                            block_number,
                            [bytes.fromhex(x[2:]) for x in candidate.exchanges],
                            [bytes.fromhex(x[2:]) for x, _ in candidate.directions],
                        )
                    )
                    assert curr.rowcount == 1, f'Expected rowcount 1 but got {curr.rowcount}'
                active_campaigns[candidate.campaign_key] = new_campaign

        l.debug(f'Relaying {len(candidate_fas):,} arbitrages in block {block_number:,}')
        for candidate in candidate_fas:
            relay_candidate(candidate)

        dead_campaigns: typing.List[TopArbCampaign] = []

        if len(active_campaigns) > 0:
            # If there's any campaign we still think is active, where an exchange was updated
            # this block, but we didn't see a good canddiate relayed, then the campaign ended last block

            curr.execute(
                '''
                SELECT exchange_address
                FROM exchanges_updated_in_block
                WHERE block_number = %s
                ''',
                (block_number,)
            )
            updated_exchanges = set(w3.toChecksumAddress(x.tobytes()) for (x,) in curr)
            monitored_exchanges = set.union(*(set(c.exchanges) for c in active_campaigns.values()))

            balv1_updates = updated_exchanges.intersection(monitored_exchanges).intersection(balv1_exchanges)
            balv2_updates = updated_exchanges.intersection(monitored_exchanges).intersection(balv2_exchanges)

            if len(balv1_updates) > 0 or len(balv2_updates) > 0:
                # get all block logs
                l.debug(f'Getting block logs')
                f: web3._utils.filters.Filter = w3.eth.filter({
                    'startBlock': block_number,
                    'endBlock': block_number,
                })
                block_logs = f.get_all_entries()

            # get relevant logs for balancer to see _what_ was updated
            pairs_updated: typing.Dict[str, typing.List[typing.Tuple[str, str]]] = {}

            for balv1_addr in sorted(balv1_updates):
                pricer = load_pricer_for(w3, curr, balv1_addr)
                observation = pricer.observe_block(block_logs)
                pairs_updated[balv1_addr] = observation.pair_prices_updated

            for balv2_addr in sorted(balv2_updates):
                pricer = load_pricer_for(w3, curr, balv2_addr)
                observation = pricer.observe_block(block_logs)
                pairs_updated[balv2_addr] = observation.pair_prices_updated

            for campaign in active_campaigns.values():
                # did this update?
                these_updated_exchanges = updated_exchanges.intersection(campaign.exchanges)

                if len(these_updated_exchanges) == 0:
                    # no update, whatsoever ignore
                    continue

                if campaign.end_block == block_number:
                    # the update extended the active campaign, ignore
                    continue

                these_balv1_updates = these_updated_exchanges.intersection(balv1_exchanges)
                these_balv2_updates = these_updated_exchanges.intersection(balv2_exchanges)

                if len(these_balv1_updates) == 0 and len(these_balv2_updates) == 0:
                    # if there's no Balancer updates, and we didn't see a new campaign (already checked above),
                    # then we need to kill this campaign
                    dead_campaigns.append(campaign._replace(end_block = block_number - 1))
                    continue

                had_balancer_update = False
                for exchange, (t_in, t_out) in zip(campaign.exchanges, campaign.directions):
                    if exchange in pairs_updated and ((t_in, t_out) in pairs_updated[exchange] or (t_out, t_in) in pairs_updated[exchange]):
                        had_balancer_update = True
                        l.debug(f'Saw a balancer update for exchange')
                        break
                
                if had_balancer_update:
                    # this died
                    dead_campaigns.append(campaign._replace(end_block = block_number - 1))

        l.debug(f'Have {len(dead_campaigns)} campaigns that died just now')
        for campaign in dead_campaigns:
            del active_campaigns[(tuple(campaign.exchanges), tuple(campaign.directions))]
            curr.execute(
                'DELETE FROM active_campaigns WHERE exchanges = %s AND directions = %s',
                (
                    [bytes.fromhex(x[2:]) for x in campaign.exchanges],
                    [bytes.fromhex(x[2:]) for x, _ in campaign.directions],
                )
            )
            assert curr.rowcount == 1
            campaign = campaign._replace(terminated = True)
            completed_campaigns.append(campaign)

        l.info(f'Have {len(active_campaigns)} top arbitrage campaigns, {len(completed_campaigns)} that terminated in block {block_number}')

        # commit details of failed relays
        psycopg2.extras.execute_batch(
            curr,
            '''
            INSERT INTO top_candidate_arbitrage_relay_results (reservation_id, candidate_arbitrage_id, shoot_success, failure_reason)
            VALUES (%s, %s, false, %s)
            ''',
            [(reservation_id, a, b) for a, b in failed_relays],
        )

        if block_number >= reservation_end_block + 100 and len(active_campaigns) > 0:
            l.warning(f'Force-closing {len(active_campaigns):,} active campaigns at 100 blocks beyond end of reservation ({block_number:,})')
            # we're at the end here, force-close everything active and mark as non-terminated
            for campaign in active_campaigns.values():
                assert campaign.terminated == False
                completed_campaigns.append(campaign)
            
            active_campaigns.clear()
            curr.execute('TRUNCATE TABLE active_campaigns')


        # get an ID for all fee objects
        fee_key_to_assigned_fee_ids = {}
        fee_to_id = {}
        for completed_campaign in completed_campaigns:
            for _, member in completed_campaign.members:
                for fee in member.token_fees_used:
                    fee_key = fee._replace(updated_on = None)
                    if fee_key in fee_key_to_assigned_fee_ids:
                        fee_to_id[fee] = fee_key_to_assigned_fee_ids[fee_key]
                    else:
                        curr.execute(
                            '''
                            INSERT INTO inferred_token_fee_on_transfer
                            (token_id, fee, round_down, from_address, to_address, block_number_inferred, updated_on)
                            SELECT id, %(fee)s, %(round_down)s, %(from_address)s, %(to_address)s, %(block_number)s, now()::timestamp
                            FROM tokens
                            WHERE address = %(token_address)s
                            RETURNING id
                            ''',
                            {
                                'fee':           fee.fee,
                                'round_down':    fee.round_down,
                                'from_address':  bytes.fromhex(fee.from_address[2:]),
                                'to_address':    bytes.fromhex(fee.to_address[2:]),
                                'block_number':  block_number,
                                'token_address': bytes.fromhex(fee.token[2:])
                            }
                        )
                        assert curr.rowcount == 1
                        (fee_id,) = curr.fetchone()
                        fee_to_id[fee] = fee_id
                        fee_key_to_assigned_fee_ids[fee_key] = fee_id

        # commit details of completed campaigns
        for completed_campaign in completed_campaigns:
            # get a campaign id
            curr.execute(
                '''
                INSERT INTO top_candidate_arbitrage_campaigns (reservation_id, exchanges, directions, start_block, end_block, terminated)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                ''',
                (
                    reservation_id,
                    [bytes.fromhex(x[2:]) for x in completed_campaign.exchanges],
                    [bytes.fromhex(x[2:]) for x, _ in completed_campaign.directions],
                    completed_campaign.start_block,
                    completed_campaign.end_block,
                    completed_campaign.terminated
                )
            )
            assert curr.rowcount == 1
            (campaign_id,) = curr.fetchone()

            # insert each of the members
            for candidate_id, relay_details in completed_campaign.members:
                curr.execute(
                    '''
                    INSERT INTO top_candidate_arbitrage_relay_results
                    (campaign_id, reservation_id, candidate_arbitrage_id, shoot_success, gas_used, had_fee_on_xfer_token, real_profit_before_fee)
                    VALUES (%(campaign_id)s, %(reservation_id)s, %(candidate_id)s, true, %(gas_used)s, %(had_fee)s, %(real_profit)s)
                    RETURNING id
                    ''',
                    {
                        'campaign_id': campaign_id,
                        'reservation_id': reservation_id,
                        'candidate_id': candidate_id,
                        'gas_used': relay_details.gas,
                        'had_fee': len(relay_details.token_fees_used) > 0,
                        'real_profit': relay_details.profit_no_fee
                    }
                )
                assert curr.rowcount == 1
                (result_id,) = curr.fetchone()

                psycopg2.extras.execute_batch(
                    curr,
                    '''
                    INSERT INTO top_candidate_arbitrage_relay_results_used_fees (top_candidate_arbitrage_relay_result_id, fee_used)
                    VALUES (%s, %s)
                    ''',
                    [(result_id, fee_to_id[fee]) for fee in relay_details.token_fees_used]
                )

        # update progress
        curr.execute(
            'UPDATE top_candidate_arbitrage_reservations SET progress = %s, heartbeat = now()::timestamp WHERE id = %s',
            (block_number, reservation_id)
        )
        if not DEBUG: curr.connection.commit()

    # work done, close rolling window
    while len(rolling_window) > 0:
        pop_rolling_window()


def relay_top_candidate(
        w3: web3.Web3,
        curr: psycopg2.extensions.cursor,
        account: LocalAccount,
        relayer_address: str,
        timestamp_to_use: int,
        w3_ganaches: typing.List[typing.Tuple[int, web3.Web3]],
        candidate: CandidateArbitrage,
        fa: FoundArbitrage,
        maybe_campaign: typing.Optional[TopArbCampaign],
        worker_id: int,
    ) -> typing.Union[TopArbCampaign, str]:
    l.debug(f'block is {w3_ganaches[10][1].eth.block_number}')
    fee_calculator = InferredTokenTransferFeeCalculator()

    assert w3_ganaches[10][1].eth.block_number > candidate.block_number
    assert w3_ganaches[10][0] == candidate.block_number, f'incorrect block number in ganaches list'

    # attempt relay
    try:
        result = auto_adapt_attempt_shoot_candidate(
            w3_ganaches[10][1],
            account,
            relayer_address,
            fa,
            fee_calculator,
            timestamp=timestamp_to_use,
            must_recompute = False,
        )
    except NotEnoughLiquidityException:
        l.critical(f'Problem with relaying ... trying with recompute on')
        result = auto_adapt_attempt_shoot_candidate(
            w3_ganaches[10][1],
            account,
            relayer_address,
            fa,
            fee_calculator,
            timestamp=timestamp_to_use,
            must_recompute = True,
        )

    if not isinstance(result, AutoAdaptShootSuccess):
        # failed, return None indicating that campaign is over
        if isinstance(result, DiagnosisBrokenToken):
            assert result.token_address not in KNOWN_TOKENS, f'Token {result.token_address} should not be banned ever'
            l.debug(f'Candidate {candidate.id_} used a broken token: {result.token_address}')
            return f'Broken token: {result.token_address}'
        elif isinstance(result, DiagnosisBadExchange):
            l.debug(f'Candidate {candidate.id_} used a bad exchange: {result.exchange}')
            return f'Bad exchange: {result.exchange}'
        elif isinstance(result, DiagnosisOther):
            l.debug(f'Candidate {candidate.id_} had undiagnosed failure')
            return f'Other failure'
        elif isinstance(result, DiagnosisNoArbitrageOnFeeApplied):
            l.debug(f'Candidate {candidate.id_} had no arbitrage after applying fee')
            return f'No arbitrage after fee'
        elif isinstance(result, DiagnosisExchangeInterference):
            l.debug(f'Candidate {candidate.id_} could not be relayed because of exchange interference')
            return f'token-exchange interference'
        elif isinstance(result, DiagnosisIncompatibleToken):
            l.debug(f'Candidate {candidate.id_} used an incompatible token: {result.token_address}')
            return f'incompatible token {result.token_address}'
        else:
            raise Exception(f'Not sure what this is: {result}')
    else:
        # success
        l.debug(f'Candidate {candidate.id_} relay success')
        if maybe_campaign is not None:
            # campaign is ongonig, simply append and return
            return maybe_campaign._replace(
                members = maybe_campaign.members + [(candidate.id_, result)],
                end_block = candidate.block_number
            )
        # campaign is not ongoing, we need to reach backward
        l.debug(f'Reaching backward!')

        in_progress_campaign = TopArbCampaign(
            start_block = candidate.block_number,
            end_block = candidate.block_number,
            members = [(candidate.id_, result)],
            exchanges = candidate.exchanges,
            directions = candidate.directions,
            terminated = False
        )

        last_backward_block = candidate.block_number
        while True:
            curr.execute(
                '''
                SELECT ca.id, ca.block_number, ca.amount_in, ca.profit_no_fee
                FROM (
                    SELECT block_number
                    FROM exchanges_updated_in_block
                    WHERE exchange_address = ANY(%(exchanges)s) AND block_number < %(block_number)s
                    ORDER BY block_number DESC
                    LIMIT 1
                ) eub
                LEFT JOIN candidate_arbitrages ca
                    ON ca.block_number = eub.block_number AND
                       ca.exchanges = %(exchanges)s AND
                       ca.directions = %(directions)s
                LIMIT 1
                ''',
                {
                    'exchanges': [bytes.fromhex(x[2:]) for x in candidate.exchanges],
                    'directions': [bytes.fromhex(x[2:]) for x, _ in candidate.directions],
                    'block_number': last_backward_block
                }
            )
            older_id, older_block_number, older_amount_in, older_profit = curr.fetchone()

            if older_id is None:
                # nothing older available
                return in_progress_campaign

            older_amount_in = int(older_amount_in)
            older_profit = int(older_profit)

            blocks_ago = candidate.block_number - older_block_number
            l.debug(f'Found prior candidate at {older_block_number:,} ({blocks_ago:,} behind) makes {older_profit / (10 ** 18):.4f} ETH')

            if blocks_ago > 1_000:
                l.critical(f'Reached back too far ({blocks_ago:,} blocks), ending here')
                return in_progress_campaign


            proc = None
            tmpd = None

            try:
                # find the ganache service if open
                if blocks_ago <= 10:
                    proc = None
                    tmpd = None
                    w3_ganache_past = w3_ganaches[10 - blocks_ago][1]
                    assert w3_ganaches[10 - blocks_ago][0] == older_block_number
                else:
                    tmpd = tempfile.TemporaryDirectory(dir='/mnt/goldphish/tmp')
                    proc, w3_ganache_past, _, _ = open_ganache(older_block_number, tmpd.name, worker_id * 2 + 1)

                older_fa = FoundArbitrage(
                    amount_in = older_amount_in,
                    circuit = [load_pricer_for(w3_ganache_past, curr, x) for x in candidate.exchanges],
                    directions = fa.directions,
                    pivot_token = fa.pivot_token,
                    profit = older_profit,
                )
                older_timestamp = get_block_timestamp(w3, older_block_number + 1)

                # attempt relay
                l.debug(f'at block {w3_ganache_past.eth.block_number}')
                try:
                    older_result = auto_adapt_attempt_shoot_candidate(
                        w3_ganache_past,
                        account,
                        relayer_address,
                        older_fa,
                        fee_calculator,
                        timestamp=older_timestamp,
                        must_recompute = len(result.token_fees_used) > 0,
                    )
                except NotEnoughLiquidityException:
                    l.critical(f'Problem with relaying ... trying with recompute on')
                    older_result = auto_adapt_attempt_shoot_candidate(
                        w3_ganaches[10][1],
                        account,
                        relayer_address,
                        fa,
                        fee_calculator,
                        timestamp=older_timestamp,
                        must_recompute = True,
                    )
            finally:
                if proc is not None:
                    proc.kill()
                    proc.wait()
                    assert proc.poll() is not None, f'proc must have completed'
                    tmpd.cleanup()

            if not isinstance(older_result, AutoAdaptShootSuccess):
                # failed, campaign starts there
                l.debug(F'Prior arbitrage id={older_id} failed')
                return in_progress_campaign
            else:
                l.debug(f'Prior arbitrage id={older_id} success')
                in_progress_campaign = in_progress_campaign._replace(members = [(older_id, older_result)] + in_progress_campaign.members)
                in_progress_campaign = in_progress_campaign._replace(start_block = older_block_number)
                last_backward_block = older_block_number


def dedupe_top_arbs(curr: psycopg2.extensions.cursor):
    curr.execute(
        'SELECT COUNT(*) FROM top_candidate_arbitrage_campaigns'
    )
    (n_top_campaigns,) = curr.fetchone()


    curr.execute(
        '''
        SELECT tcac1.id, tcac1.start_block, tcac1.end_block, tcac2.id, tcac2.start_block, tcac2.end_block
        FROM top_candidate_arbitrage_campaigns tcac1
        JOIN top_candidate_arbitrage_campaigns tcac2 ON
            tcac1.id < tcac2.id AND
            tcac1.exchanges = tcac2.exchanges AND
            tcac1.directions = tcac2.directions AND
            int8range(tcac1.start_block, tcac1.end_block) && int8range(tcac2.start_block, tcac2.end_block)
        '''
    )
    l.info(f'Have {curr.rowcount:,} conflicting campaigns ({curr.rowcount / n_top_campaigns * 100:.2f}%), de-conflicting...')

    # build a conflict graph
    g = nx.Graph()

    for id1, start_block1, end_block1, id2, start_block2, end_block2 in curr:
        if id1 not in g.nodes:
            g.add_node(id1, start_block=start_block1, end_block=end_block1)
        if id2 not in g.nodes:
            g.add_node(id2, start_block=start_block2, end_block=end_block2)

        g.add_edge(id1, id2)

    components: typing.Set[int] = list(nx.connected_components(g))
    l.info(f'Have {len(components)} conflict clusters')

    to_remove = set()
    to_keep = set()
    for c in components:
        # use the one with the longest duration, idk
        longest_dur = -1
        longest_dur_id = None
        for id_ in c:
            duration = g.nodes[id_]['end_block'] - g.nodes[id_]['start_block'] + 1
            assert duration > 0
            if duration > longest_dur:
                longest_dur = duration
                longest_dur_id = id_

        to_remove.update(set(c).difference([id_,]))
        to_keep.add(id_)

    assert len(to_keep.intersection(to_remove)) == 0

    l.info(f'Removing {len(to_remove):,} campaigns...')

    curr.execute(
        '''
        UPDATE top_candidate_arbitrage_campaigns SET removed = true WHERE id = ANY (%s)
        ''',
        (list(to_remove),)
    )
    curr.connection.commit()

    exit()


def load_pricer_for(
        w3: web3.Web3,
        curr: psycopg2.extensions.cursor,
        exchange: str,
    ) -> typing.Optional[pricers.BaseExchangePricer]:
    with profile('load_pricer_for'):
        bexchange = bytes.fromhex(exchange[2:])

        curr.execute(
            '''
            SELECT t0.address, t1.address
            FROM uniswap_v2_exchanges uv2
            JOIN tokens t0 ON uv2.token0_id = t0.id
            JOIN tokens t1 ON uv2.token1_id = t1.id
            WHERE uv2.address = %s
            ''',
            (bexchange,)
        )
        if curr.rowcount > 0:
            assert curr.rowcount == 1

            token0, token1 = curr.fetchone()
            token0 = w3.toChecksumAddress(token0.tobytes())
            token1 = w3.toChecksumAddress(token1.tobytes())
            p = UniswapV2Pricer(w3, exchange, token0, token1)
            return p

        curr.execute(
            '''
            SELECT t0.address, t1.address
            FROM sushiv2_swap_exchanges sv2
            JOIN tokens t0 ON sv2.token0_id = t0.id
            JOIN tokens t1 ON sv2.token1_id = t1.id
            WHERE sv2.address = %s
            ''',
            (bexchange,)
        )
        if curr.rowcount > 0:
            assert curr.rowcount == 1

            token0, token1 = curr.fetchone()
            token0 = w3.toChecksumAddress(token0.tobytes())
            token1 = w3.toChecksumAddress(token1.tobytes())
            p = UniswapV2Pricer(w3, exchange, token0, token1)
            return p

        curr.execute(
            '''
            SELECT t0.address, t1.address
            FROM shibaswap_exchanges ss
            JOIN tokens t0 ON ss.token0_id = t0.id
            JOIN tokens t1 ON ss.token1_id = t1.id
            WHERE ss.address = %s
            ''',
            (bexchange,)
        )
        if curr.rowcount > 0:
            assert curr.rowcount == 1

            token0, token1 = curr.fetchone()
            token0 = w3.toChecksumAddress(token0.tobytes())
            token1 = w3.toChecksumAddress(token1.tobytes())
            p = UniswapV2Pricer(w3, exchange, token0, token1)
            return p

        curr.execute(
            '''
            SELECT t0.address, t1.address, originalfee
            FROM uniswap_v3_exchanges uv3
            JOIN tokens t0 ON uv3.token0_id = t0.id
            JOIN tokens t1 ON uv3.token1_id = t1.id
            WHERE uv3.address = %s            
            ''',
            (bexchange,)
        )
        if curr.rowcount > 0:
            assert curr.rowcount == 1
            token0, token1, fee = curr.fetchone()
            token0 = w3.toChecksumAddress(token0.tobytes())
            token1 = w3.toChecksumAddress(token1.tobytes())
            p = UniswapV3Pricer(w3, exchange, token0, token1, fee)
            return p

        curr.execute(
            '''
            SELECT EXISTS(SELECT 1 FROM balancer_exchanges WHERE address = %s)
            ''',
            (bexchange,)
        )
        (is_balancerv1,) = curr.fetchone()
        if is_balancerv1:
            p = BalancerPricer(w3, exchange)
            return p

        curr.execute(
            '''
            SELECT pool_id, pool_type
            FROM balancer_v2_exchanges
            WHERE address = %s
            ''',
            (bexchange,)
        )
        if curr.rowcount > 0:
            assert curr.rowcount == 1
            pool_id, pool_type = curr.fetchone()
            pool_id = pool_id.tobytes()

            vault = w3.eth.contract(
                address = BALANCER_VAULT_ADDRESS,
                abi = get_abi('balancer_v2/Vault.json'),
            )

            if pool_type in ['WeightedPool', 'WeightedPool2Tokens']:
                return BalancerV2WeightedPoolPricer(w3, vault, exchange, pool_id)
            elif pool_type in ['LiquidityBootstrappingPool', 'NoProtocolFeeLiquidityBootstrappingPool']:
                return BalancerV2LiquidityBootstrappingPoolPricer(w3, vault, exchange, pool_id)

        l.error(f'Could not find exchange for address {exchange}')
    return None

class TokenFee(typing.NamedTuple):
    id_: typing.Optional[int]
    token: str
    from_address: str
    to_address: str
    fee: decimal.Decimal
    round_down: bool
    block_number: int
    updated_on: datetime.datetime

class InferredTokenTransferFeeCalculator(BuiltinFeeTransferCalculator):

    last_updated: datetime.datetime
    proposed_mover_to_token_fees: typing.Dict[typing.Tuple[str, str, str], TokenFee]
    mover_to_token_fees: typing.Dict[typing.Tuple[str, str, str], TokenFee]
    tokens_with_fee: typing.Set[str]
    proposed_tokens_with_fee: typing.Set[str]
    alias_map: typing.Dict[str, str]
    relayed_by_shooter: typing.Dict[typing.Tuple[str, str], str]

    updated: typing.List[TokenFee]


    def __init__(self) -> None:
        super().__init__()

        self.proposed_mover_to_token_fees = {}
        self.mover_to_token_fees = {}
        self.updated = []
        self.last_updated = datetime.datetime(year=1990, month=1, day=1, tzinfo=datetime.timezone.utc)
        self.tokens_with_fee = set()
        self.proposed_tokens_with_fee = set()
        self.alias_map = {}
        self.relayed_by_shooter = {}

    def clear_proposals(self):
        self.proposed_mover_to_token_fees.clear()
    
    def alias(self, address: str, as_: str):
        """
        Alias a given address as a `as_` -- computing all fees to/from it as if it goes to/from `as_`
        (useful only for balancer v2 really, since it uses a centralized vault)
        """
        self.alias_map[address] = as_

    def mark_requires_relay(self, from_: str, to: str, relayer: str):
        """
        Any transfers from `from_` to `to` will subtract a fee equivalent to being sent to `checkpointer`
        and then relayed back out
        """
        self.relayed_by_shooter[(from_, to)] = relayer

    def infer_relays_and_aliases(self, fa: FoundArbitrage, shooter_address: str):
        # first, add balacer vault aliases to records
        for p in fa.circuit:
            if isinstance(p, (BalancerV2LiquidityBootstrappingPoolPricer, BalancerV2WeightedPoolPricer)):
                self.alias(p.address, BALANCER_VAULT_ADDRESS)
        
        for p1, p2 in zip(fa.circuit, fa.circuit[1:]):
            # anything going _into_ Balancer (V1 or V2) must be relayed
            if isinstance(p2, (BalancerPricer, BalancerV2LiquidityBootstrappingPoolPricer, BalancerV2WeightedPoolPricer)):
                self.mark_requires_relay(p1.address, p2.address, shooter_address)

            # anything coming _out_ of Balancer V1 must be relayed
            if isinstance(p1, BalancerPricer):
                self.mark_requires_relay(p1.address, p2.address, shooter_address)

    def get_fees_used(self, fa: FoundArbitrage) -> typing.List[TokenFee]:
        ret = []

        for p1, p2, token in zip(fa.circuit, fa.circuit[1:], [x for _, x in fa.directions[:-1]]):
            from_ = p1.address
            to_ = p2.address

            from_ = self.alias_map.get(from_, from_)
            to_   = self.alias_map.get(to_, to_)

            maybe_relayer = self.relayed_by_shooter.get((from_, to_), None)

            from_ = self.alias_map.get(from_, from_)
            to_   = self.alias_map.get(to_, to_)

            if maybe_relayer:
                maybe_intermediate_fee = self._fee_for(token, from_, maybe_relayer)
                maybe_out_fee = self._fee_for(token, maybe_relayer, to_)

                if maybe_intermediate_fee is not None:
                    ret.append(maybe_intermediate_fee)
                if maybe_out_fee is not None:
                    ret.append(maybe_out_fee)
            else:
                maybe_fee = self._fee_for(token, from_, to_)
                if maybe_fee:
                    ret.append(maybe_fee)
        
        return ret

    def propose(self, token_address: str, from_address: str, to_address: str, fee: decimal.Decimal, round_down: bool):
        t = TokenFee(
            id_ = None,
            token = token_address,
            from_address = from_address,
            to_address = to_address,
            fee = fee,
            round_down = round_down,
            block_number = None,
            updated_on = datetime.datetime.utcnow(),
        )
        self.proposed_tokens_with_fee.add(token_address)
        self.proposed_mover_to_token_fees[(token_address, from_address, to_address)] = t

    def sync(self, curr: psycopg2.extensions.cursor, suggested_block_number: int):
        """
        Synchronize state of inferred transfer fee.
        """

        t_start = time.time()
        # fresh, full-pull
        l.debug(f'Syncing to block {suggested_block_number:,}')
        if (datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc) - self.last_updated).days > 100:
            # initial update
            curr.execute(
                '''
                WITH all_fees as (
                    SELECT DISTINCT token_id, from_address, to_address
                    FROM inferred_token_fee_on_transfer
                ),
                closest_fees as (
                    SELECT
                    (
                        SELECT MAX(block_number_inferred)
                        FROM inferred_token_fee_on_transfer f
                        WHERE f.token_id = af.token_id AND f.from_address = af.from_address AND f.to_address = af.to_address AND block_number_inferred <= %(block_number)s
                    ) closest_lower_block,
                    (
                        SELECT MIN(block_number_inferred)
                        FROM inferred_token_fee_on_transfer f
                        WHERE f.token_id = af.token_id AND f.from_address = af.from_address AND f.to_address = af.to_address AND block_number_inferred >= %(block_number)s
                    ) closest_upper_block,
                    *
                    FROM all_fees af
                ),
                closest_block as (
                    SELECT
                        CASE
                            WHEN cf.closest_lower_block IS NULL THEN cf.closest_upper_block
                            WHEN cf.closest_upper_block IS NULL THEN cf.closest_lower_block
                            WHEN (cf.closest_upper_block - %(block_number)s) < (%(block_number)s - cf.closest_lower_block) THEN cf.closest_upper_block
                            ELSE cf.closest_lower_block
                        END AS closest_block,
                        cf.token_id,
                        cf.from_address,
                        cf.to_address
                    FROM closest_fees cf
                )
                SELECT inf.id, t.address, fee, round_down, inf.from_address, inf.to_address, block_number_inferred, updated_on
                FROM closest_block cb
                JOIN inferred_token_fee_on_transfer inf ON cb.closest_block = inf.block_number_inferred
                JOIN tokens t ON t.id = inf.token_id
                WHERE updated_on > %(last_updated)s
                ''',
                {
                    'block_number': suggested_block_number,
                    'last_updated': self.last_updated,
                }
            )
        else:
            curr.execute(
                '''
                SELECT inf.id, t.address, fee, round_down, from_address, to_address, block_number_inferred, updated_on
                FROM inferred_token_fee_on_transfer inf
                JOIN tokens t ON t.id = inf.token_id
                WHERE updated_on > %s
                ''',
                (self.last_updated,)
            )
        l.debug(f'Loading {curr.rowcount} rows from sync')
        for id_, baddr, fee, round_down, bfrom, bto, block_number, updated_on in curr:
            t = TokenFee(
                id_          = id_,
                token        = web3.Web3.toChecksumAddress(baddr.tobytes()),
                from_address = web3.Web3.toChecksumAddress(bfrom.tobytes()),
                to_address   = web3.Web3.toChecksumAddress(bto.tobytes()),
                block_number = block_number,
                updated_on   = updated_on,
                fee          = fee,
                round_down   = round_down,
            )

            k = (t.token, t.from_address, t.to_address)
            existing_val = self.mover_to_token_fees.get(k, None)
            if existing_val is not None:
                # conflict resolution
                should_update = abs(suggested_block_number - existing_val.block_number) > abs(suggested_block_number - t.block_number)
                if should_update:
                    self.mover_to_token_fees[k] = t

            self.tokens_with_fee.add(t.token)

        l.debug(f'Loaded {len(self.mover_to_token_fees):,} token fee records')

        # dump updated tokens
        for t in self.updated:
            curr.execute(
                '''
                INSERT INTO inferred_token_fee_on_transfer (token_id, fee, round_down, from_address, to_address, block_number_inferred, updated_on)
                SELECT t.id, %(fee)s, %(round_down)s %(from_addr)s, %(to_addr)s, %(block_number)s, now()::timestamp
                FROM tokens WHERE address = %(token_addr)s
                ''',
                {
                    'fee':          t.fee,
                    'round_down':   t.round_down,
                    'from_addr':    bytes.fromhex(t.from_address[2:]),
                    'to_addr':      bytes.fromhex(t.to_address[2:]),
                    'block_number': t.block_number,
                    'token_addr':   bytes.fromhex(t.token[2:]),
                }
            )
            assert curr.rowcount == 1
        
        l.debug(f'Inserted {len(self.updated)} inferred token fee records')
        self.updated.clear()

        elapsed = time.time() - t_start
        inc_measurement('inferred_fee.sync', elapsed)

        self.last_updated = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)

    def out_from_transfer(self, token: str, from_: str, to_: str, amount: int) -> int:
        maybe_relayer = self.relayed_by_shooter.get((from_, to_), None)

        from_ = self.alias_map.get(from_, from_)
        to_   = self.alias_map.get(to_, to_)

        if maybe_relayer:
            intermediate_out = self._out_from_transfer(token, from_, maybe_relayer, amount)
            return self._out_from_transfer(token, maybe_relayer, to_, intermediate_out)
        else:
            return self._out_from_transfer(token, from_, to_, amount)

    def _out_from_transfer(self, token: str, from_: str, to_: str, amount: int) -> int:
        maybe_t = self._fee_for(token, from_, to_)
        if maybe_t is not None:
            if maybe_t.round_down:
                return int(amount * maybe_t.fee)
            else:
                return math.ceil(amount * maybe_t.fee)

        return super().out_from_transfer(token, from_, to_, amount)

    def _fee_for(self, token: str, from_: str, to_: str) -> typing.Optional[TokenFee]:
        k = (token, from_, to_)
        maybe_t = self.proposed_mover_to_token_fees.get(k, None)
        if maybe_t is not None:
            return maybe_t

        maybe_t = self.mover_to_token_fees.get((token, from_, to_), None)
        if  maybe_t is not None:
            return maybe_t

        return None

    def has_fee(self, token: str) -> bool:
        """
        Returns True if this token has fees, either committed or proposed
        """
        return token in self.tokens_with_fee or token in self.proposed_tokens_with_fee

