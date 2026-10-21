"""
因为Windows 的 ProactorEventLoop 和 psycopg 冲突，所以统一改用 SelectorEventLoop
"""
import asyncio
import selectors
from collections.abc import Coroutine


def run_async[ResultT](coro: Coroutine) -> ResultT:
    return asyncio.run(coro,
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))