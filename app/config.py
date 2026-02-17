from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import List

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    BOT_TOKEN: str = Field(..., description="Telegram bot token from BotFather")
    PUBLIC_BASE_URL: str = Field(..., description="Public HTTPS base URL for webhooks, e.g. https://xxx.onrender.com")
    WEBHOOK_SECRET: str = Field(default="hook", description="Secret path segment for webhook")

    # ApiFree
    APIFREE_API_KEY: str = Field(..., description="ApiFree API key")
    APIFREE_BASE_URL: str = Field(default="https://api.apifree.ai", description="ApiFree base URL")
    APIFREE_CHAT_MODEL: str = Field(default="gpt-4o-mini")
    APIFREE_IMAGE_MODEL: str = Field(default="stable-diffusion-xl")
    APIFREE_VIDEO_MODEL: str = Field(default="runway-gen2")

    # Storage
    DB_PATH: str = Field(default="./data/app.db")

    # Credits / Referral
    FREE_CREDITS_ON_SIGNUP: int = Field(default=2)
    REF_BONUS_REFERRER: int = Field(default=1)
    REF_BONUS_NEW_USER: int = Field(default=1)

    # Security
    APP_SECRET: str = Field(default="change-me-very-long-random-string")

    # Stars / PRO (optional)
    PRICE_PRO_XTR: int = Field(default=0, description="Telegram Stars price (XTR). 0 disables purchase button.")
    ADMIN_IDS: str = Field(default="")

    def admin_ids(self) -> List[int]:
        if not self.ADMIN_IDS.strip():
            return []
        out = []
        for x in self.ADMIN_IDS.split(","):
            x = x.strip()
            if x:
                out.append(int(x))
        return out

settings = Settings()
