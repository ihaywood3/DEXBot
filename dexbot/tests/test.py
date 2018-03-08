#!/usr/bin/python3

from bitshares.bitshares import BitShares
import unittest
import time
import threading
import logging
from dexbot.bot import BotInfrastructure


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)


TEST_CONFIG = {
    'node': 'wss://node.testnet.bitshares.eu',
    'bots': {
        'echo':
        {
            'account': 'aud.bot.test4',
            'market': 'TESTUSD:TEST',
            'module': 'dexbot.strategies.echo'
        },
        'follow_orders':
        {
            'account': 'aud.bot.test4',
            'market': 'TESTUSD:TEST',
            'module': 'dexbot.strategies.follow_orders',
            'spread': 5,
            'reset': True,
            'staggers': 2,
            'wall': 5,
            'staggerspread': 5,
            'min': 0,
            'max': 100000,
            'start': 50
        }}}

KEYS = ['5JV32w3BgPgHV1VoELuDQxvt1gdfuXHo2Rm8TrEn6SQwSsLjnH8']


class TestDexbot(unittest.TestCase):

    def test_dexbot(self):
        bitshares_instance = BitShares(node=TEST_CONFIG['node'], keys=KEYS)
        bot_infrastructure = BotInfrastructure(config=TEST_CONFIG,
                                               bitshares_instance=bitshares_instance)

        def wait_then_stop():
            time.sleep(20)
            bitshares_instance.do_next_tick(bitshares_instance.stop)

        stopper = threading.Thread(target=wait_then_stop)
        stopper.start()
        bot_infrastructure.run()
        stopper.join()


if __name__ == '__main__':
    unittest.main()
