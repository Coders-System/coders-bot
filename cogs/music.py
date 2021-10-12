from discord.ext import commands
from bot import ModmailBot
from logging import getLogger

import re
import typing
import discord
import lavalink
import os
import math

url_rx = re.compile(r"https?://(?:www\.)?.+")
logger = getLogger(__name__)


class Music(commands.Cog):
    """Commands related to music."""

    def __init__(self, bot: ModmailBot) -> None:
        self.bot = bot
        lavalink.add_event_hook(self.track_hook)

    @commands.Cog.listener()
    async def on_ready(self):
        # Initialize lavalink connection
        bot = self.bot
        if bot.lavalink is None:
            bot.lavalink = lavalink.Client(bot.user.id)
            bot.lavalink.add_node(
                os.environ["LAVALINK_HOST"],
                2333,
                os.environ["LAVALINK_PASSWORD"],
                "eu",
                "default-node",
            )
            bot.add_listener(bot.lavalink.voice_update_handler, "on_socket_response")
            logger.info("Initialized connection to lavalink server")

    @staticmethod
    def format_timestamp(millis: int):
        millis = int(millis)
        seconds = (millis / 1000) % 60
        seconds = int(seconds)
        minutes = (millis / (1000 * 60)) % 60
        minutes = int(minutes)
        hours = (millis / (1000 * 60 * 60)) % 24

        if int(hours) != 0:
            return "%s:%s:%s" % (
                int(hours),
                int(minutes) if int(minutes) > 10 else f"0{int(minutes)}",
                int(seconds) if int(seconds) > 10 else f"0{int(seconds)}",
            )
        return "%s:%s" % (
            int(minutes) if int(minutes) > 10 else f"0{int(minutes)}",
            int(seconds) if int(seconds) > 10 else f"0{int(seconds)}",
        )

    def cog_unload(self) -> None:
        """This removes any event hooks that were registered when the cog is unloaded"""
        self.bot.lavalink._event_hooks.clear()

    async def cog_before_invoke(self, ctx: commands.Context) -> bool:
        """Command before-invoke handler."""
        guild_check = ctx.guild is not None
        if guild_check:
            await self.ensure_voice(ctx)
        return guild_check

    async def cog_command_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.CommandInvokeError):
            # Sending the error message as a reply
            await ctx.reply(error.original)

    async def ensure_voice(self, ctx: commands.Context) -> None:
        """This check ensures that the bot and command author are in the same voicechannel."""
        player = self.bot.lavalink.player_manager.create(
            ctx.guild.id, endpoint=str(ctx.guild.region)
        )
        player.store(
            "channel_id", ctx.channel.id
        )  # Storing channel ID in player to be used on events
        should_connect = ctx.command.name in ("play",)

        if not ctx.author.voice or not ctx.author.voice.channel:
            # Our cog_command_error handler catches this and sends the error msg.
            raise commands.CommandInvokeError(
                "🔊 You are not connected to a voice channel."
            )

        if not player.is_connected:
            if not should_connect:
                raise commands.CommandInvokeError(
                    "❌ I am not connected to a voice channel."
                )

            permissions = ctx.author.voice.channel.permissions_for(ctx.me)

            if not permissions.connect or not permissions.speak:
                raise commands.CommandInvokeError(
                    "I need the `CONNECT` and `SPEAK` permissions."
                )

            player.store("channel", ctx.channel.id)
            await self.connect_to(ctx.guild.id, str(ctx.author.voice.channel.id))
        else:
            if int(player.channel_id) != ctx.author.voice.channel.id:
                raise commands.CommandInvokeError("❌ You are not in my voice channel.")

    async def track_hook(self, event) -> None:
        if isinstance(event, lavalink.events.QueueEndEvent):
            guild_id = int(event.player.guild_id)
            return await self.connect_to(guild_id, None)

        elif isinstance(event, lavalink.events.TrackStartEvent):
            # Send now playing message whenever a track starts
            channel_id: int = event.player.fetch("channel_id")
            channel = self.bot.get_channel(channel_id)
            embed = discord.Embed(color=discord.Color.blue())
            embed.description = f"**[{event.track.title}]({event.track.uri})**"
            embed.add_field(name="By", value=event.track.author)
            embed.add_field(
                name="Duration", value=self.format_timestamp(event.track.duration)
            )
            requester = self.bot.get_user(event.track.requester)
            embed.set_footer(text=f"Requested by: {requester}")
            embed.set_thumbnail(
                url=f"https://i3.ytimg.com/vi/{event.track.identifier}/maxresdefault.jpg"
            )
            embed.set_author(name="Now Playing", url=requester.avatar_url)

            return await channel.send(embed=embed)

    async def connect_to(self, guild_id: int, channel_id: str) -> None:
        """Connects to the given voicechannel ID. A channel_id of `None` means disconnect."""
        ws = self.bot._connection._get_websocket(guild_id)
        await ws.voice_state(str(guild_id), channel_id, self_deaf=True)

    @commands.command(aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str) -> None:
        """Searches and plays a song from a given query."""
        # Get the player for this guild from cache.
        player: lavalink.DefaultPlayer = self.bot.lavalink.player_manager.get(
            ctx.guild.id
        )
        query = query.strip("<>")

        if not url_rx.match(query):
            query = f"ytsearch:{query}"

        results = await player.node.get_tracks(query)

        if not results or not results["tracks"]:
            return await ctx.send(f"Nothing found for '{query}'!")

        embed = discord.Embed(color=discord.Color.blue())
        if results["loadType"] == "PLAYLIST_LOADED":
            tracks = results["tracks"]
            for track in tracks:
                player.add(requester=ctx.author.id, track=track)

            embed.title = "Playlist Enqueued!"
            embed.description = (
                f'{results["playlistInfo"]["name"]} - {len(tracks)} tracks'
            )
        else:
            track = lavalink.models.AudioTrack(
                results["tracks"][0], ctx.author.id, recommended=True
            )
            track.duration
            if player.is_playing:
                embed.description = f"**[{track.title}]({track.uri})**"
                embed.add_field(name="By", value=track.author)
                embed.add_field(name="Position in queue", value=len(player.queue) + 1)
                embed.set_thumbnail(
                    url=f"https://i3.ytimg.com/vi/{track.identifier}/maxresdefault.jpg"
                )
                embed.set_footer(text=f"Requested by: {ctx.author}")
                embed.set_author(name="Added to queue", url=ctx.author.avatar_url)
                await ctx.reply(embed=embed)
            else:
                await ctx.message.add_reaction("⏯️")
            player.add(requester=ctx.author.id, track=track)

        if not player.is_playing:
            await player.play()

    @commands.command(aliases=["stop"])
    async def disconnect(self, ctx: commands.Context) -> None:
        """Disconnects the bot from the voice channel and clears it's queue."""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        if not ctx.author.voice or (
            player.is_connected
            and ctx.author.voice.channel.id != int(player.channel_id)
        ):
            # Those who aren't in the voice channel, they can't disconnect the bot
            return await ctx.send(
                "You are not in my voice channel, so you can't disconnect me"
            )
        # Clearing queue
        player.queue.clear()
        # Stops the current track
        await player.stop()
        # Disconnect from the voice channel
        await ctx.message.add_reaction("⏹️")
        return await self.connect_to(ctx.guild.id, None)

    @commands.command(aliases=["q"])
    async def queue(self, ctx: commands.Context) -> None:
        """Shows the music queue for this server"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)

        queue = player.queue
        description = ""
        if len(queue):
            for i, v in enumerate(queue, start=1):
                requester = ctx.guild.get_member(v.requester)
                description += f"{i}. [**{v.title}**]({v.uri}) (Requested by {requester.mention})\n"
        else:
            description = "Queue is empty."
        em = discord.Embed(
            title="Music Queue", color=discord.Color.blue(), description=description
        )
        await ctx.send(embed=em)

    @commands.command()
    async def pause(self, ctx: commands.Context) -> None:
        """Pauses the current track"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not player.is_connected:
            # Don't disconnect if not connected
            return await ctx.send(
                "I am not connected to any voice channels in this server :c"
            )
        if not ctx.author.voice or (
            player.is_connected
            and ctx.author.voice.channel.id != int(player.channel_id)
        ):
            # Those who aren't in the voice channel, they can't pause the bot
            return await ctx.send(
                "You are not in my voice channel, so you can't pause me"
            )
        await ctx.message.add_reaction("⏸️")
        if not player.paused:
            await player.set_pause(True)
        else:
            await ctx.send("I am already paused.")

    @commands.command()
    async def resume(self, ctx: commands.Context) -> None:
        """Resumes the current track"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not player.is_connected:
            # Don't disconnect if not connected
            return await ctx.send(
                "I am not connected to any voice channels in this server :c"
            )
        if not ctx.author.voice or (
            player.is_connected
            and ctx.author.voice.channel.id != int(player.channel_id)
        ):
            # Those who aren't in the voice channel, they can't pause the bot
            return await ctx.send(
                "You are not in my voice channel, so you can't resume me"
            )
        await ctx.message.add_reaction("⏯️")
        if player.paused:
            await player.set_pause(False)
        else:
            await ctx.send("I am not paused.")

    @commands.command(aliases=["np"])
    async def nowplaying(self, ctx: commands.Context) -> None:
        """Shows the track that is currently playing"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not player.is_connected:
            # Don't disconnect if not connected
            return await ctx.send(
                "I am not connected to any voice channels in this server :c"
            )
        playing: lavalink.AudioTrack = player.current
        desc = f"[**{playing.title}**]({playing.uri}) (Requested by {ctx.guild.get_member(playing.requester).mention})\n\n"
        position = ["=" for x in range(30)]
        position[
            math.floor(player.position / playing.duration * len(position))
        ] = "**>**"
        embed = discord.Embed(
            title="Currently Playing",
            color=discord.Color.blue(),
            description=desc
            + f"**{self.format_timestamp(player.position)}** {''.join(position)} **{self.format_timestamp(playing.duration)}**",
        )
        embed.set_thumbnail(
            url=f"https://i3.ytimg.com/vi/{playing.identifier}/maxresdefault.jpg"
        )
        await ctx.send(embed=embed)

    @commands.command()
    async def volume(
        self, ctx: commands.Context, *, level: typing.Optional[int] = None
    ) -> None:
        """Changes the bot's volume level"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not player.is_connected:
            # Don't disconnect if not connected
            return await ctx.send(
                "I am not connected to any voice channels in this server :c"
            )

        if not level:
            await ctx.send(f"The current volume is: **{player.volume}**")
        else:
            if level > 100:
                return await ctx.send("Volume level cannot exceed 100.")
            await player.set_volume(level)
            return await ctx.send(f"Set volume level to **{level}**.")

    @commands.command()
    async def skip(self, ctx: commands.Context) -> None:
        """Skips to the next track in the queue, if any"""
        player = self.bot.lavalink.player_manager.get(ctx.guild.id)
        if not player.is_connected:
            # Don't disconnect if not connected
            return await ctx.send(
                "I am not connected to any voice channels in this server :c"
            )
        if not ctx.author.voice or (
            player.is_connected
            and ctx.author.voice.channel.id != int(player.channel_id)
        ):
            # Those who aren't in the voice channel, they can't pause the bot
            return await ctx.send(
                "You are not in my voice channel, so you can't skip the current track"
            )
        await player.skip()
        return await ctx.message.add_reaction("⏭️")


def setup(bot):
    bot.add_cog(Music(bot))
