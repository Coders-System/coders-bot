from bot import ModmailBot
from discord.ext import commands, tasks
from logging import getLogger

import discord
import random
import typing
import os
import asyncio

leave_messages: typing.List[str] = [
    " was not pog.",
    " pressed Alt + F4.",
    " was kinda sus..",
    " has experienced kinetic energy.",
    " called HTML a programming language.",
    " lost their Wi-Fi.",
    " didn't want to fix his neighbour's computer.",
    " forgot to remove the USB safely.",
    "'s Discord crashed.",
    " didn't go brrr.",
    " was spanked by Joe.",
    " tried to make AI in JavaScript.",
    " put their token on GitHub.",
    " got yeeted.",
]

custom_statuses: typing.Dict[discord.ActivityType, typing.List[str]] = {
    discord.ActivityType.playing: ["jingalala", "python"],
    discord.ActivityType.listening: ["DM messages", "Nightcore music"],
    discord.ActivityType.watching: ["Documentation"],
}


logger = getLogger(__name__)


class General(commands.Cog):
    """A cog for handling all general events and commands"""

    def __init__(self, bot: ModmailBot):
        self.bot = bot

    @tasks.loop(seconds=30)
    async def _status_loop(self):
        running = True
        while running:
            key = random.choice(
                list(custom_statuses.keys())
            )  # a value from discord.ActivityType enum
            status = random.choice(list(custom_statuses[key]))
            await self.bot.change_presence(
                activity=discord.Activity(type=key, name=status)
            )
            await asyncio.sleep(30)

    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.wait_until_ready()
        self._status_loop.start()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Triggered when member leaves server."""
        channel: discord.TextChannel = discord.utils.get(
            member.guild.channels, id=int(os.environ["GENERAL_CHANNEL_ID"])
        )
        msg = random.choice(leave_messages)

        logger.info(f"Member left: {member}")
        await channel.send(f"Member left: `{member}`" + msg)
        return

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Triggered when a member joins the server"""
        role = self.bot.modmail_guild.get_role(int(os.environ["MEMBER_ROLE_ID"]))

        logger.info(f"Member joined: {member}")
        await member.add_roles(role)
        channel = discord.utils.get(client.get_all_channels(), id="783359069993435150")
        await channel.send("Welcome to Coder's System! <:coders_system:865248145210212372>")
        return


def setup(bot: ModmailBot):
    bot.add_cog(General(bot))
