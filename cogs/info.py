from discord.ext import commands
from bot import ModmailBot
from datetime import datetime

import discord
import typing
import time
import psutil
import os
import sys


line_break = "\n"
verification_mapping = {
    "none": "No criteria set.",
    "low": "Member must have a verified email on their Discord account.",
    "medium": "Member must have a verified email and be registered on Discord for more than five minutes.",
    "high": "Member must have a verified email, be registered on Discord for more than five minutes, and be a member of the guild itself for more than ten minutes.",
    "extreme": "Member must have a verified phone on their Discord account.",
}

meaningful_bool = lambda x: "Yes" if x else "No"


class Info(commands.Cog):
    """A cog for accessing information about a user, channel, or the server"""

    def __init__(self, bot: ModmailBot):
        self.bot = bot
        self.process = psutil.Process(os.getpid())

    @commands.command(aliases=["pong", "latency"])
    async def ping(self, ctx: commands.Context):
        """Shows you the bot's latency to Discord"""
        # Measuring rest ping
        start = time.perf_counter()
        await ctx.trigger_typing()
        end = time.perf_counter()

        rest_latency = round((end - start) * 1000)  # Ping to REST API, in ms
        gateway_latency = round(self.bot.latency * 1000)  # Ping to gateway, in ms

        em = discord.Embed(title="🏓 Ping Pong! 🏓", color=ctx.author.color)
        em.add_field(
            name="Incoming Latency (WS)", value=f"`{gateway_latency} ms`", inline=False
        )
        em.add_field(
            name="Outgoing Latency (HTTP)", value=f"`{rest_latency} ms`", inline=False
        )
        em.timestamp = datetime.now().astimezone()

        return await ctx.send(embed=em)

    @commands.command(aliases=["guild", "server", "guildinfo"])
    async def serverinfo(self, ctx: commands.Context):
        """Shows you information about this guild"""
        await ctx.trigger_typing()

        guild: discord.Guild = ctx.guild
        em = discord.Embed(title=guild.name, color=discord.Color.blue())

        em.add_field(name="ID", value=guild.id)
        em.add_field(name="Owner", value=guild.owner.mention)
        em.add_field(
            name="Description",
            value="N/A" if not guild.description else guild.description,
            inline=False,
        )
        em.add_field(
            name="Created At",
            value=guild.created_at.strftime("%d %b, %Y on %I:%M:%S %p"),
        )
        em.add_field(name="Default Role", value=guild.default_role)

        emojis_field = (
            " ".join([str(x) for x in guild.emojis][:20]) + "..."
            if len(guild.emojis) > 20
            else ""
        )
        if emojis_field:
            em.add_field(
                name="Emojis",
                value=emojis_field,
                inline=False,
            )
        em.add_field(name="Member Count", value=guild.member_count)
        em.add_field(
            name="Channel Count",
            value=len(guild.text_channels) + len(guild.voice_channels),
        )
        em.add_field(name="Role Count", value=len(guild.roles))

        features_field = ", ".join([f"`{x}`" for x in guild.features])
        if features_field:
            em.add_field(name="Features", value=features_field, inline=False)

        em.add_field(
            name="Verification Level",
            value=guild.verification_level
            if not verification_mapping.get(str(guild.verification_level))
            else f"{str(guild.verification_level).capitalize()} - {verification_mapping.get(str(guild.verification_level))}",
            inline=False,
        )

        if str(guild.splash_url).strip():
            em.set_image(url=guild.splash_url)

        em.timestamp = datetime.now().astimezone()
        em.set_thumbnail(url=guild.icon_url)
        return await ctx.send(embed=em)

    @commands.command(aliases=["user", "member", "memberinfo"])
    async def userinfo(
        self, ctx: commands.Context, *, member: typing.Optional[str] = None
    ):
        """Shows you information about a member"""
        await ctx.trigger_typing()

        if member:
            member = await commands.MemberConverter().convert(ctx, member)
        else:
            member: discord.Member = ctx.author

        em = discord.Embed(color=member.color)

        em.add_field(name="Username", value=member)
        em.add_field(name="ID", value=member.id)

        roles = [x.mention for x in member.roles]
        roles.reverse()
        roles_field = ", ".join(roles[:-1])
        if roles_field.strip():
            em.add_field(name="Roles", value=roles_field, inline=False)
        em.add_field(name="Display Name", value=member.display_name)
        em.add_field(name="Status", value=str(member.status).capitalize())

        if member.activity:
            em.add_field(
                name="Activity/Custom Status", value=member.activity.name, inline=False
            )

        em.add_field(
            name="Joined At",
            value=member.joined_at.strftime("%d %b, %Y on %I:%M:%S %p"),
        )
        em.add_field(
            name="Created At",
            value=member.created_at.strftime("%d %b, %Y on %I:%M:%S %p"),
        )

        permissions_field = ", ".join(
            f"`{x[0].upper()}`" for x in member.guild_permissions if x[1]
        )
        if permissions_field.strip():
            em.add_field(name="Permissions", value=permissions_field, inline=False)

        em.set_author(name=ctx.author, icon_url=ctx.author.avatar_url)
        em.set_thumbnail(url=member.avatar_url)
        em.timestamp = datetime.now().astimezone()

        return await ctx.send(embed=em)

    @commands.command(aliases=["channel"])
    async def channelinfo(
        self, ctx: commands.Context, *, channel: typing.Optional[str] = None
    ):
        """Shows you information about a channel"""
        if not channel:
            channel = ctx.message.channel
        else:
            channel: discord.TextChannel = (
                await commands.TextChannelConverter().convert(ctx, channel)
            )

        em = discord.Embed(
            title=f"Channel Info: {channel.name.capitalize()}", color=ctx.author.color
        )

        em.add_field(name="Name", value=channel.name)
        em.add_field(name="ID", value=channel.id)
        em.add_field(name="Mention", value=channel.mention)

        if channel.last_message:
            em.add_field(name="Last Message", value=channel.last_message.content[:50])
            em.add_field(name="Last Message ID", value=channel.last_message_id)

        em.add_field(name="Position", value=channel.position + 1)

        if channel.topic:
            em.add_field(
                name="Topic",
                value=f"{channel.topic[:50]}{'...' if len(channel.topic) > 50 else ''}",
                inline=False,
            )

        em.add_field(name="Is NSFW", value=meaningful_bool(channel.is_nsfw()))
        em.add_field(name="Is News", value=meaningful_bool(channel.is_news()))
        em.add_field(name="Slowmode delay", value=f"{channel.slowmode_delay} secs")

        if channel.category:
            em.add_field(name="Category Name", value=channel.category)
            em.add_field(name="Category ID", value=channel.category_id)

        em.timestamp = datetime.now().astimezone()

        return await ctx.send(embed=em)

    @commands.command(aliases=["serveravatar", "serverpfp"])
    async def servericon(self, ctx: commands.Context):
        """Shows you this server's icon"""
        em = discord.Embed(title=f"{ctx.guild}'s Icon", color=ctx.author.color)
        em.set_image(url=ctx.guild.icon_url)
        em.timestamp = datetime.now().astimezone()

        return await ctx.send(embed=em)

    @commands.command(aliases=["profilepic", "pfp"])
    async def avatar(
        self, ctx: commands.Context, *, member: typing.Optional[str] = None
    ):
        """Shows you the profile avatar of a server member"""
        if member:
            member = await commands.MemberConverter().convert(ctx, member)
        else:
            member: discord.Member = ctx.author

        em = discord.Embed(title=f"{member}'s Avatar", color=member.color)
        em.set_image(url=member.avatar_url)
        em.timestamp = datetime.now().astimezone()

        return await ctx.send(embed=em)


def setup(bot: ModmailBot):
    bot.add_cog(Info(bot))
