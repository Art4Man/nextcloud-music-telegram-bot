from __future__ import annotations

from dataclasses import dataclass

from telegram import Message, Update
from telegram.ext import ContextTypes


@dataclass(slots=True)
class UploadJob:
    update: Update
    context: ContextTypes.DEFAULT_TYPE
    status_message: Message
