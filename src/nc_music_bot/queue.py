from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from telegram import Message, Update
from telegram.ext import ContextTypes


@dataclass(slots=True)
class UploadJob:
    update: Update
    context: ContextTypes.DEFAULT_TYPE
    status_message: Message
    job_id: str


class QueueManager:
    def __init__(
        self,
        processor: Callable[
            [Update, ContextTypes.DEFAULT_TYPE, Message],
            Awaitable[None],
        ],
    ) -> None:
        self.processor = processor
        self.queues: dict[int, asyncio.Queue[UploadJob]] = defaultdict(asyncio.Queue)
        self.pending: dict[int, list[UploadJob]] = defaultdict(list)
        self.workers: dict[int, asyncio.Task[None]] = {}

    async def enqueue(self,update: Update,context: ContextTypes.DEFAULT_TYPE,) -> None:
        user = update.effective_user

        if user is None:
            return

        user_id = user.id
        position = len(self.pending[user_id]) + 1
        total = position
        message = update.effective_message

        if message is None:
            return

        status = await message.reply_text(
            f"📋 Queued — position {position} of {total}"
        )

        job = UploadJob(
            update=update,
            context=context,
            status_message=status,
            job_id=str(uuid.uuid4()),
        )

        self.pending[user_id].append(job)

        await self.queues[user_id].put(job)

        worker = self.workers.get(user_id)

        if worker is None or worker.done():
            self.workers[user_id] = asyncio.create_task(
                self._worker(user_id)
            )


    async def _worker(self, user_id: int) -> None:
        queue = self.queues[user_id]

        while not queue.empty():
            job = await queue.get()

            if job in self.pending[user_id]:
                self.pending[user_id].remove(job)

            try:
                await self.processor(
                    job.update,
                    job.context,
                    job.status_message,
                )
            finally:
                await self._update_positions(user_id)
                queue.task_done()

        self.workers.pop(user_id, None)

    async def _update_positions(self, user_id: int) -> None:
        jobs = self.pending[user_id]

        total = len(jobs)

        for position, job in enumerate(jobs, start=1):
            await job.status_message.edit_text(
                f"📋 Queued — position {position} of {total}"
            )
