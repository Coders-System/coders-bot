import base64
import functools
import re
import string
import typing
from difflib import get_close_matches
from distutils.util import strtobool as _stb  # pylint: disable=import-error
from itertools import takewhile, zip_longest
from urllib import parse

import discord
from discord.ext import commands

__all__ = [
    "strtobool",
    "User",
    "truncate",
    "format_preview",
    "is_image_url",
    "parse_image_url",
    "human_join",
    "days",
    "cleanup_code",
    "match_user_id",
    "create_not_found_embed",
    "parse_alias",
    "normalize_alias",
    "format_description",
    "trigger_typing",
    "escape_code_block",
    "format_channel_name",
    "tryint",
    "match_title",
]


def strtobool(val):
    if isinstance(val, bool):
        return val
    try:
        return _stb(str(val))
    except ValueError:
        val = val.lower()
        if val == "enable":
            return 1
        if val == "disable":
            return 0
        raise


class User(commands.MemberConverter):
    """
    A custom discord.py `Converter` that
    supports `Member`, `User`, and string ID's.
    """

    # noinspection PyCallByClass,PyTypeChecker
    async def convert(self, ctx, argument):
        try:
            return await commands.MemberConverter().convert(ctx, argument)
        except commands.BadArgument:
            pass
        try:
            return await commands.UserConverter().convert(ctx, argument)
        except commands.BadArgument:
            pass
        match = self._get_id_match(argument)
        if match is None:
            raise commands.BadArgument('User "{}" not found'.format(argument))
        return discord.Object(int(match.group(1)))


def truncate(text: str, max: int = 50) -> str:  # pylint: disable=redefined-builtin
    """
    Reduces the string to `max` length, by trimming the message into "...".

    Parameters
    ----------
    text : str
        The text to trim.
    max : int, optional
        The max length of the text.
        Defaults to 50.

    Returns
    -------
    str
        The truncated text.
    """
    text = text.strip()
    return text[: max - 3].strip() + "..." if len(text) > max else text


def format_preview(messages: typing.List[typing.Dict[str, typing.Any]]):
    """
    Used to format previews.

    Parameters
    ----------
    messages : List[Dict[str, Any]]
        A list of messages.

    Returns
    -------
    str
        A formatted string preview.
    """
    messages = messages[:3]
    out = ""
    for message in messages:
        if message.get("type") in {"note", "internal"}:
            continue
        author = message["author"]
        content = str(message["content"]).replace("\n", " ")
        name = author["name"] + "#" + str(author["discriminator"])
        prefix = "[M]" if author["mod"] else "[R]"
        out += truncate(f"`{prefix} {name}:` {content}", max=75) + "\n"

    return out or "No Messages"


def is_image_url(url: str, **kwargs) -> str:
    """
    Check if the URL is pointing to an image.

    Parameters
    ----------
    url : str
        The URL to check.

    Returns
    -------
    bool
        Whether the URL is a valid image URL.
    """
    if url.startswith("https://gyazo.com") or url.startswith("http://gyazo.com"):
        # gyazo support
        url = re.sub(
            r"(http[s]?:\/\/)((?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+)",
            r"\1i.\2.png",
            url,
        )

    return parse_image_url(url, **kwargs)


def parse_image_url(url: str, *, convert_size=True) -> str:
    """
    Convert the image URL into a sized Discord avatar.

    Parameters
    ----------
    url : str
        The URL to convert.

    Returns
    -------
    str
        The converted URL, or '' if the URL isn't in the proper format.
    """
    types = [".png", ".jpg", ".gif", ".jpeg", ".webp"]
    url = parse.urlsplit(url)

    if any(url.path.lower().endswith(i) for i in types):
        if convert_size:
            return parse.urlunsplit((*url[:3], "size=128", url[-1]))
        else:
            return parse.urlunsplit(url)
    return ""


def human_join(strings):
    if len(strings) <= 2:
        return " or ".join(strings)
    return ", ".join(strings[: len(strings) - 1]) + " or " + strings[-1]


def days(day: typing.Union[str, int]) -> str:
    """
    Humanize the number of days.

    Parameters
    ----------
    day: Union[int, str]
        The number of days passed.

    Returns
    -------
    str
        A formatted string of the number of days passed.
    """
    day = int(day)
    if day == 0:
        return "**today**"
    return f"{day} day ago" if day == 1 else f"{day} days ago"


def cleanup_code(content: str) -> str:
    """
    Automatically removes code blocks from the code.

    Parameters
    ----------
    content : str
        The content to be cleaned.

    Returns
    -------
    str
        The cleaned content.
    """
    # remove ```py\n```
    if content.startswith("```") and content.endswith("```"):
        return "\n".join(content.split("\n")[1:-1])

    # remove `foo`
    return content.strip("` \n")


TOPIC_TITLE_REGEX = re.compile(
    r"\bTitle: (.*)\n(?:User ID: )\b", flags=re.IGNORECASE | re.DOTALL
)
TOPIC_UID_REGEX = re.compile(r"\bUser ID:\s*(\d{17,21})\b", flags=re.IGNORECASE)


def match_title(text: str) -> int:
    """
    Matches a title in the format of "Title: XXXX"

    Parameters
    ----------
    text : str
        The text of the user ID.

    Returns
    -------
    Optional[str]
        The title if found
    """
    match = TOPIC_TITLE_REGEX.search(text)
    if match is not None:
        return match.group(1)


def match_user_id(text: str) -> int:
    """
    Matches a user ID in the format of "User ID: 12345".

    Parameters
    ----------
    text : str
        The text of the user ID.

    Returns
    -------
    int
        The user ID if found. Otherwise, -1.
    """
    match = TOPIC_UID_REGEX.search(text)
    if match is not None:
        return int(match.group(1))
    return -1


def create_not_found_embed(word, possibilities, name, n=2, cutoff=0.6) -> discord.Embed:
    # Single reference of Color.red()
    embed = discord.Embed(
        color=discord.Color.red(),
        description=f"**{name.capitalize()} `{word}` cannot be found.**",
    )
    val = get_close_matches(word, possibilities, n=n, cutoff=cutoff)
    if val:
        embed.description += "\nHowever, perhaps you meant...\n" + "\n".join(val)
    return embed


def parse_alias(alias, *, split=True):
    def encode_alias(m):
        return "\x1AU" + base64.b64encode(m.group(1).encode()).decode() + "\x1AU"

    def decode_alias(m):
        return base64.b64decode(m.group(1).encode()).decode()

    alias = re.sub(
        r"(?:(?<=^)(?:\s*(?<!\\)(?:\")\s*)|(?<=&&)(?:\s*(?<!\\)(?:\")\s*))(.+?)"
        r"(?:(?:\s*(?<!\\)(?:\")\s*)(?=&&)|(?:\s*(?<!\\)(?:\")\s*)(?=$))",
        encode_alias,
        alias,
    ).strip()

    aliases = []
    if not alias:
        return aliases

    if split:
        iterate = re.split(r"\s*&&\s*", alias)
    else:
        iterate = [alias]

    for a in iterate:
        a = re.sub("\x1AU(.+?)\x1AU", decode_alias, a)
        if a[0] == a[-1] == '"':
            a = a[1:-1]
        aliases.append(a)

    return aliases


def normalize_alias(alias, message=""):
    aliases = parse_alias(alias)
    contents = parse_alias(message, split=False)

    final_aliases = []
    for a, content in zip_longest(aliases, contents):
        if a is None:
            break

        if content:
            final_aliases.append(f"{a} {content}")
        else:
            final_aliases.append(a)

    return final_aliases


def format_description(i, names):
    return "\n".join(
        ": ".join((str(a + i * 15), b))
        for a, b in enumerate(takewhile(lambda x: x is not None, names), start=1)
    )


def trigger_typing(func):
    @functools.wraps(func)
    async def wrapper(self, ctx: commands.Context, *args, **kwargs):
        await ctx.trigger_typing()
        return await func(self, ctx, *args, **kwargs)

    return wrapper


def escape_code_block(text):
    return re.sub(r"```", "`\u200b``", text)


def format_channel_name(bot, author, exclude_channel=None, force_null=False):
    """Sanitises a username for use with text channel names"""
    guild = bot.modmail_guild

    if force_null:
        name = new_name = "null"
    else:
        if bot.config["use_user_id_channel_name"]:
            name = new_name = str(author.id)
        else:
            name = author.name.lower()
            if force_null:
                name = "null"

            name = new_name = (
                "".join(
                    x for x in name if x not in string.punctuation and x.isprintable()
                )
                or "null"
            ) + f"-{author.discriminator}"

    counter = 1
    existed = set(c.name for c in guild.text_channels if c != exclude_channel)
    while new_name in existed:
        new_name = f"{name}_{counter}"  # multiple channels with same name
        counter += 1

    return new_name


def tryint(x):
    try:
        return int(x)
    except (ValueError, TypeError):
        return x
