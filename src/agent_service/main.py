import asyncio

from agent_service.core.runner import AgentRunner
from agent_service.ui.app import App


async def main():
    async with AgentRunner() as runner:
        app = App(runner=runner)
        await app.run()


if __name__ == "__main__":
    asyncio.run(main())
