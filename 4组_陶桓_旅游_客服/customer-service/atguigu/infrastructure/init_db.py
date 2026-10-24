from sqlalchemy import text

from atguigu.common.event_loop import run_async
from atguigu.infrastructure.db import db_engine
from atguigu.models import models as _models  # noqa: F401
from atguigu.models.models import Base


async def init_database() -> None:
    async with db_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        # 兼容已有 conversations 表：幂等补充 task_state 列
        await connection.execute(
            text(
                "ALTER TABLE conversations "
                "ADD COLUMN IF NOT EXISTS task_state JSONB"
            )
        )

if __name__ == '__main__':
    run_async(init_database())