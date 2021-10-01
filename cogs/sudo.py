from discord.ext import commands
from bot import ModmailBot

import contextlib
import io
import re
import time
import traceback
import sys
import discord
import asyncio
import aiohttp
import DiscordUtils


class Sudo(commands.Cog):
    """A cog for handling all superuser functionality. Only owner can use it"""

    def __init__(self, bot):
        self.bot: ModmailBot = bot
        self.exec_vars = {
            "bot": self.bot,
            "discord": discord,
            "aiohttp": aiohttp,
            "asyncio": asyncio,
        }

    async def execute_code(self, ctx: commands.Context, code: str):
        line_break = "\n"
        sout = io.StringIO()
        serr = io.StringIO()
        with contextlib.redirect_stdout(sout):
            with contextlib.redirect_stderr(serr):
                start_time = float("nan")
                try:
                    func = (
                        f"async def execcode(ctx, bot):{line_break}"
                        f'{line_break.join(((" " * 4) + line) for line in code.split(line_break))}'
                    )

                    start_time = time.monotonic()
                    exec(func, self.exec_vars.update(locals()))

                    result = await locals()["execcode"](ctx, ctx.bot)
                except BaseException as ex:
                    traceback.print_exc()
                    result = type(ex)
                finally:
                    exec_time = time.monotonic() - start_time

        return (
            sout.getvalue(),
            serr.getvalue(),
            result,
            exec_time,
            f'Python {sys.version.replace(line_break, " ")}',
        )

    @commands.command(aliases=["eval"], hidden=True)
    @commands.is_owner()
    async def exec(self, ctx: commands.Context, *, code: str):
        """Executes the given Python code"""
        code_block = re.findall(r"```([a-zA-Z0-9]*)\s([\s\S(^\\`{3})]*?)\s*```", code)
        code = code_block[0][1]

        sout, serr, result, exec_time, prog = await self.execute_code(ctx, code)

        line_break = "\n"
        desc = f"""---- {prog.replace(line_break, " ")} ----\n"""

        if sout and sout.strip():
            desc += f"- /dev/stdout:\n{sout}"
        if serr and serr.strip():
            desc += f"\n- /dev/stderr:\n{serr}"

        output = f"{desc}\n+ Returned `{result}` in approx {1000 * exec_time:,.2f}ms."

        pages = []
        content = enumerate(range(0, len(output), 2000), start=1)
        for i, x in content:
            em = discord.Embed(
                title=f"Output [Page {i} of {len(list(content)) + 1}]",
                description=f"```diff\n{output[x : x + 2000]}\n```",
                color=discord.Color.blue(),
            )
            pages.append(em)

        paginator = DiscordUtils.Pagination.AutoEmbedPaginator(ctx)
        return await paginator.run(pages)

    @commands.command(hidden=True)
    @commands.is_owner()
    async def load(self, ctx: commands.Context, *, cog):
        """Loads an extension"""
        try:
            self.bot.load_extension(f"cogs.{cog}")
            return await ctx.send(f"Loaded cog `{cog}`.")
        except commands.errors.ExtensionAlreadyLoaded:
            return await ctx.send(f"Cog `{cog}` has already been loaded.")
        except commands.errors.ExtensionNotFound:
            return await ctx.send(f"Cog `{cog}` was not found.")

    @commands.command(hidden=True)
    @commands.is_owner()
    async def unload(self, ctx: commands.context, *, cog):
        """Unloads an extension"""
        try:
            self.bot.unload_extension(f"cogs.{cog}")
            return await ctx.send(f"Unloaded cog `{cog}`.")
        except commands.errors.ExtensionNotLoaded:
            return await ctx.send(f"Cog `{cog}` has not been loaded yet.")

    @commands.command(aliases=["quit"], hidden=True)
    @commands.is_owner()
    async def shutdown(self, ctx: commands.Context):
        await ctx.send("Shutting down...")
        await self.bot.logout()

    @commands.command(hidden=True)
    @commands.is_owner()
    async def reload(self, ctx: commands.Context):
        for x in self.bot.config["cogs"]:
            try:
                self.bot.unload_extension(f"cogs.{x}")
            except Exception:
                pass
        self.bot._load_cogs()
        return await ctx.send("Reload complete.")


def setup(bot: ModmailBot):
    bot.add_cog(Sudo(bot))
