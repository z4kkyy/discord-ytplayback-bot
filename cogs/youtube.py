""""
Copyright © Krypton 2019-2023 - https://github.com/kkrypt0nn (https://krypton.ninja)

Version: 6.1.0

Modified by Y.Ozaki - https://github.com/mttk1528
"""

import os
import subprocess
# from datetime import datetime
from queue import Queue
from collections import defaultdict

import discord
from discord.ext import commands
from discord.ext.commands import Context


class Youtube(commands.Cog, name="youtube"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.server_to_queue = defaultdict(Queue)  # server_id: queue.Queue
        self.server_to_voice_client = defaultdict(lambda: None)  # server_id: voice_client
        self.server_to_play_status = defaultdict(lambda: None)  # server_id: play_status
        self.voice_client = None
        self.text_input_channel = None

    def fetch_video(self, link: str) -> str:
        """
        This is a testing command that does nothing.

        :param link: The link to the YouTube video.
        """
        command = [
            "yt-dlp",
            "--extract-audio",
            "--audio-format", "wav",
            "--paths", os.path.join(os.getcwd(), "YTdownload"),
            "--format", "bestaudio",
            # "--max-downloads", "1",
            link,
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            self.bot.logger.info(f"Successfully downloaded a video: {link}")
        else:
            self.bot.logger.error(f"Failed to download a video: {link}")
        print(result.stdout)
        filename = os.listdir(os.path.join(os.getcwd(), "YTdownload"))[0]
        return os.path.join(os.getcwd(), "YTdownload", filename)

    @commands.hybrid_command(
        name="play",
        description="Play the audio from the linked YouTube video.",
    )
    async def play(self, context: Context) -> None:
        """
        This is a testing command that does nothing.

        :param context: The application command context.
        """
        guild_id = context.guild.id
        if self.server_to_queue[guild_id].empty():
            await context.reply("Queue is empty.")
            return
        else:
            link = self.server_to_queue[guild_id].get()
            path = self.fetch_video(link)
            voice_client = self.server_to_voice_client[guild_id]
            if voice_client is None:
                await self.ytjoin(context)
            voice_client = self.server_to_voice_client[guild_id]
            voice_client.play(discord.FFmpegPCMAudio(path))

    @commands.hybrid_command(
        name="playnow",
        description="Play the audio from the linked YouTube video.",
    )
    async def playnow(self, context: Context, link: str) -> None:
        """
        This is a testing command that does nothing.

        :param context: The application command context.
        """
        download_dir = "./YTdownload"
        for filename in os.listdir(download_dir):
            file_path = os.path.join(download_dir, filename)
            os.remove(file_path)
        guild_id = context.guild.id
        await context.reply(f"Playing Now: {link}")
        path = self.fetch_video(link)
        if self.server_to_voice_client[guild_id] is None:
            await self.ytjoin(context)
        voice_client = self.server_to_voice_client[guild_id]
        ffmpeg_options = {
            'options': '-af "volume=0.1"'
        }
        voice_client.play(discord.FFmpegPCMAudio(path, **ffmpeg_options))

    @commands.hybrid_command(
        name="stop",
        description="Play the audio from the linked YouTube video.",
    )
    async def stop(self, context: Context) -> None:
        """
        This is a testing command that does nothing.

        :param context: The application command context.
        """
        guild_id = context.guild.id
        voice_client = self.server_to_voice_client[guild_id]
        voice_client.stop()
        await context.reply("Playback Stopped.")

    @commands.hybrid_command(
        name="queue",
        description="Play the audio from the linked YouTube video.",
    )
    async def queue(self, context: Context, link: str) -> None:
        """
        This is a testing command that does nothing.

        :param context: The application command context.
        """
        guild_id = context.guild.id
        self.server_to_queue[guild_id].put(link)

    @commands.hybrid_command(
        name="ytjoin",
        description="Joins a voice channel.",
    )
    async def ytjoin(self, context: Context) -> None:
        """
        Joins a voice channel.

        :param context: The application command context.
        """
        user = context.author
        guild_id = context.guild.id
        if user.voice is None:
            await context.reply("You are not connected to a voice channel.")
            return

        self.server_to_voice_client[guild_id] = await user.voice.channel.connect()
        latency = self.bot.latency * 1000
        embed = discord.Embed(
            title="YouTube Playback Bot",
            description=(f"Joined {user.voice.channel.mention} (Ping: {latency:.0f}ms)"),
            color=0x00FF00,
        )
        await context.reply(embed=embed)

    @commands.hybrid_command(
        name="ytleave",
        description="Leaves a voice channel.",
    )
    async def ytleave(self, context: Context) -> None:
        """
        Leaves a voice channel.

        :param context: The application command context.
        """
        voice_client = self.server_to_voice_client[context.guild.id]
        if voice_client is None:
            await context.reply("You are not connected to a voice channel.")
            return
        await context.reply(f"Leaving {context.channel.mention} 👋")
        await voice_client.disconnect()


async def setup(bot) -> None:
    await bot.add_cog(Youtube(bot))
