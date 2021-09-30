__version__ = "0.0.2"


import asyncio
import copy
import logging
import os
import re
import signal
import sys
import lavalink
import typing
from datetime import datetime
from subprocess import PIPE
from types import SimpleNamespace

import discord
import isodate
from aiohttp import ClientSession
from discord.ext import commands, tasks
from discord.ext.commands.view import StringView
from emoji import UNICODE_EMOJI
from pkg_resources import parse_version

from core.utils import tryint


try:
    # noinspection PyUnresolvedReferences
    from colorama import init

    init()
except ImportError:
    pass

from core import checks
from core.changelog import Changelog
from core.clients import ApiClient, MongoDBClient, PluginDatabaseClient
from core.config import ConfigManager
from core.models import (
    DMDisabled,
    HostingMethod,
    InvalidConfigError,
    PermissionLevel,
    SafeFormatter,
    configure_logging,
    getLogger,
)
from core.thread import ThreadManager
from core.time import human_timedelta
from core.utils import normalize_alias, truncate

logger = getLogger(__name__)

temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
if not os.path.exists(temp_dir):
    os.mkdir(temp_dir)

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except AttributeError:
        logger.error("Failed to use WindowsProactorEventLoopPolicy.", exc_info=True)


class ModmailBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=None, intents=intents
        )  # implemented in `get_prefix`
        self._session = None
        self._api = None
        self.metadata_loop = None
        self.autoupdate_loop = None
        self.formatter = SafeFormatter()
        self.loaded_cogs = [
            "cogs.modmail",
            "cogs.plugins",
            "cogs.utility",
            "cogs.general",
            "cogs.starboard",
            "cogs.music",
        ]
        self._connected = asyncio.Event()
        self.start_time = datetime.utcnow()
        self.lavalink: typing.Optional[lavalink.Client] = None

        self.config = ConfigManager(self)
        self.config.populate_cache()

        self.threads = ThreadManager(self)

        self.log_file_name = os.path.join(temp_dir, f"{self.token.split('.')[0]}.log")
        self._configure_logging()

        self.plugin_db = PluginDatabaseClient(self)  # Deprecated
        self.startup()

    @property
    def uptime(self) -> str:
        now = datetime.utcnow()
        delta = now - self.start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        days, hours = divmod(hours, 24)

        fmt = "{h}h {m}m {s}s"
        if days:
            fmt = "{d}d " + fmt

        return self.formatter.format(fmt, d=days, h=hours, m=minutes, s=seconds)

    @property
    def hosting_method(self) -> HostingMethod:
        # use enums
        if ".heroku" in os.environ.get("PYTHONHOME", ""):
            return HostingMethod.HEROKU

        if os.environ.get("pm_id"):
            return HostingMethod.PM2

        if os.environ.get("INVOCATION_ID"):
            return HostingMethod.SYSTEMD

        if os.environ.get("USING_DOCKER"):
            return HostingMethod.DOCKER

        if os.environ.get("TERM"):
            return HostingMethod.SCREEN

        return HostingMethod.OTHER

    def startup(self):
        logger.line()
        logger.info("Coder's Bot")
        logger.info("v%s", __version__)
        logger.line()
        logger.info("discord.py: v%s", discord.__version__)
        logger.line()

        for cog in self.loaded_cogs:
            logger.debug("Loading %s.", cog)
            try:
                self.load_extension(cog)
                logger.debug("Successfully loaded %s.", cog)
            except Exception:
                logger.exception("Failed to load %s.", cog)
        logger.line("debug")

    def _configure_logging(self):
        level_text = self.config["log_level"].upper()
        logging_levels = {
            "CRITICAL": logging.CRITICAL,
            "ERROR": logging.ERROR,
            "WARNING": logging.WARNING,
            "INFO": logging.INFO,
            "DEBUG": logging.DEBUG,
        }
        logger.line()

        log_level = logging_levels.get(level_text)
        if log_level is None:
            log_level = self.config.remove("log_level")
            logger.warning("Invalid logging level set: %s.", level_text)
            logger.warning("Using default logging level: INFO.")
        else:
            logger.info("Logging level: %s", level_text)

        logger.info("Log file: %s", self.log_file_name)
        configure_logging(self.log_file_name, log_level)
        logger.debug("Successfully configured logging.")

    @property
    def version(self):
        return parse_version(__version__)

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            self._session = ClientSession(loop=self.loop)
        return self._session

    @property
    def api(self) -> ApiClient:
        if self._api is None:
            if self.config["database_type"].lower() == "mongodb":
                self._api = MongoDBClient(self)
            else:
                logger.critical("Invalid database type.")
                raise RuntimeError
        return self._api

    @property
    def db(self):
        # deprecated
        return self.api.db

    async def get_prefix(self, message=None):
        return [self.prefix, f"<@{self.user.id}> ", f"<@!{self.user.id}> "]

    def run(self):
        loop = self.loop

        try:
            loop.add_signal_handler(signal.SIGINT, lambda: loop.stop())
            loop.add_signal_handler(signal.SIGTERM, lambda: loop.stop())
        except NotImplementedError:
            pass

        async def runner():
            try:
                retry_intents = False
                try:
                    await self.start(self.token)
                except discord.PrivilegedIntentsRequired:
                    retry_intents = True
                if retry_intents:
                    await self.http.close()
                    if self.ws is not None and self.ws.open:
                        await self.ws.close(code=1000)
                    self._ready.clear()
                    intents = discord.Intents.default()
                    intents.members = True
                    # Try again with members intent
                    self._connection._intents = intents
                    logger.warning(
                        "Attempting to login with only the server members privileged intent. Some plugins might not work correctly."
                    )
                    await self.start(self.token)
            except discord.PrivilegedIntentsRequired:
                logger.critical(
                    "Privileged intents are not explicitly granted in the discord developers dashboard."
                )
            except discord.LoginFailure:
                logger.critical("Invalid token")
            except Exception:
                logger.critical("Fatal exception", exc_info=True)
            finally:
                if not self.is_closed():
                    await self.close()
                if self._session:
                    await self._session.close()

        def stop_loop_on_completion(f):
            loop.stop()

        def _cancel_tasks():
            if sys.version_info < (3, 8):
                task_retriever = asyncio.Task.all_tasks
            else:
                task_retriever = asyncio.all_tasks

            tasks = {t for t in task_retriever(loop=loop) if not t.done()}

            if not tasks:
                return

            logger.info("Cleaning up after %d tasks.", len(tasks))
            for task in tasks:
                task.cancel()

            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            logger.info("All tasks finished cancelling.")

            for task in tasks:
                if task.cancelled():
                    continue
                if task.exception() is not None:
                    loop.call_exception_handler(
                        {
                            "message": "Unhandled exception during Client.run shutdown.",
                            "exception": task.exception(),
                            "task": task,
                        }
                    )

        future = asyncio.ensure_future(runner(), loop=loop)
        future.add_done_callback(stop_loop_on_completion)
        try:
            loop.run_forever()
        except KeyboardInterrupt:
            logger.info("Received signal to terminate bot and event loop.")
        finally:
            future.remove_done_callback(stop_loop_on_completion)
            logger.info("Cleaning up tasks.")

            try:
                _cancel_tasks()
                if sys.version_info >= (3, 6):
                    loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                logger.info("Closing the event loop.")
                loop.close()

        if not future.cancelled():
            try:
                return future.result()
            except KeyboardInterrupt:
                # I am unsure why this gets raised here but suppress it anyway
                return None

    @property
    def bot_owner_ids(self):
        owner_ids = self.config["owners"]
        if owner_ids is not None:
            owner_ids = set(map(int, str(owner_ids).split(",")))
        if self.owner_id is not None:
            owner_ids.add(self.owner_id)
        permissions = self.config["level_permissions"].get(
            PermissionLevel.OWNER.name, []
        )
        for perm in permissions:
            owner_ids.add(int(perm))
        return owner_ids

    async def is_owner(self, user: discord.User) -> bool:
        if user.id in self.bot_owner_ids:
            return True
        return await super().is_owner(user)

    @property
    def log_channel(self) -> typing.Optional[discord.TextChannel]:
        channel_id = self.config["log_channel_id"]
        if channel_id is not None:
            try:
                channel = self.get_channel(int(channel_id))
                if channel is not None:
                    return channel
            except ValueError:
                pass
            logger.debug("LOG_CHANNEL_ID was invalid, removed.")
            self.config.remove("log_channel_id")
        if self.main_category is not None:
            try:
                channel = self.main_category.channels[0]
                self.config["log_channel_id"] = channel.id
                logger.warning(
                    "No log channel set, setting #%s to be the log channel.",
                    channel.name,
                )
                return channel
            except IndexError:
                pass
        logger.warning(
            "No log channel set, set one with `%ssetup` or `%sconfig set log_channel_id <id>`.",
            self.prefix,
            self.prefix,
        )
        return None

    @property
    def mention_channel(self):
        channel_id = self.config["mention_channel_id"]
        if channel_id is not None:
            try:
                channel = self.get_channel(int(channel_id))
                if channel is not None:
                    return channel
            except ValueError:
                pass
            logger.debug("MENTION_CHANNEL_ID was invalid, removed.")
            self.config.remove("mention_channel_id")

        return self.log_channel

    @property
    def update_channel(self):
        channel_id = self.config["update_channel_id"]
        if channel_id is not None:
            try:
                channel = self.get_channel(int(channel_id))
                if channel is not None:
                    return channel
            except ValueError:
                pass
            logger.debug("UPDATE_CHANNEL_ID was invalid, removed.")
            self.config.remove("update_channel_id")

        return self.log_channel

    async def wait_for_connected(self) -> None:
        await self.wait_until_ready()
        await self._connected.wait()
        await self.config.wait_until_ready()

    @property
    def snippets(self) -> typing.Dict[str, str]:
        return self.config["snippets"]

    @property
    def aliases(self) -> typing.Dict[str, str]:
        return self.config["aliases"]

    @property
    def auto_triggers(self) -> typing.Dict[str, str]:
        return self.config["auto_triggers"]

    @property
    def token(self) -> str:
        token = self.config["token"]
        if token is None:
            logger.critical(
                "TOKEN must be set, set this as bot token found on the Discord Developer Portal."
            )
            sys.exit(0)
        return token

    @property
    def guild_id(self) -> typing.Optional[int]:
        guild_id = self.config["guild_id"]
        if guild_id is not None:
            try:
                return int(str(guild_id))
            except ValueError:
                self.config.remove("guild_id")
                logger.critical("Invalid GUILD_ID set.")
        else:
            logger.debug("No GUILD_ID set.")
        return None

    @property
    def guild(self) -> typing.Optional[discord.Guild]:
        """
        The guild that the bot is serving
        (the server where users message it from)
        """
        return discord.utils.get(self.guilds, id=self.guild_id)

    @property
    def modmail_guild(self) -> typing.Optional[discord.Guild]:
        """
        The guild that the bot is operating in
        (where the bot is creating threads)
        """
        modmail_guild_id = self.config["modmail_guild_id"]
        if modmail_guild_id is None:
            return self.guild
        try:
            guild = discord.utils.get(self.guilds, id=int(modmail_guild_id))
            if guild is not None:
                return guild
        except ValueError:
            pass
        self.config.remove("modmail_guild_id")
        logger.critical("Invalid MODMAIL_GUILD_ID set.")
        return self.guild

    @property
    def using_multiple_server_setup(self) -> bool:
        return self.modmail_guild != self.guild

    @property
    def main_category(self) -> typing.Optional[discord.CategoryChannel]:
        if self.modmail_guild is not None:
            category_id = self.config["main_category_id"]
            if category_id is not None:
                try:
                    cat = discord.utils.get(
                        self.modmail_guild.categories, id=int(category_id)
                    )
                    if cat is not None:
                        return cat
                except ValueError:
                    pass
                self.config.remove("main_category_id")
                logger.debug("MAIN_CATEGORY_ID was invalid, removed.")
            cat = discord.utils.get(self.modmail_guild.categories, name="Modmail")
            if cat is not None:
                self.config["main_category_id"] = cat.id
                logger.debug(
                    'No main category set explicitly, setting category "Modmail" as the main category.'
                )
                return cat
        return None

    @property
    def blocked_users(self) -> typing.Dict[str, str]:
        return self.config["blocked"]

    @property
    def blocked_roles(self) -> typing.Dict[str, str]:
        return self.config["blocked_roles"]

    @property
    def blocked_whitelisted_users(self) -> typing.List[str]:
        return self.config["blocked_whitelist"]

    @property
    def prefix(self) -> str:
        return str(self.config["prefix"])

    @property
    def mod_color(self) -> int:
        return self.config.get("mod_color")

    @property
    def recipient_color(self) -> int:
        return self.config.get("recipient_color")

    @property
    def main_color(self) -> int:
        return self.config.get("main_color")

    @property
    def error_color(self) -> int:
        return self.config.get("error_color")

    def command_perm(self, command_name: str) -> PermissionLevel:
        level = self.config["override_command_level"].get(command_name)
        if level is not None:
            try:
                return PermissionLevel[level.upper()]
            except KeyError:
                logger.warning(
                    "Invalid override_command_level for command %s.", command_name
                )
                self.config["override_command_level"].pop(command_name)

        command = self.get_command(command_name)
        if command is None:
            logger.debug("Command %s not found.", command_name)
            return PermissionLevel.INVALID
        level = next(
            (
                check.permission_level
                for check in command.checks
                if hasattr(check, "permission_level")
            ),
            None,
        )
        if level is None:
            logger.debug("Command %s does not have a permission level.", command_name)
            return PermissionLevel.INVALID
        return level

    async def on_connect(self):
        try:
            await self.api.validate_database_connection()
        except Exception:
            logger.debug("Logging out due to failed database connection.")
            return await self.logout()

        logger.debug("Connected to gateway.")
        await self.config.refresh()
        await self.api.setup_indexes()
        self._connected.set()

    async def on_ready(self):
        """Bot startup, sets uptime."""

        # Wait until config cache is populated with stuff from db and on_connect ran
        await self.wait_for_connected()

        if self.guild is None:
            logger.error("Logging out due to invalid GUILD_ID.")
            return await self.logout()

        logger.line()
        logger.debug("Client ready.")
        logger.info("Logged in as: %s", self.user)
        logger.info("Bot ID: %s", self.user.id)
        owners = ", ".join(
            getattr(self.get_user(owner_id), "name", str(owner_id))
            for owner_id in self.bot_owner_ids
        )
        logger.info("Owners: %s", owners)
        logger.info("Prefix: %s", self.prefix)
        logger.info("Guild Name: %s", self.guild.name)
        logger.info("Guild ID: %s", self.guild.id)
        if self.using_multiple_server_setup:
            logger.info("Receiving guild ID: %s", self.modmail_guild.id)
        logger.line()

        await self.threads.populate_cache()

        # closures
        closures = self.config["closures"]
        logger.info("There are %d thread(s) pending to be closed.", len(closures))
        logger.line()

        for recipient_id, items in tuple(closures.items()):
            after = (
                datetime.fromisoformat(items["time"]) - datetime.utcnow()
            ).total_seconds()
            if after <= 0:
                logger.debug("Closing thread for recipient %s.", recipient_id)
                after = 0
            else:
                logger.debug(
                    "Thread for recipient %s will be closed after %s seconds.",
                    recipient_id,
                    after,
                )

            thread = await self.threads.find(recipient_id=int(recipient_id))

            if not thread:
                # If the channel is deleted
                logger.debug("Failed to close thread for recipient %s.", recipient_id)
                self.config["closures"].pop(recipient_id)
                await self.config.update()
                continue

            await thread.close(
                closer=self.get_user(items["closer_id"]),
                after=after,
                silent=items["silent"],
                delete_channel=items["delete_channel"],
                message=items["message"],
                auto_close=items.get("auto_close", False),
            )

        for log in await self.api.get_open_logs():
            if self.get_channel(int(log["channel_id"])) is None:
                logger.debug(
                    "Unable to resolve thread with channel %s.", log["channel_id"]
                )
                log_data = await self.api.post_log(
                    log["channel_id"],
                    {
                        "open": False,
                        "title": None,
                        "closed_at": str(datetime.utcnow()),
                        "close_message": "Channel has been deleted, no closer found.",
                        "closer": {
                            "id": str(self.user.id),
                            "name": self.user.name,
                            "discriminator": self.user.discriminator,
                            "avatar_url": str(self.user.avatar_url),
                            "mod": True,
                        },
                    },
                )
                if log_data:
                    logger.debug(
                        "Successfully closed thread with channel %s.", log["channel_id"]
                    )
                else:
                    logger.debug(
                        "Failed to close thread with channel %s, skipping.",
                        log["channel_id"],
                    )

        if self.config.get("data_collection"):
            self.metadata_loop = tasks.Loop(
                self.post_metadata,
                seconds=0,
                minutes=0,
                hours=1,
                count=None,
                reconnect=True,
                loop=None,
            )
            self.metadata_loop.before_loop(self.before_post_metadata)
            self.metadata_loop.start()

        self.autoupdate_loop = tasks.Loop(
            self.autoupdate,
            seconds=0,
            minutes=0,
            hours=1,
            count=None,
            reconnect=True,
            loop=None,
        )
        self.autoupdate_loop.before_loop(self.before_autoupdate)
        self.autoupdate_loop.start()

        other_guilds = [
            guild
            for guild in self.guilds
            if guild not in {self.guild, self.modmail_guild}
        ]
        if any(other_guilds):
            logger.warning(
                "The bot is in more servers other than the main and staff server. "
                "This may cause data compromise (%s).",
                ", ".join(guild.name for guild in other_guilds),
            )
            logger.warning(
                "If the external servers are valid, you may ignore this message."
            )

        # Initialize lavalink connection
        if self.lavalink == None:
            self.lavalink = lavalink.Client(self.user.id)
            self.lavalink.add_node(
                os.environ["LAVALINK_HOST"],
                2333,
                os.environ["LAVALINK_PASSWORD"],
                "eu",
                "default-node",
            )
            self.add_listener(self.lavalink.voice_update_handler, "on_socket_response")
            logger.info("Initialized connection to lavalink server")

    async def convert_emoji(self, name: str) -> str:
        ctx = SimpleNamespace(bot=self, guild=self.modmail_guild)
        converter = commands.EmojiConverter()

        if name not in UNICODE_EMOJI:
            try:
                name = await converter.convert(ctx, name.strip(":"))
            except commands.BadArgument as e:
                logger.warning("%s is not a valid emoji. %s.", e)
                raise
        return name

    async def retrieve_emoji(self) -> typing.Tuple[str, str]:

        sent_emoji = self.config["sent_emoji"]
        blocked_emoji = self.config["blocked_emoji"]

        if sent_emoji != "disable":
            try:
                sent_emoji = await self.convert_emoji(sent_emoji)
            except commands.BadArgument:
                logger.warning("Removed sent emoji (%s).", sent_emoji)
                sent_emoji = self.config.remove("sent_emoji")
                await self.config.update()

        if blocked_emoji != "disable":
            try:
                blocked_emoji = await self.convert_emoji(blocked_emoji)
            except commands.BadArgument:
                logger.warning("Removed blocked emoji (%s).", blocked_emoji)
                blocked_emoji = self.config.remove("blocked_emoji")
                await self.config.update()

        return sent_emoji, blocked_emoji

    def check_account_age(self, author: discord.Member) -> bool:
        account_age = self.config.get("account_age")
        now = datetime.utcnow()

        try:
            min_account_age = author.created_at + account_age
        except ValueError:
            logger.warning("Error with 'account_age'.", exc_info=True)
            min_account_age = author.created_at + self.config.remove("account_age")

        if min_account_age > now:
            # User account has not reached the required time
            delta = human_timedelta(min_account_age)
            logger.debug("Blocked due to account age, user %s.", author.name)

            if str(author.id) not in self.blocked_users:
                new_reason = (
                    f"System Message: New Account. Required to wait for {delta}."
                )
                self.blocked_users[str(author.id)] = new_reason

            return False
        return True

    def check_guild_age(self, author: discord.Member) -> bool:
        guild_age = self.config.get("guild_age")
        now = datetime.utcnow()

        if not hasattr(author, "joined_at"):
            logger.warning("Not in guild, cannot verify guild_age, %s.", author.name)
            return True

        try:
            min_guild_age = author.joined_at + guild_age
        except ValueError:
            logger.warning("Error with 'guild_age'.", exc_info=True)
            min_guild_age = author.joined_at + self.config.remove("guild_age")

        if min_guild_age > now:
            # User has not stayed in the guild for long enough
            delta = human_timedelta(min_guild_age)
            logger.debug("Blocked due to guild age, user %s.", author.name)

            if str(author.id) not in self.blocked_users:
                new_reason = (
                    f"System Message: Recently Joined. Required to wait for {delta}."
                )
                self.blocked_users[str(author.id)] = new_reason

            return False
        return True

    def check_manual_blocked_roles(self, author: discord.Member) -> bool:
        if isinstance(author, discord.Member):
            for r in author.roles:
                if str(r.id) in self.blocked_roles:

                    blocked_reason = self.blocked_roles.get(str(r.id)) or ""
                    now = datetime.utcnow()

                    # etc "blah blah blah... until 2019-10-14T21:12:45.559948."
                    end_time = re.search(r"until ([^`]+?)\.$", blocked_reason)
                    if end_time is None:
                        # backwards compat
                        end_time = re.search(r"%([^%]+?)%", blocked_reason)
                        if end_time is not None:
                            logger.warning(
                                r"Deprecated time message for role %s, block and unblock again to update.",
                                r.name,
                            )

                    if end_time is not None:
                        after = (
                            datetime.fromisoformat(end_time.group(1)) - now
                        ).total_seconds()
                        if after <= 0:
                            # No longer blocked
                            self.blocked_roles.pop(str(r.id))
                            logger.debug("No longer blocked, role %s.", r.name)
                            return True
                    logger.debug("User blocked, role %s.", r.name)
                    return False

        return True

    def check_manual_blocked(self, author: discord.Member) -> bool:
        if str(author.id) not in self.blocked_users:
            return True

        blocked_reason = self.blocked_users.get(str(author.id)) or ""
        now = datetime.utcnow()

        if blocked_reason.startswith("System Message:"):
            # Met the limits already, otherwise it would've been caught by the previous checks
            logger.debug("No longer internally blocked, user %s.", author.name)
            self.blocked_users.pop(str(author.id))
            return True
        # etc "blah blah blah... until 2019-10-14T21:12:45.559948."
        end_time = re.search(r"until ([^`]+?)\.$", blocked_reason)
        if end_time is None:
            # backwards compat
            end_time = re.search(r"%([^%]+?)%", blocked_reason)
            if end_time is not None:
                logger.warning(
                    r"Deprecated time message for user %s, block and unblock again to update.",
                    author.name,
                )

        if end_time is not None:
            after = (datetime.fromisoformat(end_time.group(1)) - now).total_seconds()
            if after <= 0:
                # No longer blocked
                self.blocked_users.pop(str(author.id))
                logger.debug("No longer blocked, user %s.", author.name)
                return True
        logger.debug("User blocked, user %s.", author.name)
        return False

    async def _process_blocked(self, message):
        _, blocked_emoji = await self.retrieve_emoji()
        if await self.is_blocked(
            message.author, channel=message.channel, send_message=True
        ):
            await self.add_reaction(message, blocked_emoji)
            return True
        return False

    async def is_blocked(
        self,
        author: discord.User,
        *,
        channel: discord.TextChannel = None,
        send_message: bool = False,
    ) -> typing.Tuple[bool, str]:

        member = self.guild.get_member(author.id)
        if member is None:
            # try to find in other guilds
            for g in self.guilds:
                member = g.get_member(author.id)
                if member:
                    break

            if member is None:
                logger.debug("User not in guild, %s.", author.id)

        if member is not None:
            author = member

        if str(author.id) in self.blocked_whitelisted_users:
            if str(author.id) in self.blocked_users:
                self.blocked_users.pop(str(author.id))
                await self.config.update()
            return False

        blocked_reason = self.blocked_users.get(str(author.id)) or ""

        if not self.check_account_age(author) or not self.check_guild_age(author):
            new_reason = self.blocked_users.get(str(author.id))
            if new_reason != blocked_reason:
                if send_message:
                    await channel.send(
                        embed=discord.Embed(
                            title="Message not sent!",
                            description=new_reason,
                            color=self.error_color,
                        )
                    )
            return True

        if not self.check_manual_blocked(author):
            return True

        if not self.check_manual_blocked_roles(author):
            return True

        await self.config.update()
        return False

    async def get_thread_cooldown(self, author: discord.Member):
        thread_cooldown = self.config.get("thread_cooldown")
        now = datetime.utcnow()

        if thread_cooldown == isodate.Duration():
            return

        last_log = await self.api.get_latest_user_logs(author.id)

        if last_log is None:
            logger.debug("Last thread wasn't found, %s.", author.name)
            return

        last_log_closed_at = last_log.get("closed_at")

        if not last_log_closed_at:
            logger.debug("Last thread was not closed, %s.", author.name)
            return

        try:
            cooldown = datetime.fromisoformat(last_log_closed_at) + thread_cooldown
        except ValueError:
            logger.warning("Error with 'thread_cooldown'.", exc_info=True)
            cooldown = datetime.fromisoformat(last_log_closed_at) + self.config.remove(
                "thread_cooldown"
            )

        if cooldown > now:
            # User messaged before thread cooldown ended
            delta = human_timedelta(cooldown)
            logger.debug("Blocked due to thread cooldown, user %s.", author.name)
            return delta
        return

    @staticmethod
    async def add_reaction(msg, reaction: discord.Reaction) -> bool:
        if reaction != "disable":
            try:
                await msg.add_reaction(reaction)
            except (discord.HTTPException, discord.InvalidArgument) as e:
                logger.warning("Failed to add reaction %s: %s.", reaction, e)
                return False
        return True

    async def process_dm_modmail(self, message: discord.Message) -> None:
        """Processes messages sent to the bot."""
        blocked = await self._process_blocked(message)
        if blocked:
            return
        sent_emoji, blocked_emoji = await self.retrieve_emoji()

        if message.type != discord.MessageType.default:
            return

        thread = await self.threads.find(recipient=message.author)
        if thread is None:
            delta = await self.get_thread_cooldown(message.author)
            if delta:
                await message.channel.send(
                    embed=discord.Embed(
                        title=self.config["cooldown_thread_title"],
                        description=self.config["cooldown_thread_response"].format(
                            delta=delta
                        ),
                        color=self.error_color,
                    )
                )
                return

            if self.config["dm_disabled"] in (
                DMDisabled.NEW_THREADS,
                DMDisabled.ALL_THREADS,
            ):
                embed = discord.Embed(
                    title=self.config["disabled_new_thread_title"],
                    color=self.error_color,
                    description=self.config["disabled_new_thread_response"],
                )
                embed.set_footer(
                    text=self.config["disabled_new_thread_footer"],
                    icon_url=self.guild.icon_url,
                )
                logger.info(
                    "A new thread was blocked from %s due to disabled Modmail.",
                    message.author,
                )
                await self.add_reaction(message, blocked_emoji)
                return await message.channel.send(embed=embed)

            thread = await self.threads.create(message.author, message=message)
        else:
            if self.config["dm_disabled"] == DMDisabled.ALL_THREADS:
                embed = discord.Embed(
                    title=self.config["disabled_current_thread_title"],
                    color=self.error_color,
                    description=self.config["disabled_current_thread_response"],
                )
                embed.set_footer(
                    text=self.config["disabled_current_thread_footer"],
                    icon_url=self.guild.icon_url,
                )
                logger.info(
                    "A message was blocked from %s due to disabled Modmail.",
                    message.author,
                )
                await self.add_reaction(message, blocked_emoji)
                return await message.channel.send(embed=embed)

        if not thread.cancelled:
            try:
                await thread.send(message)
            except Exception:
                logger.error("Failed to send message:", exc_info=True)
                await self.add_reaction(message, blocked_emoji)
            else:
                await self.add_reaction(message, sent_emoji)
                self.dispatch("thread_reply", thread, False, message, False, False)

    async def get_contexts(self, message, *, cls=commands.Context):
        """
        Returns all invocation contexts from the message.
        Supports getting the prefix from database as well as command aliases.
        """

        view = StringView(message.content)
        ctx = cls(prefix=self.prefix, view=view, bot=self, message=message)
        thread = await self.threads.find(channel=ctx.channel)

        if self._skip_check(message.author.id, self.user.id):
            return [ctx]

        prefixes = await self.get_prefix()

        invoked_prefix = discord.utils.find(view.skip_string, prefixes)
        if invoked_prefix is None:
            return [ctx]

        invoker = view.get_word().lower()

        # Check if there is any aliases being called.
        alias = self.aliases.get(invoker)
        if alias is not None:
            ctxs = []
            aliases = normalize_alias(
                alias, message.content[len(f"{invoked_prefix}{invoker}") :]
            )
            if not aliases:
                logger.warning("Alias %s is invalid, removing.", invoker)
                self.aliases.pop(invoker)

            for alias in aliases:
                view = StringView(invoked_prefix + alias)
                ctx_ = cls(prefix=self.prefix, view=view, bot=self, message=message)
                ctx_.thread = thread
                discord.utils.find(view.skip_string, prefixes)
                ctx_.invoked_with = view.get_word().lower()
                ctx_.command = self.all_commands.get(ctx_.invoked_with)
                ctxs += [ctx_]
            return ctxs

        ctx.thread = thread
        ctx.invoked_with = invoker
        ctx.command = self.all_commands.get(invoker)
        return [ctx]

    async def trigger_auto_triggers(self, message, channel, *, cls=commands.Context):
        message.author = self.modmail_guild.me
        message.channel = channel
        message.guild = channel.guild

        view = StringView(message.content)
        ctx = cls(prefix=self.prefix, view=view, bot=self, message=message)
        thread = await self.threads.find(channel=ctx.channel)

        invoked_prefix = self.prefix
        invoker = None

        if self.config.get("use_regex_autotrigger"):
            trigger = next(
                filter(
                    lambda x: re.search(x, message.content), self.auto_triggers.keys()
                )
            )
            if trigger:
                invoker = re.search(trigger, message.content).group(0)
        else:
            trigger = next(
                filter(
                    lambda x: x.lower() in message.content.lower(),
                    self.auto_triggers.keys(),
                )
            )
            if trigger:
                invoker = trigger.lower()

        alias = self.auto_triggers[trigger]

        ctxs = []
        if alias is not None:
            ctxs = []
            aliases = normalize_alias(alias)
            if not aliases:
                logger.warning("Alias %s is invalid as called in autotrigger.", invoker)

            for alias in aliases:
                view = StringView(invoked_prefix + alias)
                ctx_ = cls(prefix=self.prefix, view=view, bot=self, message=message)
                ctx_.thread = thread
                discord.utils.find(view.skip_string, await self.get_prefix())
                ctx_.invoked_with = view.get_word().lower()
                ctx_.command = self.all_commands.get(ctx_.invoked_with)
                ctxs += [ctx_]

        for ctx in ctxs:
            if ctx.command:
                old_checks = copy.copy(ctx.command.checks)
                ctx.command.checks = [checks.has_permissions(PermissionLevel.INVALID)]

                await self.invoke(ctx)

                ctx.command.checks = old_checks
                continue

    async def get_context(self, message, *, cls=commands.Context):
        """
        Returns the invocation context from the message.
        Supports getting the prefix from database.
        """

        view = StringView(message.content)
        ctx = cls(prefix=self.prefix, view=view, bot=self, message=message)

        if self._skip_check(message.author.id, self.user.id):
            return ctx

        ctx.thread = await self.threads.find(channel=ctx.channel)

        prefixes = await self.get_prefix()

        invoked_prefix = discord.utils.find(view.skip_string, prefixes)
        if invoked_prefix is None:
            return ctx

        invoker = view.get_word().lower()

        ctx.invoked_with = invoker
        ctx.command = self.all_commands.get(invoker)

        return ctx

    async def update_perms(
        self, name: typing.Union[PermissionLevel, str], value: int, add: bool = True
    ) -> None:
        if value != -1:
            value = str(value)
        if isinstance(name, PermissionLevel):
            level = True
            permissions = self.config["level_permissions"]
            name = name.name
        else:
            level = False
            permissions = self.config["command_permissions"]
        if name not in permissions:
            if add:
                permissions[name] = [value]
        else:
            if add:
                if value not in permissions[name]:
                    permissions[name].append(value)
            else:
                if value in permissions[name]:
                    permissions[name].remove(value)

        if level:
            self.config["level_permissions"] = permissions
        else:
            self.config["command_permissions"] = permissions
        logger.info("Updating permissions for %s, %s (add=%s).", name, value, add)
        await self.config.update()

    async def on_message(self, message):
        await self.wait_for_connected()
        if message.type == discord.MessageType.pins_add and message.author == self.user:
            await message.delete()

        if (
            (
                f"<@{self.user.id}" in message.content
                or f"<@!{self.user.id}" in message.content
            )
            and self.config["alert_on_mention"]
            and not message.author.bot
        ):
            em = discord.Embed(
                title="Bot mention",
                description=f"[Jump URL]({message.jump_url})\n{truncate(message.content, 50)}",
                color=self.main_color,
            )
            if self.config["show_timestamp"]:
                em.timestamp = datetime.utcnow()

            if not self.config["silent_alert_on_mention"]:
                content = self.config["mention"]
            else:
                content = ""
            await self.mention_channel.send(content=content, embed=em)

        await self.process_commands(message)

    async def process_commands(self, message):
        if message.author.bot:
            return

        if isinstance(message.channel, discord.DMChannel):
            return await self.process_dm_modmail(message)

        if message.content.startswith(self.prefix):
            cmd = message.content[len(self.prefix) :].strip()

            # Process snippets
            if cmd in self.snippets:
                snippet = self.snippets[cmd]
                if self.config["anonymous_snippets"]:
                    message.content = f"{self.prefix}fareply {snippet}"
                else:
                    message.content = f"{self.prefix}freply {snippet}"

        ctxs = await self.get_contexts(message)
        for ctx in ctxs:
            if ctx.command:
                if not any(
                    1
                    for check in ctx.command.checks
                    if hasattr(check, "permission_level")
                ):
                    logger.debug(
                        "Command %s has no permissions check, adding invalid level.",
                        ctx.command.qualified_name,
                    )
                    checks.has_permissions(PermissionLevel.INVALID)(ctx.command)

                await self.invoke(ctx)
                continue

            thread = await self.threads.find(channel=ctx.channel)
            if thread is not None:
                anonymous = False
                plain = False
                if self.config.get("anon_reply_without_command"):
                    anonymous = True
                if self.config.get("plain_reply_without_command"):
                    plain = True

                if (
                    self.config.get("reply_without_command")
                    or self.config.get("anon_reply_without_command")
                    or self.config.get("plain_reply_without_command")
                ):
                    await thread.reply(message, anonymous=anonymous, plain=plain)
                else:
                    await self.api.append_log(message, type_="internal")
            elif ctx.invoked_with:
                exc = commands.CommandNotFound(
                    'Command "{}" is not found'.format(ctx.invoked_with)
                )
                self.dispatch("command_error", ctx, exc)

    async def on_typing(self, channel, user, _):
        await self.wait_for_connected()

        if user.bot:
            return

        if isinstance(channel, discord.DMChannel):
            if not self.config.get("user_typing"):
                return

            thread = await self.threads.find(recipient=user)

            if thread:
                await thread.channel.trigger_typing()
        else:
            if not self.config.get("mod_typing"):
                return

            thread = await self.threads.find(channel=channel)
            if thread is not None and thread.recipient:
                if await self.is_blocked(thread.recipient):
                    return
                await thread.recipient.trigger_typing()

    async def handle_reaction_events(self, payload):
        user = self.get_user(payload.user_id)
        if user is None or user.bot:
            return

        channel = self.get_channel(payload.channel_id)
        if not channel:  # dm channel not in internal cache
            _thread = await self.threads.find(recipient=user)
            if not _thread:
                return
            channel = await _thread.recipient.create_dm()

        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden):
            return

        reaction = payload.emoji

        close_emoji = await self.convert_emoji(self.config["close_emoji"])

        if isinstance(channel, discord.DMChannel):
            thread = await self.threads.find(recipient=user)
            if not thread:
                return
            if (
                payload.event_type == "REACTION_ADD"
                and message.embeds
                and str(reaction) == str(close_emoji)
                and self.config.get("recipient_thread_close")
            ):
                ts = message.embeds[0].timestamp
                if thread and ts == thread.channel.created_at:
                    # the reacted message is the corresponding thread creation embed
                    # closing thread
                    return await thread.close(closer=user)
            if (
                message.author == self.user
                and message.embeds
                and self.config.get("confirm_thread_creation")
                and message.embeds[0].title
                == self.config["confirm_thread_creation_title"]
                and message.embeds[0].description
                == self.config["confirm_thread_response"]
            ):
                return
            if not thread.recipient.dm_channel:
                await thread.recipient.create_dm()
            try:
                linked_message = await thread.find_linked_message_from_dm(
                    message, either_direction=True
                )
            except ValueError as e:
                logger.warning("Failed to find linked message for reactions: %s", e)
                return
        else:
            thread = await self.threads.find(channel=channel)
            if not thread:
                return
            try:
                _, linked_message = await thread.find_linked_messages(
                    message.id, either_direction=True
                )
            except ValueError as e:
                logger.warning("Failed to find linked message for reactions: %s", e)
                return

        if self.config["transfer_reactions"] and linked_message is not None:
            if payload.event_type == "REACTION_ADD":
                if await self.add_reaction(linked_message, reaction):
                    await self.add_reaction(message, reaction)
            else:
                try:
                    await linked_message.remove_reaction(reaction, self.user)
                    await message.remove_reaction(reaction, self.user)
                except (discord.HTTPException, discord.InvalidArgument) as e:
                    logger.warning("Failed to remove reaction: %s", e)

    async def on_raw_reaction_add(self, payload):
        await self.handle_reaction_events(payload)

        react_message_id = tryint(self.config.get("react_to_contact_message"))
        react_message_emoji = self.config.get("react_to_contact_emoji")
        if all((react_message_id, react_message_emoji)):
            if payload.message_id == react_message_id:
                if payload.emoji.is_unicode_emoji():
                    emoji_fmt = payload.emoji.name
                else:
                    emoji_fmt = f"<:{payload.emoji.name}:{payload.emoji.id}>"

                if emoji_fmt == react_message_emoji:
                    channel = self.get_channel(payload.channel_id)
                    member = channel.guild.get_member(payload.user_id)
                    if not member.bot:
                        message = await channel.fetch_message(payload.message_id)
                        await message.remove_reaction(payload.emoji, member)
                        await message.add_reaction(emoji_fmt)  # bot adds as well

                        if self.config["dm_disabled"] in (
                            DMDisabled.NEW_THREADS,
                            DMDisabled.ALL_THREADS,
                        ):
                            embed = discord.Embed(
                                title=self.config["disabled_new_thread_title"],
                                color=self.error_color,
                                description=self.config["disabled_new_thread_response"],
                            )
                            embed.set_footer(
                                text=self.config["disabled_new_thread_footer"],
                                icon_url=self.guild.icon_url,
                            )
                            logger.info(
                                "A new thread using react to contact was blocked from %s due to disabled Modmail.",
                                member,
                            )
                            return await member.send(embed=embed)

                        ctx = await self.get_context(message)
                        ctx.author = member
                        await ctx.invoke(
                            self.get_command("contact"),
                            user=member,
                            manual_trigger=False,
                        )

    async def on_raw_reaction_remove(self, payload):
        if self.config["transfer_reactions"]:
            await self.handle_reaction_events(payload)

    async def on_guild_channel_delete(self, channel):
        if channel.guild != self.modmail_guild:
            return

        if isinstance(channel, discord.CategoryChannel):
            if self.main_category == channel:
                logger.debug("Main category was deleted.")
                self.config.remove("main_category_id")
                await self.config.update()
            return

        if not isinstance(channel, discord.TextChannel):
            return

        if self.log_channel is None or self.log_channel == channel:
            logger.info("Log channel deleted.")
            self.config.remove("log_channel_id")
            await self.config.update()
            return

        audit_logs = self.modmail_guild.audit_logs(
            limit=10, action=discord.AuditLogAction.channel_delete
        )
        entry = await audit_logs.find(lambda a: int(a.target.id) == channel.id)

        if entry is None:
            logger.debug(
                "Cannot find the audit log entry for channel delete of %d.", channel.id
            )
            return

        mod = entry.user
        if mod == self.user:
            return

        thread = await self.threads.find(channel=channel)
        if thread and thread.channel == channel:
            logger.debug("Manually closed channel %s.", channel.name)
            await thread.close(closer=mod, silent=True, delete_channel=False)

    async def on_member_remove(self, member):
        if member.guild != self.guild:
            return
        thread = await self.threads.find(recipient=member)
        if thread:
            if self.config["close_on_leave"]:
                await thread.close(
                    closer=member.guild.me,
                    message=self.config["close_on_leave_reason"],
                    silent=True,
                )
            else:
                embed = discord.Embed(
                    description=self.config["close_on_leave_reason"],
                    color=self.error_color,
                )
                await thread.channel.send(embed=embed)

    async def on_member_join(self, member):
        if member.guild != self.guild:
            return
        thread = await self.threads.find(recipient=member)
        if thread:
            embed = discord.Embed(
                description="The recipient has joined the server.", color=self.mod_color
            )
            await thread.channel.send(embed=embed)

    async def on_message_delete(self, message):
        """Support for deleting linked messages"""

        if message.is_system():
            return

        if isinstance(message.channel, discord.DMChannel):
            if message.author == self.user:
                return
            thread = await self.threads.find(recipient=message.author)
            if not thread:
                return
            try:
                message = await thread.find_linked_message_from_dm(message)
            except ValueError as e:
                if str(e) != "Thread channel message not found.":
                    logger.debug("Failed to find linked message to delete: %s", e)
                return
            embed = message.embeds[0]
            embed.set_footer(
                text=f"{embed.footer.text} (deleted)", icon_url=embed.footer.icon_url
            )
            await message.edit(embed=embed)
            return

        if message.author != self.user:
            return

        thread = await self.threads.find(channel=message.channel)
        if not thread:
            return

        audit_logs = self.modmail_guild.audit_logs(
            limit=10, action=discord.AuditLogAction.message_delete
        )

        entry = await audit_logs.find(lambda a: a.target == self.user)

        if entry is None:
            return

        try:
            await thread.delete_message(message, note=False)
            embed = discord.Embed(
                description="Successfully deleted message.", color=self.main_color
            )
        except ValueError as e:
            if str(e) not in {"DM message not found.", "Malformed thread message."}:
                logger.debug("Failed to find linked message to delete: %s", e)
                embed = discord.Embed(
                    description="Failed to delete message.", color=self.error_color
                )
            else:
                return
        except discord.NotFound:
            return
        embed.set_footer(text=f"Message ID: {message.id} from {message.author}.")
        return await message.channel.send(embed=embed)

    async def on_bulk_message_delete(self, messages):
        await discord.utils.async_all(self.on_message_delete(msg) for msg in messages)

    async def on_message_edit(self, before, after):
        if after.author.bot:
            return
        if before.content == after.content:
            return

        if isinstance(after.channel, discord.DMChannel):
            thread = await self.threads.find(recipient=before.author)
            if not thread:
                return

            try:
                await thread.edit_dm_message(after, after.content)
            except ValueError:
                _, blocked_emoji = await self.retrieve_emoji()
                await self.add_reaction(after, blocked_emoji)
            else:
                embed = discord.Embed(
                    description="Successfully Edited Message", color=self.main_color
                )
                embed.set_footer(text=f"Message ID: {after.id}")
                await after.channel.send(embed=embed)

    async def on_error(self, event_method, *args, **kwargs):
        logger.error("Ignoring exception in %s.", event_method)
        logger.error("Unexpected exception:", exc_info=sys.exc_info())

    async def on_command_error(self, context, exception):
        if isinstance(exception, commands.BadArgument):
            await context.trigger_typing()
            await context.send(
                embed=discord.Embed(color=self.error_color, description=str(exception))
            )
        elif isinstance(exception, commands.CommandNotFound):
            logger.warning("CommandNotFound: %s", exception)
        elif isinstance(exception, commands.MissingRequiredArgument):
            await context.send_help(context.command)
        elif isinstance(exception, commands.CommandOnCooldown):
            await context.send(
                embed=discord.Embed(
                    title="Command on cooldown",
                    description=f"Try again in {exception.retry_after:.2f} seconds",
                    color=self.error_color,
                )
            )
        elif isinstance(exception, commands.CheckFailure):
            for check in context.command.checks:
                if not await check(context):
                    if hasattr(check, "fail_msg"):
                        await context.send(
                            embed=discord.Embed(
                                color=self.error_color, description=check.fail_msg
                            )
                        )
                    if hasattr(check, "permission_level"):
                        corrected_permission_level = self.command_perm(
                            context.command.qualified_name
                        )
                        logger.warning(
                            "User %s does not have permission to use this command: `%s` (%s).",
                            context.author.name,
                            context.command.qualified_name,
                            corrected_permission_level.name,
                        )
            logger.warning("CheckFailure: %s", exception)
        elif isinstance(exception, commands.DisabledCommand):
            logger.info(
                "DisabledCommand: %s is trying to run eval but it's disabled",
                context.author.name,
            )
        else:
            logger.error("Unexpected exception:", exc_info=exception)

    async def post_metadata(self):
        info = await self.application_info()

        data = {
            "bot_id": self.user.id,
            "bot_name": str(self.user),
            "avatar_url": str(self.user.avatar_url),
            "guild_id": self.guild_id,
            "guild_name": self.guild.name,
            "member_count": len(self.guild.members),
            "uptime": (datetime.utcnow() - self.start_time).total_seconds(),
            "latency": f"{self.ws.latency * 1000:.4f}",
            "version": str(self.version),
            "selfhosted": True,
            "last_updated": str(datetime.utcnow()),
        }

        if info.team is not None:
            data.update(
                {
                    "owner_name": info.team.owner.name
                    if info.team.owner is not None
                    else "No Owner",
                    "owner_id": info.team.owner_id,
                    "team": True,
                }
            )
        else:
            data.update(
                {
                    "owner_name": info.owner.name,
                    "owner_id": info.owner.id,
                    "team": False,
                }
            )

        async with self.session.post("https://api.modmail.dev/metadata", json=data):
            logger.debug("Uploading metadata to Modmail server.")

    async def before_post_metadata(self):
        await self.wait_for_connected()
        logger.debug("Starting metadata loop.")
        logger.line("debug")
        if not self.guild:
            self.metadata_loop.cancel()

    async def autoupdate(self):
        changelog = await Changelog.from_url(self)
        latest = changelog.latest_version

        if self.version < parse_version(latest.version):
            if self.hosting_method == HostingMethod.HEROKU:
                data = await self.api.update_repository()

                embed = discord.Embed(color=self.main_color)

                commit_data = data["data"]
                user = data["user"]
                embed.set_author(
                    name=user["username"] + " - Updating Bot",
                    icon_url=user["avatar_url"],
                    url=user["url"],
                )

                embed.set_footer(
                    text=f"Updating Modmail v{self.version} " f"-> v{latest.version}"
                )

                embed.description = latest.description
                for name, value in latest.fields.items():
                    embed.add_field(name=name, value=value)

                if commit_data:
                    message = commit_data["commit"]["message"]
                    html_url = commit_data["html_url"]
                    short_sha = commit_data["sha"][:6]
                    embed.add_field(
                        name="Merge Commit",
                        value=f"[`{short_sha}`]({html_url}) "
                        f"{message} - {user['username']}",
                    )
                    logger.info("Bot has been updated.")
                    channel = self.log_channel
                    if self.config["update_notifications"]:
                        await channel.send(embed=embed)
            else:
                try:
                    # update fork if gh_token exists
                    await self.api.update_repository()
                except InvalidConfigError:
                    pass

                command = "git pull"
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stderr=PIPE,
                    stdout=PIPE,
                )
                err = await proc.stderr.read()
                err = err.decode("utf-8").rstrip()
                res = await proc.stdout.read()
                res = res.decode("utf-8").rstrip()

                if err and not res:
                    logger.warning(f"Autoupdate failed: {err}")
                    self.autoupdate_loop.cancel()
                    return

                elif res != "Already up to date.":
                    logger.info("Bot has been updated.")
                    channel = self.update_channel
                    if self.hosting_method in (
                        HostingMethod.PM2,
                        HostingMethod.SYSTEMD,
                    ):
                        embed = discord.Embed(
                            title="Bot has been updated", color=self.main_color
                        )
                        embed.set_footer(
                            text=f"Updating Modmail v{self.version} "
                            f"-> v{latest.version}"
                        )
                        if self.config["update_notifications"]:
                            await channel.send(embed=embed)
                    else:
                        embed = discord.Embed(
                            title="Bot has been updated and is logging out.",
                            description="If you do not have an auto-restart setup, please manually start the bot.",
                            color=self.main_color,
                        )
                        embed.set_footer(
                            text=f"Updating Modmail v{self.version} "
                            f"-> v{latest.version}"
                        )
                        if self.config["update_notifications"]:
                            await channel.send(embed=embed)
                    await self.logout()

    async def before_autoupdate(self):
        await self.wait_for_connected()
        logger.debug("Starting autoupdate loop")

        if self.config.get("disable_autoupdates"):
            logger.warning("Autoupdates disabled.")
            self.autoupdate_loop.cancel()

        if self.hosting_method == HostingMethod.DOCKER:
            logger.warning("Autoupdates disabled as using Docker.")
            self.autoupdate_loop.cancel()

        if (
            not self.config.get("github_token")
            and self.hosting_method == HostingMethod.HEROKU
        ):
            logger.warning("GitHub access token not found.")
            logger.warning("Autoupdates disabled.")
            self.autoupdate_loop.cancel()


def main():
    try:
        # noinspection PyUnresolvedReferences
        import uvloop

        logger.debug("Setting up with uvloop.")
        uvloop.install()
    except ImportError:
        pass

    bot = ModmailBot()
    bot.run()


if __name__ == "__main__":
    main()
