import sqlalchemy
from sqlalchemy import create_engine, Table, Column, String, Integer, MetaData, DateTime, Float
import os
import json
import threading
import queue
import uuid
import time
import datetime
import re

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from appdirs import user_data_dir
Base = declarative_base()

# For dexbot.sqlite file
appname = "dexbot"
appauthor = "ChainSquad GmbH"
storageDatabase = "dexbot.sqlite"


def mkdir_p(d):
    if os.path.isdir(d):
        return
    else:
        try:
            os.makedirs(d)
        except FileExistsError:
            return
        except OSError:
            raise


class Config(Base):
    __tablename__ = 'config'

    id = Column(Integer, primary_key=True)
    category = Column(String)
    key = Column(String)
    value = Column(String)

    def __init__(self, c, k, v):
        self.category = c
        self.key = k
        self.value = v


class Journal(Base):
    __tablename__ = 'journal'
    id = Column(Integer, primary_key=True)
    category = Column(String)
    key = Column(String)
    amount = Column(Float)
    stamp = Column(DateTime, default=datetime.datetime.now)


class Storage(dict):
    """ Storage class

        :param string category: The category to distinguish
                                different storage namespaces
    """

    def __init__(self, category):
        self.category = category

    def __setitem__(self, key, value):
        worker.execute_noreturn(worker.set_item, self.category, key, value)

    def __getitem__(self, key):
        return worker.execute(worker.get_item, self.category, key)

    def __delitem__(self, key):
        worker.execute_noreturn(worker.del_item, self.category, key)

    def __contains__(self, key):
        return worker.execute(worker.contains, self.category, key)

    def items(self):
        return worker.execute(worker.get_items, self.category)

    def clear(self):
        worker.execute_noreturn(worker.clear, self.category)

    def save_journal(self, amounts):
        worker.execute_noreturn(worker.save_journal, self.category, amounts)

    def query_journal(self, start, end_=None):
        return worker.execute(worker.query_journal, self.category, start, end_)


class DatabaseWorker(threading.Thread):
    """
    Thread safe database worker
    """

    def __init__(self):
        super().__init__()

        # Obtain engine and session
        engine = create_engine('sqlite:///%s' % sqlDataBaseFile, echo=False)
        Session = sessionmaker(bind=engine)
        self.session = Session()
        Base.metadata.create_all(engine)
        self.session.commit()

        self.task_queue = queue.Queue()
        self.results = {}
        self.lock = threading.Lock()
        self.event = threading.Event()
        self.daemon = True
        self.start()

    def run(self):
        for func, args, token in iter(self.task_queue.get, None):
            args = args + (token,)
            func(*args)

    def get_result(self, token):
        while True:
            with self.lock:
                if token in self.results:
                    return_value = self.results[token]
                    del self.results[token]
                    return return_value
                else:
                    self.event.clear()
            self.event.wait()

    def set_result(self, token, result):
        with self.lock:
            self.results[token] = result
            self.event.set()

    def execute(self, func, *args):
        token = str(uuid.uuid4)
        self.task_queue.put((func, args, token))
        return self.get_result(token)

    def execute_noreturn(self, func, *args):
        self.task_queue.put((func, args, None))

    def set_item(self, category, key, value, token):
        value = json.dumps(value)
        e = self.session.query(Config).filter_by(
            category=category,
            key=key
        ).first()
        if e:
            e.value = value
        else:
            e = Config(category, key, value)
            self.session.add(e)
        self.session.commit()

    def get_item(self, category, key, token):
        e = self.session.query(Config).filter_by(
            category=category,
            key=key
        ).first()
        if not e:
            result = None
        else:
            result = json.loads(e.value)
        self.set_result(token, result)

    def del_item(self, category, key, token):
        e = self.session.query(Config).filter_by(
            category=category,
            key=key
        ).first()
        self.session.delete(e)
        self.session.commit()

    def contains(self, category, key, token):
        e = self.session.query(Config).filter_by(
            category=category,
            key=key
        ).first()
        self.set_result(token, bool(e))

    def get_items(self, category, token):
        es = self.session.query(Config).filter_by(
            category=category
        ).all()
        result = [(e.key, e.value) for e in es]
        self.set_result(token, result)

    def clear(self, category, token):
        rows = self.session.query(Config).filter_by(
            category=category
        )
        for row in rows:
            self.session.delete(row)
            self.session.commit()

    def save_journal(self, category, amounts, token):
        now_t = datetime.datetime.now()
        for key, amount in amounts:
            e = Journal(key=key, category=category, amount=amount, stamp=now_t)
            self.session.add(e)
        self.session.commit()

    def query_journal(self, category, start, end_, token):
        """Query this bots journal
        start: datetime of start time
        end_: datetime of end (None means up to now)
        """
        r = self.session.query(Journal).filter(Journal.category == category)
        if isinstance(start, str):
            m = re.match("(\\d+)([dw])", start)
            if m:
                n = int(m.group(1))
                start = datetime.datetime.now()
                if m.group(2) == 'w':
                    n *= 7
                start -= datetime.timedelta(days=n)
        if end_:
            r = r.filter(Journal.stamp > start, Journal.stamp < end_)
        else:
            r = r.filter(Journal.stamp > start)
        self.set_result(token, r.all())


# Derive sqlite file directory
data_dir = user_data_dir(appname, appauthor)
sqlDataBaseFile = os.path.join(data_dir, storageDatabase)

# Create directory for sqlite file
mkdir_p(data_dir)

worker = DatabaseWorker()
