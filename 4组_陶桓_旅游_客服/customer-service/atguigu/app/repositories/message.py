from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atguigu.models.models import Conversation, Message


class MessageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_conversation_messages(self, conversation_id: str) -> list[Message]:
        results = await  self.session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
        )

        return list(results.all())

    async def find_with_conversation_by_message_id(self, message_id: str) -> tuple[Message, Conversation] | None:
        result = await self.session.execute(
            select(Message, Conversation)
            .join(
                Conversation,
                Message.conversation_id == Conversation.id
            )
            .where(Message.message_id == message_id)
        )

        return result.tuples().one_or_none()

    def add_message(self, message:Message):
        self.session.add(message)

    async def list_user_messages(
            self,
            user_id: str,
            after_sequence: int | None,
            limit: int
    ) -> list[Message]:
        """按用户返回其全部会话的消息（可按游标增量），按 sequence 升序。"""
        statement = (
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.user_id == user_id)
        )
        if after_sequence is not None:
            statement = statement.where(Message.id > after_sequence)
        statement = statement.order_by(Message.id.asc()).limit(limit)
        results = await self.session.scalars(statement)
        return list(results.all())

    async def find_current_messages_by_turn_range(self,
                                                  conversation_id: str,
                                                  start_revision: int,
                                                  snapshot_revision: int) -> list[Message]:
        results = await  self.session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id,
                   Message.input_revision >= start_revision,
                   Message.input_revision <= snapshot_revision,
                   Message.role == "user"
                   )
            .order_by(Message.input_revision)
        )

        return list(results.all())

    async def find_history_message_by_sequence(self,
                                               conversation_id: str,
                                               message_id: int,
                                               limit: int = 30
                                               ) -> list[Message]:
        results = await self.session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id,
                   Message.id < message_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        return list(results.all())

