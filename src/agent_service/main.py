import asyncio

from agent_service.bootstrap import create_app, create_runner


async def main():
    async with create_runner() as runner:
        app = create_app(runner)
        await app.run()


if __name__ == "__main__":
    asyncio.run(main())
