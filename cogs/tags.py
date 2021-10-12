from bot import ModmailBot
from discord.ext import commands
from logging import getLogger

import os
import discord
import datetime
import math
import DiscordUtils

logger = getLogger(__name__)
STAFF_ROLE_ID = int(os.environ["STAFF_ROLE_ID"])


# TODO add embed to rm and edit commands
# TODO add author check for edit and delete cmd, so that only author can delete/edit their tags
class Tags(commands.Cog):
    """A cog for all tag-related functionality."""

    def __init__(self, bot: ModmailBot):
        self.bot = bot
        self.collection = self.bot.api.db.tags

    @commands.group(aliases=["tags"], invoke_without_command=True)
    async def tag(self, ctx: commands.Context, *, tag_name: str = None):
        if not tag_name:
            return await self.all.__call__(
                ctx
            )  # Running "tags all" if no tag name is specified

        tag = await self.collection.find_one({"name": tag_name})
        if not tag:
            return await ctx.reply(f"Tag **{tag_name}** not found.")
        return await ctx.send(tag["content"])

    @tag.command(aliases=["ls", "dir"])
    async def all(self, ctx: commands.Context):
        all_tags = [i async for i in self.collection.find({})]

        pages = []
        tags_per_page = 5
        slices = math.ceil(len(all_tags) / tags_per_page)
        tags_iterator = [
            all_tags[tags_per_page * i : tags_per_page * i + tags_per_page]
            for i in range(0, slices)
        ]
        for index, tag_chunk in enumerate(tags_iterator):
            embed = discord.Embed(
                color=discord.Color.blue(),
                title=f"All Tags [Page {index + 1} of {len(list(tags_iterator))}]",
            )
            embed.description = "\n".join(
                [
                    f"{i + 5 * index}. **{x['name']}** (By {'<@' + str(x['author']) + '>'})"
                    for i, x in enumerate(tag_chunk, start=1)
                ]
            )
            pages.append(embed)

        paginator = DiscordUtils.Pagination.AutoEmbedPaginator(
            ctx, remove_reactions=True
        )
        return await paginator.run(pages)

    @tag.command(aliases=["create"])
    @commands.has_role(STAFF_ROLE_ID)
    async def add(self, ctx: commands.Context, tag_name, *, tag_content):
        """Adds a tag. Staff can use it only"""
        existing_tag = await self.collection.find_one({"name": tag_name})
        if existing_tag:
            return await ctx.reply(f"The tag **{tag_name}** already exists.")

        await self.collection.insert_one(
            {"name": tag_name, "content": tag_content, "author": ctx.author.id}
        )

        embed = discord.Embed(title="Added Tag", color=discord.Color.blue())
        embed.add_field(name="Name", value=tag_name, inline=False)
        embed.add_field(
            name="Content",
            value=f"{tag_content[:1000]}{'...' if len(tag_content) > 1000 else ''}",
        )
        embed.timestamp = datetime.datetime.utcnow()
        embed.set_footer(text=f"Added by {ctx.author}", icon_url=ctx.author.avatar_url)

        return await ctx.reply(embed=embed)

    @tag.command(aliases=["rm", "del", "delete"])
    @commands.has_role(STAFF_ROLE_ID)
    async def remove(self, ctx: commands.Context, tag_name):
        existing_tag = await self.collection.find_one({"name": tag_name})
        if not existing_tag:
            return await ctx.reply(f"Tag **{tag_name}** not found.")

        if ctx.author.id != int(existing_tag["author"]):
            return await ctx.reply(
                "You are not the author of this tag, hence you cannot delete it."
            )

        await self.collection.delete_one({"name": tag_name})
        return await ctx.reply(f"Deleted tag **{tag_name}**.")

    @tag.command(aliases=["update"])
    @commands.has_role(STAFF_ROLE_ID)
    async def edit(self, ctx: commands.Context, tag_name, *, tag_content):
        existing_tag = await self.collection.find_one({"name": tag_name})
        if not existing_tag:
            return await ctx.reply(f"Tag **{tag_name}** not found.")

        if ctx.author.id != int(existing_tag["author"]):
            return await ctx.reply(
                "You are not the author of this tag, hence you cannot edit it."
            )

        existing_tag["content"] = tag_content
        await self.collection.replace_one(
            {
                "name": tag_name,
            },
            existing_tag,
        )
        return await ctx.reply(f"Edited tag **{tag_name}**.")


def setup(bot: ModmailBot):
    bot.add_cog(Tags(bot))
