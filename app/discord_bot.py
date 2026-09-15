from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

import httpx
import websockets

from app import config
from app import account as user_account
from app.store import Store

log = logging.getLogger("realitify.discord")

# Invite the bot with Manage Channels + Manage Webhooks + View + Send + Embed Links:
# https://discord.com/oauth2/authorize?client_id=CLIENT_ID&permissions=536890384&scope=bot%20applications.commands
API = "https://discord.com/api/v10"
PERM_VIEW = 1 << 10
PERM_SEND = 1 << 11
PERM_EMBED = 1 << 14
PERM_HISTORY = 1 << 16
PERM_WEBHOOKS = 1 << 29
USER_ALLOW = str(PERM_VIEW | PERM_SEND | PERM_EMBED | PERM_HISTORY)
BOT_ALLOW = str(PERM_VIEW | PERM_SEND | PERM_EMBED | PERM_HISTORY | PERM_WEBHOOKS)
EVERYONE_DENY = str(PERM_VIEW)


class DiscordAPIError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Discord API {status}: {body[:400]}")
        self.status = status


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bot {config.DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "Realitify (https://realitify.cz, 1.0)",
    }


def _slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "")
    ascii_text = folded.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug[:80] or "ucet"


class DiscordBot:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.http = httpx.AsyncClient(timeout=20.0, headers=_headers())
        self.bot_user_id = ""
        self._seq: int | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._ack = True

    async def aclose(self) -> None:
        await self.http.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self.http.request(method, f"{API}{path}", **kwargs)
        if response.status_code >= 400:
            raise DiscordAPIError(response.status_code, response.text)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def bootstrap(self) -> None:
        me = await self.request("GET", "/users/@me")
        self.bot_user_id = str(me["id"])
        await self.request(
            "PUT",
            f"/applications/{self.bot_user_id}/guilds/{config.DISCORD_GUILD_ID}/commands",
            json=[
                {
                    "name": "link",
                    "description": "Propojí Realitify účet se soukromým Discord kanálem",
                    "options": [
                        {
                            "name": "kod",
                            "description": "Jednorázový kód z nastavení Realitify",
                            "type": 3,
                            "required": True,
                        }
                    ],
                }
            ],
        )

    async def create_private_channel_for_user(self, discord_user_id: str, name: str, existing_id: str = "") -> dict[str, Any]:
        guild_id = config.DISCORD_GUILD_ID
        overwrites = [
            {"id": guild_id, "type": 0, "deny": EVERYONE_DENY, "allow": "0"},
            {"id": discord_user_id, "type": 1, "allow": USER_ALLOW, "deny": "0"},
            {"id": self.bot_user_id, "type": 1, "allow": BOT_ALLOW, "deny": "0"},
        ]
        if existing_id:
            try:
                channel = await self.request("GET", f"/channels/{existing_id}")
                await self.request("PATCH", f"/channels/{existing_id}", json={"permission_overwrites": overwrites})
                return channel
            except DiscordAPIError as exc:
                if exc.status != 404:
                    raise
        taken = {str(item.get("name") or "") for item in await self.request("GET", f"/guilds/{guild_id}/channels")}
        channel_name = name
        suffix = 2
        while channel_name in taken:
            channel_name = f"{name}-{suffix}"[:100]
            suffix += 1
        return await self.request(
            "POST",
            f"/guilds/{guild_id}/channels",
            json={
                "name": channel_name,
                "type": 0,
                "topic": "Soukromý kanál Realitify — sem chodí všechny hlídací profily.",
                "permission_overwrites": overwrites,
            },
        )

    async def create_account_webhook(self, channel_id: str) -> dict[str, Any]:
        hooks = await self.request("GET", f"/channels/{channel_id}/webhooks")
        for hook in hooks or []:
            if (hook.get("name") or "") == "Realitify" and hook.get("url"):
                return hook
        if len(hooks or []) >= 10:
            # Discord allows max 10 webhooks per channel. Reuse the first one instead of
            # creating byty-2 — this app uses a single webhook for all monitors.
            for hook in hooks:
                if hook.get("url"):
                    return hook
            raise RuntimeError("Kanál má 10 webhooků a žádný nejde znovu použít")
        return await self.request(
            "POST",
            f"/channels/{channel_id}/webhooks",
            json={"name": "Realitify"},
        )

    async def send_welcome(self, channel_id: str, username: str) -> None:
        try:
            await self.request(
                "POST",
                f"/channels/{channel_id}/messages",
                json={
                    "content": (
                        f"Ahoj {username}, kanál je jen pro tebe. "
                        "Všechny hlídací profily z Realitify budou chodit sem."
                    )
                },
            )
        except DiscordAPIError:
            log.exception("Welcome message failed")

    async def handle_link(self, interaction: dict[str, Any]) -> str:
        options = ((interaction.get("data") or {}).get("options") or [])
        code = str(next((item.get("value") for item in options if item.get("name") == "kod"), "") or "")
        member = interaction.get("member") or {}
        user = member.get("user") or interaction.get("user") or {}
        discord_user_id = str(user.get("id") or "")
        username = str(user.get("global_name") or user.get("username") or "uživatel")
        if not user_account.match_discord_link_code(self.store, code):
            return "Kód neplatí nebo vypršel. V Realitify vygeneruj nový a zkus `/link` znovu."
        account = user_account.account_record(self.store)
        channel_name = f"byty-{_slug(account.get('first') or username)}"
        channel = await self.create_private_channel_for_user(
            discord_user_id,
            channel_name,
            str(account.get("discord_channel_id") or ""),
        )
        old_user = str(account.get("discord_user_id") or "")
        if old_user and old_user != discord_user_id:
            try:
                await self.request("DELETE", f"/channels/{channel['id']}/permissions/{old_user}")
            except DiscordAPIError:
                pass
        hook = await self.create_account_webhook(str(channel["id"]))
        webhook_url = str(hook.get("url") or "")
        if not webhook_url:
            return "Kanál vznikl, ale Discord nevrátil webhook URL. Zkus `/link` znovu."
        user_account.save_discord_connection(
            self.store,
            {
                "discord_user_id": discord_user_id,
                "discord_username": username,
                "discord_channel_id": str(channel["id"]),
                "discord_channel_name": str(channel.get("name") or channel_name),
                "discord_webhook_url": webhook_url,
                "discord_webhook_id": str(hook.get("id") or ""),
                "discord_linked_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        user_account.clear_discord_link_code(self.store)
        await self.send_welcome(str(channel["id"]), username)
        return (
            f"Hotovo. Kanál **#{channel.get('name')}** vidíš jen ty. "
            "Všechny hlídací profily z Realitify budou chodit sem."
        )

    async def respond(self, interaction: dict[str, Any], content: str) -> None:
        await self.request(
            "POST",
            f"/interactions/{interaction['id']}/{interaction['token']}/callback",
            json={"type": 4, "data": {"content": content, "flags": 64}},
        )

    async def on_interaction(self, interaction: dict[str, Any]) -> None:
        name = ((interaction.get("data") or {}).get("name") or "")
        if interaction.get("type") != 2 or name != "link":
            return
        try:
            message = await self.handle_link(interaction)
        except Exception as exc:
            log.exception("Discord /link failed")
            message = f"Nepodařilo se propojit účet: {exc}"
        try:
            await self.respond(interaction, message)
        except DiscordAPIError:
            log.exception("Discord interaction reply failed")

    async def _heartbeat(self, ws: Any, interval: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                if not self._ack:
                    await ws.close()
                    return
                self._ack = False
                await ws.send(json.dumps({"op": 1, "d": self._seq}))
        except asyncio.CancelledError:
            return
        except websockets.exceptions.ConnectionClosedOK:
            return
        except Exception:
            log.exception("Discord heartbeat failed")

    async def gateway_once(self) -> None:
        info = await self.request("GET", "/gateway/bot")
        url = f"{info['url']}?v=10&encoding=json"
        async with websockets.connect(url, max_size=2**22, ping_interval=None) as ws:
            try:
                while True:
                    raw = await ws.recv()
                    payload = json.loads(raw)
                    if payload.get("s") is not None:
                        self._seq = payload["s"]
                    op = payload.get("op")
                    if op == 10:
                        interval = float(payload["d"]["heartbeat_interval"]) / 1000.0
                        self._ack = True
                        if self._heartbeat_task:
                            self._heartbeat_task.cancel()
                        self._heartbeat_task = asyncio.create_task(self._heartbeat(ws, interval * 0.9))
                        await ws.send(
                            json.dumps(
                                {
                                    "op": 2,
                                    "d": {
                                        "token": config.DISCORD_BOT_TOKEN,
                                        "intents": 1,
                                        "properties": {"os": "mac", "browser": "realitify", "device": "realitify"},
                                    },
                                }
                            )
                        )
                    elif op == 11:
                        self._ack = True
                    elif op == 7:
                        await ws.close()
                        return
                    elif op == 9:
                        await ws.close()
                        return
                    elif op == 0 and payload.get("t") == "INTERACTION_CREATE":
                        asyncio.create_task(self.on_interaction(payload.get("d") or {}))
            except asyncio.CancelledError:
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                raise

    async def run_forever(self) -> None:
        await self.bootstrap()
        print(f"Discord bot připojen (user {self.bot_user_id})", flush=True)
        while True:
            try:
                await self.gateway_once()
            except asyncio.CancelledError:
                raise
            except websockets.exceptions.ConnectionClosedOK:
                pass
            except Exception:
                log.exception("Discord gateway dropped")
            await asyncio.sleep(5)


async def run_discord_bot(store: Store) -> None:
    if not config.DISCORD_BOT_TOKEN or not config.DISCORD_GUILD_ID:
        return
    bot = DiscordBot(store)
    try:
        await bot.run_forever()
    finally:
        try:
            await asyncio.wait_for(bot.aclose(), timeout=1.0)
        except (asyncio.TimeoutError, Exception):
            pass
