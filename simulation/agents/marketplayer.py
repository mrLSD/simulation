from typing import Set, Tuple, Callable
from collections import namedtuple
from decimal import Decimal as Dec

from mesa import Agent

import model
import orderbook as ob
from managers import HavvenManager as hm

Portfolio: 'namedtuple' = namedtuple(
    "Portfolio", ["fiat", "escrowed_curits", "curits", "nomins", "issued_nomins"])


class MarketPlayer(Agent):
    """
    A generic agent with a fixed initial wealth in fiat,
      with which it must buy into the market.
    The agent may escrow curits in order to issue nomins,
      and use various strategies in order to trade in the marketplace.
      Its aim is to increase its own wealth.
    """

    def __init__(self, unique_id: int, havven: "model.Havven",
                 fiat: Dec = Dec(0), curits: Dec = Dec(0),
                 nomins: Dec = Dec(0)) -> None:
        super().__init__(unique_id, havven)
        self.fiat: Dec = Dec(fiat)
        self.curits: Dec = Dec(curits)
        self.nomins: Dec = Dec(nomins)
        self.escrowed_curits: Dec = Dec('0.0')
        self.issued_nomins: Dec = Dec('0.0')

        # values that are currently used in orders
        self.used_fiat: "Dec" = Dec('0')
        self.used_curits: "Dec" = Dec('0')
        self.used_nomins: "Dec" = Dec('0')

        self.initial_wealth: "Dec" = self.wealth()

        self.orders: Set["ob.LimitOrder"] = set()

    def __str__(self) -> str:
        return self.name

    @property
    def name(self) -> str:
        """Return the name of this object; its type and its unique id."""
        return f"{self.__class__.__name__} {self.unique_id}"

    def _fraction_(self, qty: Dec, divisor: Dec = Dec('3'),
                   minimum: Dec = Dec('1')) -> Dec:
        """
        Return a fraction of the given quantity, with a minimum.
        Used for depleting reserves gradually.
        """
        return max(qty / divisor, min(minimum, qty))

    def cancel_orders(self) -> None:
        """
        Cancel all of this agent's orders.
        """
        for order in list(self.orders):
            order.cancel()

    def wealth(self) -> Dec:
        """Return the total wealth of this agent at current fiat prices."""
        return self.model.fiat_value(curits=(self.curits + self.escrowed_curits),
                                     nomins=(self.nomins - self.issued_nomins),
                                     fiat=self.fiat)

    def portfolio(self, fiat_values: bool = False
                  ) -> "Portfolio":
        """
        Return the parts of the agent that dictate its wealth.
        If fiat_value is True, then return the equivalent fiat values at the going market rates.
        """

        fiat = self.fiat
        curits = self.curits
        escrowed_curits = self.escrowed_curits
        nomins = self.nomins
        issued_nomins = self.issued_nomins

        if fiat_values:
            v_f = self.model.fiat_value
            curits = v_f(curits=curits)
            escrowed_curits = v_f(curits=escrowed_curits)
            nomins = v_f(nomins=nomins)
            issued_nomins = v_f(nomins=issued_nomins)

        return Portfolio(fiat=fiat, curits=curits, escrowed_curits=escrowed_curits,
                         nomins=nomins, issued_nomins=issued_nomins)

    def reset_initial_wealth(self) -> Dec:
        """Reset this agent's initial wealth to the current wealth, returning the old value."""
        old = self.initial_wealth
        self.initial_wealth = self.wealth()
        return old

    def profit(self) -> Dec:
        """
        Return the total profit accrued over the initial wealth.
        May be negative.
        """
        return self.wealth() - self.initial_wealth

    def profit_fraction(self) -> Dec:
        """
        Return profit accrued as a fraction of initial wealth.
        May be negative.
        """
        if hm.round_decimal(self.initial_wealth) != 0:
            return self.profit() / self.initial_wealth
        else:
            return Dec('0')

    def transfer_fiat_to(self, recipient: "MarketPlayer",
                         value: Dec) -> bool:
        """
        Transfer a positive value of fiat to the recipient,
        if balance is sufficient. Return True on success.
        """
        return self.model.market_manager.transfer_fiat(self, recipient, value)

    def transfer_curits_to(self, recipient: "MarketPlayer",
                           value: Dec) -> bool:
        """
        Transfer a positive value of curits to the recipient,
        if balance is sufficient. Return True on success.
        """
        return self.model.market_manager.transfer_curits(self, recipient, value)

    def transfer_nomins_to(self, recipient: "MarketPlayer",
                           value: Dec) -> bool:
        """
        Transfer a positive value of nomins to the recipient,
        if balance is sufficient. Return True on success.
        """
        return self.model.market_manager.transfer_nomins(self, recipient, value)

    def escrow_curits(self, value: Dec) -> bool:
        """
        Escrow a positive value of curits in order to be able to issue
        nomins against them.
        """
        return self.model.mint.escrow_curits(self, value)

    def unescrow_curits(self, value: Dec) -> bool:
        """
        Unescrow a quantity of curits, if there are not too many
        issued nomins locking it.
        """
        return self.model.mint.unescrow_curits(self, value)

    def available_escrowed_curits(self) -> Dec:
        """
        Return the quantity of escrowed curits which is not
        locked by issued nomins. May be negative.
        """
        return self.model.mint.available_escrowed_curits(self)

    def unavailable_escrowed_curits(self) -> Dec:
        """
        Return the quantity of locked escrowed curits,
          having had nomins issued against it.
        May be greater than total escrowed curits.
        """
        return self.model.mint.unavailable_escrowed_curits(self)

    def max_issuance_rights(self) -> Dec:
        """
        The total quantity of nomins this agent has a right to issue.
        """
        return self.model.mint.max_issuance_rights(self)

    def remaining_issuance_rights(self) -> Dec:
        """
        Return the remaining quantity of tokens this agent can issued on the back of their
          escrowed curits. May be negative.
        """
        return self.model.mint.remaining_issuance_rights(self)

    def issue_nomins(self, value: Dec) -> bool:
        """
        Issue a positive value of nomins against currently escrowed curits,
          up to the utilisation ratio maximum.
        """
        return self.model.mint.issue_nomins(self, value)

    def burn_nomins(self, value: Dec) -> bool:
        """
        Burn a positive value of issued nomins, which frees up curits.
        """
        return self.model.mint.burn_nomins(self, value)

    def _sell_quoted_(self, book: "ob.OrderBook", quantity: Dec,
                      premium: Dec = Dec('0')) -> "ob.Bid":
        """
        Sell a quantity of the quoted currency into the given market.
        """
        price = book.lowest_ask_price()
        return book.buy(quantity/price, self, premium)

    def _sell_base_(self, book: "ob.OrderBook", quantity: Dec,
                    discount: Dec = Dec('0')) -> "ob.Ask":
        """
        Sell a quantity of the base currency into the given market.
        """
        return book.sell(quantity, self, discount)

    def sell_nomins_for_curits(self, quantity: Dec,
                               premium: Dec = Dec('0')) -> "ob.Bid":
        """
        Sell a quantity of nomins to buy curits.
        """
        return self._sell_quoted_(self.model.market_manager.curit_nomin_market,
                                  quantity, premium)

    def sell_curits_for_nomins(self, quantity: Dec,
                               discount: Dec = Dec('0')) -> "ob.Ask":
        """
        Sell a quantity of curits to buy nomins.
        """
        return self._sell_base_(self.model.market_manager.curit_nomin_market,
                                quantity, discount)

    def sell_fiat_for_curits(self, quantity: Dec,
                             premium: Dec = Dec('0')) -> "ob.Bid":
        """
        Sell a quantity of fiat to buy curits.
        """
        return self._sell_quoted_(self.model.market_manager.curit_fiat_market,
                                  quantity, premium)

    def sell_curits_for_fiat(self, quantity: Dec,
                             discount: Dec = Dec('0')) -> "ob.Ask":
        """
        Sell a quantity of curits to buy fiat.
        """
        return self._sell_base_(self.model.market_manager.curit_fiat_market,
                                quantity, discount)

    def sell_fiat_for_nomins(self, quantity: Dec,
                             premium: Dec = Dec('0')) -> "ob.Bid":
        """
        Sell a quantity of fiat to buy nomins.
        """
        return self._sell_quoted_(self.model.market_manager.nomin_fiat_market,
                                  quantity, premium)

    def sell_nomins_for_fiat(self, quantity: Dec,
                             discount: Dec = Dec('0')) -> "ob.Ask":
        """
        Sell a quantity of nomins to buy fiat.
        """
        return self._sell_base_(self.model.market_manager.nomin_fiat_market,
                                quantity, discount)

    def _sell_quoted_with_fee_(self, received_qty_fn: Callable[[Dec], Dec],
                               book: "ob.OrderBook", quantity: Dec,
                               premium: Dec = Dec('0')) -> "ob.Bid":
        """
        Sell a quantity of the quoted currency into the given market, including the
          fee, as calculated by the provided function.
        """
        price = book.lowest_ask_price()
        return book.buy(received_qty_fn(quantity/price), self, premium)

    def _sell_base_with_fee_(self, received_qty_fn: Callable[[Dec], Dec],
                             book: "ob.OrderBook", quantity: Dec,
                             discount: Dec = Dec('0')) -> "ob.Ask":
        """
        Sell a quantity of the base currency into the given market, including the
          fee, as calculated by the provided function.
        """
        return book.sell(received_qty_fn(quantity), self, discount)

    def sell_nomins_for_curits_with_fee(self, quantity: Dec,
                                        premium: Dec = Dec('0')) -> "ob.Bid":
        """
        Sell a quantity of nomins (including fee) to buy curits.
        """
        return self._sell_quoted_with_fee_(self.model.fee_manager.transferred_nomins_received,
                                           self.model.market_manager.curit_nomin_market,
                                           quantity, premium)

    def sell_curits_for_nomins_with_fee(self, quantity: Dec,
                                        discount: Dec = Dec('0')) -> "ob.Ask":
        """
        Sell a quantity of curits (including fee) to buy nomins.
        """
        return self._sell_base_with_fee_(self.model.fee_manager.transferred_curits_received,
                                         self.model.market_manager.curit_nomin_market,
                                         quantity, discount)

    def sell_fiat_for_curits_with_fee(self, quantity: Dec,
                                      premium: Dec = Dec('0')) -> "ob.Bid":
        """
        Sell a quantity of fiat (including fee) to buy curits.
        """
        return self._sell_quoted_with_fee_(self.model.fee_manager.transferred_fiat_received,
                                           self.model.market_manager.curit_fiat_market,
                                           quantity, premium)

    def sell_curits_for_fiat_with_fee(self, quantity: Dec,
                                      discount: Dec = Dec('0')) -> "ob.Ask":
        """
        Sell a quantity of curits (including fee) to buy fiat.
        """
        return self._sell_base_with_fee_(self.model.fee_manager.transferred_curits_received,
                                         self.model.market_manager.curit_fiat_market,
                                         quantity, discount)

    def sell_fiat_for_nomins_with_fee(self, quantity: Dec,
                                      premium: Dec = Dec('0')) -> "ob.Bid":
        """
        Sell a quantity of fiat (including fee) to buy nomins.
        """
        return self._sell_quoted_with_fee_(self.model.fee_manager.transferred_fiat_received,
                                           self.model.market_manager.nomin_fiat_market,
                                           quantity, premium)

    def sell_nomins_for_fiat_with_fee(self, quantity: Dec,
                                      discount: Dec = Dec('0')) -> "ob.Ask":
        """
        Sell a quantity of nomins (including fee) to buy fiat.
        """
        return self._sell_base_with_fee_(self.model.fee_manager.transferred_nomins_received,
                                         self.model.market_manager.nomin_fiat_market,
                                         quantity, discount)

    def place_curit_fiat_bid(self, quantity: Dec, price: Dec) -> "ob.Bid":
        """
        Place a bid for a quantity of curits, at a price in fiat.
        """
        return self.model.market_manager.curit_fiat_market.bid(price, quantity, self)

    def place_curit_fiat_ask(self, quantity: Dec, price: Dec) -> "ob.Ask":
        """
        Place an ask for fiat with a quantity of curits, at a price in fiat.
        """
        return self.model.market_manager.curit_fiat_market.ask(price, quantity, self)

    def place_nomin_fiat_bid(self, quantity: Dec, price: Dec) -> "ob.Bid":
        """
        Place a bid for a quantity of nomins, at a price in fiat.
        """
        return self.model.market_manager.nomin_fiat_market.bid(price, quantity, self)

    def place_nomin_fiat_ask(self, quantity: Dec, price: Dec) -> "ob.Ask":
        """
        Place an ask for fiat with a quantity of nomins, at a price in fiat.
        """
        return self.model.market_manager.nomin_fiat_market.ask(price, quantity, self)

    def place_curit_nomin_bid(self, quantity: Dec, price: Dec) -> "ob.Bid":
        """
        Place a bid for a quantity of curits, at a price in nomins.
        """
        return self.model.market_manager.curit_nomin_market.bid(price, quantity, self)

    def place_curit_nomin_ask(self, quantity: Dec, price: Dec) -> "ob.Ask":
        """
        Place an ask for nomins with a quantity of curits, at a price in nomins.
        """
        return self.model.market_manager.curit_nomin_market.ask(price, quantity, self)

    def place_curit_fiat_bid_with_fee(self, quantity: Dec, price: Dec) -> "ob.Bid":
        """
        Place a bid for a quantity of curits, at a price in fiat, including the fee.
        """
        # Note, only works because the fee is multiplicative, we're calculating the fee not
        # on the quantity we are actually transferring, which is (quantity*price)
        qty = self.model.fee_manager.transferred_fiat_received(quantity)
        return self.model.market_manager.curit_fiat_market.bid(price, qty, self)

    def place_curit_fiat_ask_with_fee(self, quantity: Dec, price: Dec) -> "ob.Ask":
        """
        Place an ask for fiat with a quantity of curits, including the fee, at a price in fiat.
        """
        qty = self.model.fee_manager.transferred_curits_received(quantity)
        return self.model.market_manager.curit_fiat_market.ask(price, qty, self)

    def place_nomin_fiat_bid_with_fee(self, quantity: Dec, price: Dec) -> "ob.Bid":
        """
        Place a bid for a quantity of nomins, at a price in fiat, including the fee.
        """
        # Note, only works because the fee is multiplicative, we're calculating the fee not
        # on the quantity we are actually transferring, which is (quantity*price)
        qty = self.model.fee_manager.transferred_fiat_received(quantity)
        return self.model.market_manager.nomin_fiat_market.bid(price, qty, self)

    def place_nomin_fiat_ask_with_fee(self, quantity: Dec, price: Dec) -> "ob.Ask":
        """
        Place an ask for fiat with a quantity of nomins, including the fee, at a price in fiat.
        """
        qty = self.model.fee_manager.transferred_nomins_received(quantity)
        return self.model.market_manager.nomin_fiat_market.ask(price, qty, self)

    def place_curit_nomin_bid_with_fee(self, quantity: Dec, price: Dec) -> "ob.Bid":
        """
        Place a bid for a quantity of curits, at a price in nomins, including the fee.
        """
        # Note, only works because the fee is multiplicative, we're calculating the fee not
        # on the quantity we are actually transferring, which is (quantity*price)
        qty = self.model.fee_manager.transferred_nomins_received(quantity)
        return self.model.market_manager.curit_nomin_market.bid(price, qty, self)

    def place_curit_nomin_ask_with_fee(self, quantity: Dec, price: Dec) -> "ob.Ask":
        """
        Place an ask for nomins with a quantity of curits, including the fee, at a price in nomins.
        """
        qty = self.model.fee_manager.transferred_curits_received(quantity)
        return self.model.market_manager.curit_nomin_market.ask(price, qty, self)

    @property
    def available_fiat(self) -> "Dec":
        return self.fiat - self.used_fiat

    @property
    def available_curits(self) -> "Dec":
        return self.curits - self.used_curits

    @property
    def available_nomins(self) -> "Dec":
        return self.nomins - self.used_nomins

    def notify_cancelled(self, order: "ob.LimitOrder") -> None:
        """
        Notify this agent that its order was cancelled.
        """
        pass

    def notify_filled(self, order: "ob.LimitOrder") -> None:
        """
        Notify this agent that its order was filled.
        """
        pass

    def step(self) -> None:
        pass
