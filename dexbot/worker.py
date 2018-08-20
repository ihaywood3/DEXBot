import importlib
import sys
import logging
import os.path
import threading
import copy

import dexbot.errors as errors
from dexbot.basestrategy import BaseStrategy
import dexbot.ui as ui

from bitshares import BitShares
from bitshares.notify import Notify
from bitshares.instance import shared_bitshares_instance

log = logging.getLogger(__name__)
log_workers = logging.getLogger('dexbot.per_worker')
# NOTE this is the  special logger for per-worker events
# it returns LogRecords with extra fields: worker_name, account, market and is_disabled
# is_disabled is a callable returning True if the worker is currently disabled.
# GUIs can add a handler to this logger to get a stream of events of the running workers.


class WorkerInfrastructure(threading.Thread):

    def __init__(
        self,
        config,
        bitshares_instance=None,
        view=None
    ):
        super().__init__()

        # BitShares instance
        self.bitshares = bitshares_instance or shared_bitshares_instance()
        self.config = copy.deepcopy(config)
        self.view = view
        self.jobs = set()
        self.notify = None
        self.config_lock = threading.RLock()
        self.workers = {}

        self.accounts = set()
        self.markets = set()

        # Set the module search path
        user_worker_path = os.path.expanduser("~/bots")
        if os.path.exists(user_worker_path):
            sys.path.append(user_worker_path)

    def init_workers(self, config):
        """ Initialize the workers
        """
        self.config_lock.acquire()
        for worker_name, worker in config["workers"].items():
            self.init_single_worker(worker_name, worker, config)
        self.config_lock.release()

    def init_single_worker(self, workername, worker, config):
        try:
            strategy_class = getattr(
                importlib.import_module(worker["module"]),
                'Strategy'
            )
            if not strategy_class.check_config(config["workers"][workername]):
                return
            self.workers[workername] = strategy_class(
                config=config,
                name=workername,
                bitshares_instance=self.bitshares,
                view=self.view
            )
            self.markets |= self.workers[workername].get_markets()
            self.accounts |= self.workers[workername].get_accounts()
        except BaseException:
            log_workers.exception("Worker initialisation", extra={
                'worker_name': workername, 'account': worker['account'],
                'market': 'unknown', 'is_disabled': (lambda: True)
            })

    def reload_config(self, newconfig):
        """reload the configuration while still running
        """
        self.config_lock.acquire()
        if self.config["node"] != newconfig["node"]:
            self.bitshares = BitShares(
                newconfig["node"],
                num_retries=-1)
            ui.unlock_wallet(self.bitshares)
            new_bitshares = True
        else:
            new_bitshares = False
        newconfig_workers = set(newconfig["workers"].keys())
        oldconfig_workers = set(self.config["workers"].keys())
        self.accounts = set()
        self.markets = set()
        # new workers
        for workername in newconfig_workers - oldconfig_workers:
            self.init_single_worker(workername, newconfig['workers'][workername], newconfig)
        # workers deleted
        for workername in oldconfig_workers - newconfig_workers:
            self.workers[workername].purge()
            del self.workers[workername]
        # workers changed
        for workername in oldconfig_workers & newconfig_workers:
            if newconfig["workers"][workername] != self.config["workers"][workername] or new_bitshares:
                worker = self.workers[workername]
                if new_bitshares:
                    worker.bitshares = self.bitshares
                worker.purge()
                if hasattr(worker, "check_orders"):
                    worker.check_orders()
                else:
                    worker.log.warning("no check_orders() method")
            self.markets |= self.workers[workername].get_markets()
            self.accounts |= self.workers[workername].get_accounts()
        self.config = newconfig
        self.config_lock.release()

    def update_notify(self):
        if not self.config['workers']:
            log.critical("No workers configured to launch, exiting")
            raise errors.NoWorkersAvailable()
        if not self.workers:
            log.critical("No workers actually running")
            raise errors.NoWorkersAvailable()
        if self.notify:
            # Update the notification instance
            self.notify.reset_subscriptions(list(self.accounts), list(self.markets))
        else:
            # Initialize the notification instance
            self.notify = Notify(
                markets=list(self.markets),
                accounts=list(self.accounts),
                on_market=self.on_market,
                on_account=self.on_account,
                on_block=self.on_block,
                bitshares_instance=self.bitshares
            )

    # Events
    def on_block(self, data):
        if self.jobs:
            try:
                for job in self.jobs:
                    job()
            finally:
                self.jobs = set()

        self.config_lock.acquire()
        for worker_name, worker in self.config["workers"].items():
            if worker_name not in self.workers or self.workers[worker_name].disabled:
                continue
            try:
                self.workers[worker_name].ontick(data)
            except Exception as e:
                self.workers[worker_name].log.exception("in ontick()")
                try:
                    self.workers[worker_name].error_ontick(e)
                except Exception:
                    self.workers[worker_name].log.exception("in error_ontick()")
        self.config_lock.release()

    def on_market(self, data):
        if data.get("deleted", False):  # No info available on deleted orders
            return

        self.config_lock.acquire()
        for worker_name, worker in self.config["workers"].items():
            if self.workers[worker_name].disabled:
                self.workers[worker_name].log.debug('Worker "{}" is disabled'.format(worker_name))
                continue
            if data.market in list(self.workers[worker_name].get_markets()):
                try:
                    self.workers[worker_name].onMarketUpdate(data)
                except Exception as e:
                    self.workers[worker_name].log.exception("in onMarketUpdate()")
                    try:
                        self.workers[worker_name].error_onMarketUpdate(e)
                    except Exception:
                        self.workers[worker_name].log.exception("in error_onMarketUpdate()")
        self.config_lock.release()

    def on_account(self, account_update):
        self.config_lock.acquire()
        account = account_update.account
        for worker_name, worker in self.config["workers"].items():
            if self.workers[worker_name].disabled:
                self.workers[worker_name].log.info('Worker "{}" is disabled'.format(worker_name))
                continue
            if account["name"] in self.workers[worker_name].get_accounts():
                try:
                    self.workers[worker_name].onAccount(account_update)
                except Exception as e:
                    self.workers[worker_name].log.exception("in onAccountUpdate()")
                    try:
                        self.workers[worker_name].error_onAccount(e)
                    except Exception:
                        self.workers[worker_name].log.exception("in error_onAccountUpdate()")
        self.config_lock.release()

    def add_worker(self, worker_name, config):
        with self.config_lock:
            self.config['workers'][worker_name] = config['workers'][worker_name]
            self.init_workers(config)
        self.update_notify()

    def run(self):
        self.init_workers(self.config)
        self.update_notify()
        self.notify.listen()

    def stop(self, worker_name=None, pause=False):
        """ Used to stop the worker(s)
            :param str worker_name: name of the worker to stop
            :param bool pause: optional argument which tells worker if it was
                stopped or just paused
        """
        if worker_name and len(self.workers) > 1:
            # Kill only the specified worker
            with self.config_lock:
                self.config['workers'].pop(worker_name)
            if pause:
                self.workers[worker_name].pause()
            self.workers.pop(worker_name, None)
            # re-compute accounts and markets
            self.markets = set()
            self.accounts = set()
            for worker in self.workers.values():
                self.markets |= worker.get_markets()
                self.accounts |= worker.get_accounts()
            self.update_notify()
        else:
            # Kill all of the workers
            if pause:
                for worker in self.workers:
                    self.workers[worker].pause()
            if self.notify:
                self.notify.websocket.close()

    def remove_worker(self, worker_name=None):
        if worker_name:
            self.workers[worker_name].purge()
        else:
            for worker in self.workers:
                self.workers[worker].purge()

    @staticmethod
    def remove_offline_worker(config, worker_name, bitshares_instance):
        # Initialize the base strategy to get control over the data
        strategy = BaseStrategy(worker_name, config, bitshares_instance=bitshares_instance)
        strategy.purge()

    @staticmethod
    def remove_offline_worker_data(worker_name):
        BaseStrategy.purge_worker_data(worker_name)

    def do_next_tick(self, job):
        """ Add a callable to be executed on the next tick """
        self.jobs.add(job)
