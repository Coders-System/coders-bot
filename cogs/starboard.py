from bot import ModmailBot
from discord.ext import commands
from logging import getLogger

import discord
import os
import datetime

logger = getLogger(__name__)

starboard_emoji = "⭐"
star_requirement = int(os.environ["STARBOARD_REQUIRED_STARS"])


class Starboard(commands.Cog):
    """A cog for managing the starboard system"""

    def __init__(self, bot: ModmailBot):
        self.bot = bot
        self.collection = self.bot.api.db.starred_messages

    async def _add_to_starboard(self, message: discord.Message, stars: int):
        # Sending msg to starboard channel
        starboard_channel = discord.utils.get(
            message.guild.text_channels, name="starboard"
        )
        embed = discord.Embed(
            title="Jump to message!",
            url=message.jump_url,
            description=message.content,
            color=discord.Color.gold(),
        )
        embed.set_author(name=str(message.author), icon_url=message.author.avatar_url)
        if len(message.attachments) > 0:
            embed.set_image(url=message.attachments[0].url)

        embed.timestamp = datetime.datetime.utcnow()
        embed.set_footer(text=f"Message ID: {message.id}")

        starboard_msg = await starboard_channel.send(
            f":star: **{stars}** | {message.channel.mention}", embed=embed
        )

        # Adding to DB
        payload = {
            "channel_id": message.channel.id,
            "starboard_message_id": starboard_msg.id,
            "message_id": message.id,
            "stars": stars,
        }
        await self.collection.insert_one(payload)
        logger.info(f"Added message with ID {message.id} to starboard")

    async def _update_starboard(self, payload, message: discord.Message, stars: int):
        # Updating DB
        await self.collection.replace_one(
            {"message_id": message.id},
            {
                "channel_id": message.channel.id,
                "starboard_message_id": payload["starboard_message_id"],
                "message_id": message.id,
                "stars": stars,
            },
        )
        starboard_channel = discord.utils.get(
            message.guild.text_channels, name="starboard"
        )

        # Updating starboard msg
        msg = await starboard_channel.fetch_message(payload["starboard_message_id"])
        starred_msg_channel = discord.utils.get(
            msg.guild.text_channels, id=int(payload["channel_id"])
        )
        await msg.edit(content=f":star: **{stars}** | {starred_msg_channel.mention}")
        logger.info("Updated message with ID {message.id} in starboard")

    async def _remove_from_starboard(self, payload, message: discord.Message):
        # Deleting from DB
        await self.collection.delete_one({"message_id": message.id})

        # Deleting starboard message
        starboard_channel = discord.utils.get(
            message.guild.text_channels, name="starboard"
        )
        starred_msg = await starboard_channel.fetch_message(
            payload["starboard_message_id"]
        )
        await starred_msg.delete()
        logger.info(f"Removed message with ID {message.id} from starboard")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, event: discord.RawReactionActionEvent):
        emoji = event.emoji

        if emoji.is_unicode_emoji() and str(emoji) == starboard_emoji:
            channel = self.bot.get_channel(event.channel_id)
            message: discord.Message = await channel.fetch_message(event.message_id)

            if message.author.id == event.user_id or message.author.bot:
                return await message.remove_reaction(starboard_emoji, event.member)
            
            star_count = 0

            for x in message.reactions:
                if x.emoji == starboard_emoji:
                    star_count = x.count

            if star_count >= star_requirement:
                starred_message = await self.collection.find_one(
                    {"message_id": message.id}
                )
                if starred_message:
                    await self._update_starboard(
                        starred_message, message, starred_message["stars"] + 1
                    )
                else:
                    await self._add_to_starboard(message, star_count)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, event: discord.RawReactionActionEvent):
        emoji = event.emoji

        if emoji.is_unicode_emoji() and str(emoji) == starboard_emoji:
            channel = self.bot.get_channel(event.channel_id)
            message = await channel.fetch_message(event.message_id)
            reactions_with_star = [
                i for i in message.reactions if i.emoji == starboard_emoji
            ]

            star_count = len(reactions_with_star)

            starred_message = await self.collection.find_one({"message_id": message.id})

            if not starred_message:
                logger.info("Message not found in starboard database")
                return

            if star_count < 1:
                return await self._remove_from_starboard(starred_message, message)

            await self._update_starboard(starred_message, message, star_count)


def setup(bot: ModmailBot):
    bot.add_cog(Starboard(bot))
