from bot import ModmailBot
from discord.ext import commands
from logging import getLogger

import discord
import os
import datetime

logger = getLogger(__name__)


# TODO embeds ui
# TODO pagination if able
# TODO renaming cmds if need
class Tags(commands.Cog):
    """A cog for tags"""

    def __init__(self, bot: ModmailBot):
        self.bot = bot
        self.collection = self.bot.api.db.tags

    # TODO not sure if putting "Staff" here is enough or not.
    @commands.group(invoke_without_command=True)
    @commands.has_role("Staff")
    async def tag(self, ctx, tag_name):
        data = await self.collection.find_one({"name": tag_name})
        await ctx.send(f"List tags\n {data}")

    @tag.command(hidden=True)
    async def add(self, ctx, tag_name, *, text):
        await ctx.send(f"Adding tag: {tag_name}\n with text: {text}")
        data = {"name": tag_name, "content": text}

        await self.collection.insert_one(data)

    @tag.command(hidden=True)
    async def rm(self, ctx, tag_name):
        await ctx.send(f"Removing {tag_name}")
        await self.collection.remove_one({name: tag_name})

    @tag.command(hidden=True)
    async def edit(self, ctx, tag_name, *, text):
        await ctx.send(f"Editing tag: {tag_name}\n with new text: {text}")
        data = {"name": tag_name, "content": text}

        await self.collection.replace_one(
            {
                "name": tag_name,
            },
            data,
        )

    @tag.command(hidden=True)
    async def ls(self, ctx):
        data_cursor = self.collection.find()
        await ctx.send(f"List tags")

        # TODO find a way to paginate this
        for document in await data_cursor.to_list(100):
            await ctx.send(document)


def setup(bot: ModmailBot):
    bot.add_cog(Tags(bot))
