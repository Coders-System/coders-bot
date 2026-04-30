from discord.ext import commands, tasks
from bot import ModmailBot
from datetime import datetime

import discord
import os
import aiohttp


line_break = "\n"
meaningful_bool = lambda x: "Yes" if x else "No"

FEED_QUERY = """
query AnonymousFeed(
  $loggedIn: Boolean! = false
  $first: Int
  $after: String
  $ranking: Ranking
  $version: Int
  $filters: FiltersInput
) {
  page: anonymousFeed(
    first: $first
    after: $after
    ranking: $ranking
    version: $version
    filters: $filters
  ) {
    ...FeedPostConnection
  }
}
fragment FeedPostConnection on PostConnection {
  pageInfo {
    hasNextPage
    endCursor
  }
  edges {
    node {
      ...FeedPost
      ...UserPost @include(if: $loggedIn)
    }
  }
}
fragment FeedPost on Post {
  id
  title
  createdAt
  image
  readTime
  source {
    id
    name
    image
  }
  permalink
  numComments
  numUpvotes
  commentsPermalink
  scout {
    id
    name
    image
    username
  }
  author {
    id
    name
    image
    username
    permalink
  }
  trending
  tags
}
fragment UserPost on Post {
  read
  upvoted
  commented
  bookmarked
}
"""

interest_tags = [
    "tech-news",
    "architecture",
    "cloud",
    "microservices",
    "devops",
    "security",
    "linux",
    "backend",
    "frontend",
    "programming",
    "docker",
    "database",
    "devtools",
    "secops",
    "hardware",
    "jvm",
    "testing",
    "webdev",
    "javascript",
    "python",
    "c",
    "c++",
    "open-source",
    ".net",
    "android",
]

GQL_PAYLOAD = {
    "query": FEED_QUERY,
    "variables": {
        "version": 7,
        "ranking": "TIME",
        "first": 3,
        "loggedIn": False,
        "unreadOnly": False,
        "filters": {"includeTags": interest_tags},
    },
}

API_ENDPOINT = "https://app.daily.dev/api/graphql"


class News(commands.Cog):
    """A cog for sending daily dev news to a channel"""

    def __init__(self, bot: ModmailBot):
        self.bot = bot
        self.channel_id = os.environ["NEWS_CHANNEL_ID"]
        self.role_id = os.environ["NEWS_ROLE_ID"]

    @commands.Cog.listener()
    async def on_ready(self):
        self.post_news.start()

    @commands.command()
    async def ok(self, ctx: commands.Context):
        """Shows you this server's icon"""
        em = discord.Embed(title=f"{ctx.guild}'s Icon", color=ctx.author.color)
        em.set_image(url=ctx.guild.icon_url)
        em.timestamp = datetime.now().astimezone()

        return await ctx.send("ok")

    @tasks.loop(hours=24)
    async def post_news(self):
        today = datetime.now().strftime(r"%B %d, %Y")
        channel = self.bot.get_channel(int(self.channel_id))
        async with aiohttp.ClientSession() as session:
            async with session.post(API_ENDPOINT, json=GQL_PAYLOAD) as r:
                data = await r.json()

        await channel.send(
            f"<@&{self.role_id}> Today is **{today}**\nHere's the latest tech news for you, all from our curated list \\:)"
        )
        for entry in data["data"]["page"]["edges"]:
            post = entry["node"]
            embed = discord.Embed(
                title=post["title"],
                description=f"[Read Here]({post['permalink']})",
                color=0x00B3FF,
            )
            embed.set_author(
                name=post["source"]["name"], icon_url=post["source"]["image"]
            )
            embed.add_field(
                name="Time needed", value=f"{post['readTime']} minutes", inline=False
            )
            embed.set_image(url=post["image"])
            embed.set_footer(
                text="Powered by daily.dev",
                icon_url="https://assets.website-files.com/5e0a5d9d743608d0f3ea6753/5f350958935a5ccf103429ce_daily.dev%20-%2032.png",
            )

            await channel.send(embed=embed)


def setup(bot: ModmailBot):
    bot.add_cog(News(bot))
