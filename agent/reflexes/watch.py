"""Run the cycle on a fixed interval, on its own, until stopped.

An ERP that is down skips one cycle and the loop goes on. A journal that fails verification
stops the loop: the agent must not decide on top of a memory that was tampered with.
"""

import logging
import time
from collections.abc import Callable
from datetime import timedelta

from services.erp import LedgerError

log = logging.getLogger("embco")


def watch(
    cycle: Callable[[], object],
    interval: timedelta,
    *,
    cycles: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run `cycle` every `interval`. Returns how many cycles ran without an ERP error."""
    done = ok = 0
    while cycles is None or done < cycles:
        try:
            cycle()
            ok += 1
        except LedgerError as error:
            log.warning("cycle skipped, will retry next time: %s", error)
        done += 1
        if cycles is None or done < cycles:
            sleep(interval.total_seconds())
    return ok
