from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

AsyncSessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class Database:
    """应用生命周期内共享的引擎与短会话工厂。"""

    engine: AsyncEngine
    session_factory: AsyncSessionFactory

    async def close(self) -> None:
        await self.engine.dispose()


def create_database_engine(
    database_url: str,
    *,
    echo: bool = False,
) -> AsyncEngine:
    """创建应用级异步引擎；调用方应在应用退出时释放它。"""

    return create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> AsyncSessionFactory:
    """创建短生命周期 Session 的工厂，不共享 AsyncSession 实例。"""

    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def create_database(
    database_url: str,
    *,
    echo: bool = False,
) -> Database:
    """创建应用数据库资源，但不会主动连接数据库。"""

    engine = create_database_engine(database_url, echo=echo)
    return Database(
        engine=engine,
        session_factory=create_session_factory(engine),
    )
