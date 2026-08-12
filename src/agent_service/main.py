from agent_service.ui.app import App
import asyncio


async def main():
    app = App()
    await app.run()


if __name__ == "__main__":
    asyncio.run(main())
