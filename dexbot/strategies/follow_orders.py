from math import fabs
from pprint import pprint
from collections import Counter
from bitshares.amount import Amount
from bitshares.price import Price, Order, FilledOrder
from dexbot.basestrategy import BaseStrategy, ConfigElement

import pdb
        
class FollowOrders(BaseStrategy):

    @classmethod
    def configure(cls):
        return BaseStrategy.configure()+[
            ConfigElement("spread","float",5,"Percentage difference between buy and sell",(0,1000)),
            ConfigElement("wall","float",0.0,"the default amount to buy/sell, in quote",(0.0,None)),
            ConfigElement("max","float",100.0,"bot will not trade if price above this",(0,0,None)),
            ConfigElement("min","float",100.0,"bot will not trade if price below this",(0,0,None)),
            ConfigElement("start","float",100.0,"Starting price, as percentage of settlement price",(0,0,None)),
            ConfigElement("reset","bool",False,"bot will alwys reset orders on start",(0,0,None)),
        ]


    def safe_dissect(self,thing,name):
        try:
            self.log.info("%s() returned type: %r repr: %r dict: %r" % (name,type(thing),repr(thing),dict(thing)))
        except:
            self.log.info("%s() returned type: %r repr: %r" % (name,type(thing),repr(thing)))


    def add_price(self,p1,p2):
        if not p1: return p2
        return Price(quote=p1['quote']+p2['quote'],base=p1['base']+p2['base'])
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Define Callbacks
        self.onMarketUpdate += self.onmarket
        if self.bot.get("reset",False):
            self.cancelall()
        self.reassess()
                                           
    def updateorders(self,newprice):
        """ Update the orders
        """

        self.log.info("Replacing orders")

        sell_price = newprice * (100+(self.bot['spread']/2))/100.0
        buy_price = newprice * (100-(self.bot['spread']/2))/100.0
        
        # Canceling orders
        self.cancelall()
        myorders = {}

        if newprice < self.bot["min"]:
            self.disabled = True
            self.log.critical("Price %f is below minimum %f" % (newprice,sel.bot["min"]))
            return
        if newprice > self.bot["max"]:
            self.disabled = True
            self.log.critical("Price %f is above maxiimum %f" % (newprice,sel.bot["max"]))
            return
        
        if float(self.balance(self.market["quote"])) < self.bot["wall"]:
            self.log.critical("insufficient sell balance: %r (needed %f)" % (self.balance(self.market["quote"]),self.bot["wall"]))
            self.disabled = True # now we get no more events
            return

        if self.balance(self.market["base"]) < buy_price * self.bot["wall"]:
            self.disabled = True
            self.log.critical("insufficient buy balance: %r (need: %f)" % (self.balance(self.market["base"]),self.bot["wall"]*buy_price))
            return
    
        amt = Amount(self.bot["wall"], self.market["quote"])
        self.log.info("SELL {amt} at {price} {base}/{quote} (= {inv_price} {quote}/{base})".format(
            amt=repr(amt),
            price=sell_price,
            inv_price = 1/sell_price,
            quote=self.market['quote']['symbol'],
            base=self.market['base']['symbol']))
        
        ret = self.market.sell(
                sell_price,
                amt,
                account=self.account,
                returnOrderId="head"
            )
        myorders[ret['orderid']] = sell_price
        self.log.info("BUY {amt} at {price} {base}/{quote} (= {inv_price} {quote}/{base})".format(
            amt=repr(amt),
            price = buy_price,
            inv_price = 1/buy_price,
            quote=self.market['quote']['symbol'],
            base=self.market['base']['symbol']))
        ret = self.market.buy(
                buy_price,
                amt,
                account=self.account,
                    returnOrderId="head",
            )
        myorders[ret['orderid']] = buy_price

        self['myorders'] = myorders
        #ret = self.execute() this doesn't seem to work reliably
        #self.safe_dissect(ret,"execute")

    def onmarket(self, data):
        if type(data) is FilledOrder and data['account_id'] == self.account['id']:
            self.log.info("FilledOrder on our account")
            self.log.info("%r" % dict(data))
            self.reassess()

    def reassess(self):
        # sadly no smart way to match a FilledOrder to an existing order
        # even price-matching won't work as we can buy at a better price than we asked for
        # so look at what's missing
        self.account.refresh()
        still_open = set(i['id'] for i in self.account.openorders)
        if len(still_open) == 0:
            self.log.info("no open orders, recalculating from startprice")
            t = self.market.ticker()
            self.updateorders(float(t['quoteSettlement_price'])*self.bot['start']/100.0)
            return
        missing = set(self['myorders'].keys()) - still_open
        if len(missing) == 2:
            self.log.critical("Aaargh! there are open orders but both of ours have gone. Probably because user is doing other trades with bot's account. Suspening bot.")
            self.disabled = True
            return
        if len(missing) == 1:
            # one surviving order and one missing, use the missing one as new price
            for i in missing:
                self.updateorders(self['myorders'][i])

