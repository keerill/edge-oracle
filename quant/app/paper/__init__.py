"""Paper-trading: no-money validation of the live strategies.

Auto-captures the bets the advisor *would* place (``capture``), then scores them against
real outcomes (settled by the resolution watcher). Lets us prove an edge exists — after
fees, spread, slippage and gas — before a single dollar is risked.
"""
