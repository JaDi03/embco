"""Start and stop the agent process of one shop: one systemd unit per shop.

The service user may only start, stop and restart `embco-shop@*` units (a polkit rule set up on
the server). The shop name is a checked contract address, and no shell is involved. The units are
not enabled, so the hub starts them again when it starts.
"""

import logging
import subprocess
from typing import Protocol

from services.shops.store import ShopStore, check_shop

SYSTEMCTL = "/usr/bin/systemctl"

log = logging.getLogger("embco.hub")


class UnitError(Exception):
    pass


class Units(Protocol):
    def start(self, shop: str) -> None: ...

    def ensure(self, shop: str) -> None: ...

    def stop(self, shop: str) -> None: ...


class SystemdUnits:
    def start(self, shop: str) -> None:
        self._systemctl("restart", shop)  # restart: a reconnection picks up the new settings

    def ensure(self, shop: str) -> None:
        self._systemctl("start", shop)  # nothing happens to an agent already running

    def stop(self, shop: str) -> None:
        self._systemctl("stop", shop)

    def _systemctl(self, action: str, shop: str) -> None:
        unit = f"embco-shop@{check_shop(shop)}.service"
        # a fixed program, a fixed action and a checked contract address: no untrusted input
        done = subprocess.run([SYSTEMCTL, action, unit], capture_output=True,  # noqa: S603
                              text=True, timeout=30, check=False)
        if done.returncode != 0:
            raise UnitError(f"systemctl {action} {unit} failed ({done.returncode})")


def start_connected(store: ShopStore, units: Units) -> list[str]:
    """After a reboot nothing else brings the shops' agents back: the hub starts every connected
    shop whose owner left its agent on. One shop that fails does not keep the others off."""
    started = []
    for shop in store.connected():
        if not store.agent_on(shop):
            continue
        try:
            units.ensure(shop)
            started.append(shop)
        except UnitError as error:
            log.warning("%s", error)
    return started
