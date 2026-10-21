from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.models.models import ConversationTurn, Conversation


class ConversationTurnRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def is_activate_turn(self, conversation_id: str) -> bool:
        return bool(await self.session.scalar(
            select(
                exists()
                .where(ConversationTurn.conversation_id == conversation_id,
                       ConversationTurn.status.in_(["COLLECTING", "RUNNING"]))
            )
        ))

    async def find_conversation_by_id(self, conv_id: str) -> ConversationTurn | None:
        return await self.session.scalar(
            select(ConversationTurn)
            .where(ConversationTurn.conversation_id == conv_id,
                   ConversationTurn.status == "COLLECTING")
            .with_for_update()
        )

    def add_turn(self, turn:ConversationTurn):
        self.session.add(turn)


    async def find_turn_conversation_by_id(self,turn_id:str)->tuple[ConversationTurn,Conversation]:

        result=await self.session.execute(
            select(ConversationTurn,Conversation)
            .join(
                Conversation,
                Conversation.id==ConversationTurn.conversation_id
            )
            .where(ConversationTurn.id==turn_id)
            .with_for_update(of=Conversation)
        )
        return result.tuples().one()





    async def list_expired_running_turns(
            self,
            now: datetime
    ) -> list[ConversationTurn]:
        """查询租约已经过期的全部运行中 Turn。"""
        result = await self.session.scalars(
            select(ConversationTurn)
            .where(
                ConversationTurn.status == "RUNNING",
                ConversationTurn.locked_until <= now
            )
        )
        return list(result.all())

    async def find_ready_turn_conversation_by_lock(self, now: datetime) -> tuple[ConversationTurn, Conversation] | None:
        """
        查询一个已到期、当前没有其他运行中 Turn 的可执行轮次。

        使用行锁和 SKIP LOCKED，避免多个 AI Worker 同时领取同一个 Turn。
        """
        running_turn = aliased(ConversationTurn)
        results = await self.session.execute(
            select(ConversationTurn, Conversation)
            .join(
                Conversation,
                ConversationTurn.conversation_id == Conversation.id
            )
            .where(
                ConversationTurn.status == "COLLECTING",
                ConversationTurn.collect_until <= now,
                Conversation.mode == "AI",
                ~exists(
                    select(1).where(
                        running_turn.conversation_id == Conversation.id,
                        running_turn.status == "RUNNING",
                    )
                ),
            )
            .order_by(ConversationTurn.collect_until, ConversationTurn.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

        return results.tuples().one_or_none()
