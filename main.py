"""Minimal runnable demo for cordis-py."""

from __future__ import annotations

import asyncio

from cordis_py import Context, Service, inject


class Greeter(Service):
    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx, "greeter")

    def hello(self, name: str) -> str:
        return f"Hello, {name}!"


@inject("greeter")
def greeter_plugin(ctx: Context, config: dict) -> None:
    ctx.on("app/ready", lambda msg: print(ctx.greeter.hello(msg)))


async def main() -> None:
    root = Context()
    await root.plugin(Greeter)
    await root.plugin(greeter_plugin)
    root.emit("app/ready", "Cordis Python")
    await root.fiber.dispose()


if __name__ == "__main__":
    asyncio.run(main())
