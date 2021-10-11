from bot import ModmailBot
from discord.ext import commands
from logging import getLogger

import discord
import os
import datetime

logger = getLogger(__name__)

starboard_emoji = "⭐"
star_requirement = int(os.environ["STARBOARD_REQUIRED_STARS"])


class Tags(commands.Cog):
    """A cog for tags"""

    def __init__(self, bot: ModmailBot):
        self.bot = bot
        self.collection = self.bot.api.db.starred_messages

    @commands.command(hidden=True)
    @commands.has_role("Staff")
    async def tag_add(self,ctx):
        await ctx.send("added tag boi")

    @commands.command(hidden=True)
    @commands.has_role("Staff")
    async def tag_list(self,ctx):
        await ctx.send(" i am the tags command")

def setup(bot: ModmailBot):
    bot.add_cog(Tags(bot))
