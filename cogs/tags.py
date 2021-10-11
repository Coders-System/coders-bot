from bot import ModmailBot
from discord.ext import commands
from logging import getLogger

import discord
import os
import datetime

logger = getLogger(__name__)

class Tags(commands.Cog):
    """A cog for tags"""

    def __init__(self, bot: ModmailBot):
        self.bot = bot
        self.collection = self.bot.api.db.starred_messages

    @commands.command(hidden=True)
    @commands.has_role("Staff")
    async def tag_add(self,ctx):
        await ctx.send("Add tags")

    @commands.command(hidden=True)
    async def tag_list(self,ctx):
        await ctx.send("List tags")

def setup(bot: ModmailBot):
    bot.add_cog(Tags(bot))
