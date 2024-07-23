""""
Copyright © Krypton 2019-2023 - https://github.com/kkrypt0nn (https://krypton.ninja)

Version: 6.1.0

Modified by z4kky - https://github.com/z4kkyy
"""

import asyncio
import os
import re
import subprocess
import time
from collections import defaultdict, deque
# from datetime import datetime
from typing import Union

import discord
from discord.ext import commands
from discord.ext.commands import Context
# from pydrive2.auth import GoogleAuth
# from pydrive2.drive import GoogleDrive

from tqdm import tqdm
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


class AsyncioDequeQueue:
    def __init__(self):
        self.queue = deque()
        self._get_event = asyncio.Event()

    async def put_front(self, item):
        self.queue.appendleft(item)
        self._get_event.set()

    async def get(self):
        while not self.queue:
            self._get_event.clear()
            await self._get_event.wait()
        return self.queue.popleft()

    async def put(self, item):
        self.queue.append(item)
        self._get_event.set()


# using yt-dlp (https://github.com/yt-dlp/yt-dlp)
class YouTube(commands.Cog, name="youtube"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.server_to_queue = defaultdict(AsyncioDequeQueue)
        self.server_to_voice_client = defaultdict(lambda: None)
        self.server_to_if_playnow = defaultdict(lambda: False)
        self.server_to_current_song_info = defaultdict(lambda: None)
        self.server_to_current_loop_status = defaultdict(lambda: True)
        self.server_to_expected_disconnection = defaultdict(lambda: False)
        self.server_to_timestamps = defaultdict(lambda: 0)
        self.server_to_timestamp_task = defaultdict(lambda: None)

        self.download_dir = os.path.join(os.getcwd(), "dldata/yt-dlp-download")
        self.video_download_dir = os.path.join(os.getcwd(), "dldata/yt-dlp-video-download")
        self.download_archive_path = os.path.join(os.getcwd(), "dldata/yt-dlp-download-archive.txt")

        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir)
        if not os.path.exists(self.download_archive_path):
            with open(self.download_archive_path, "w") as _:
                pass

        # gauth = GoogleAuth()
        # gauth.LocalWebserverAuth()

        # self.drive = GoogleDrive(gauth)

    def _create_after_callback(self, guild_id: int, context: Context) -> callable:
        def after_callback(error):
            async def play_again():
                if error:
                    self.bot.logger.error(f"Failed to play the audio: {error}")

                if self.server_to_current_loop_status[guild_id]:
                    self.bot.logger.info(f"Loop status is True. Playing again. (guild id: {guild_id})")
                    file_path = self.server_to_current_song_info[guild_id]['path']
                    ffmpeg_options = {
                        "options": "-af 'volume=0.1' -vn -ac 2",
                        "stderr": subprocess.DEVNULL,
                    }
                    self.server_to_voice_client[guild_id].play(
                        discord.FFmpegPCMAudio(file_path, **ffmpeg_options),
                        after=self._create_after_callback(guild_id, context)
                    )
                    self.server_to_current_song_info[guild_id]['start_time'] = discord.utils.utcnow()
                    self.bot.loop.create_task(self._start_timestamp_tracking(guild_id))
                elif self.server_to_queue[guild_id].queue:
                    self.bot.logger.info(f"Queue is not empty. Playing next song. (guild id: {guild_id})")
                    await self._play_next(guild_id, context)
                else:
                    self.bot.logger.info(f"Loop is off and queue is empty. Stopping playback. (guild id: {guild_id})")
                    self.server_to_if_playnow[guild_id] = False
                    self.server_to_current_song_info[guild_id] = None
                    self.server_to_timestamps[guild_id] = 0
                    if context:
                        embed = discord.Embed(
                            description="Playback finished. The queue is now empty.", color=0xE02B2B
                        )
                        await context.send(embed=embed)

            asyncio.run_coroutine_threadsafe(play_again(), self.bot.loop)
        return after_callback

    async def _update_timestamp(self, guild_id: int) -> None:
        if guild_id in self.server_to_current_song_info:
            current_song = self.server_to_current_song_info[guild_id]
            if current_song is not None and "start_time" in current_song:
                elapsed = (discord.utils.utcnow() - current_song["start_time"]).total_seconds()
                self.server_to_timestamps[guild_id] = elapsed

    async def _start_timestamp_tracking(self, guild_id: int) -> None:
        try:
            while self.server_to_voice_client[guild_id] and self.server_to_voice_client[guild_id].is_playing():
                await self._update_timestamp(guild_id)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.bot.logger.error(f"Failed to track the timestamp: {e}")
        finally:
            self.server_to_timestamp_task[guild_id] = None

    def _fetch_video_sync(self, url: str) -> Union[tuple[str, str], None]:  # TODO: args += path
        """
        This command fetches the audio from the linked YouTube video synchronously.
        # Exclusively for theme setup.

        :param url: The url to the YouTube video.
        """
        output_template = "%(id)s.%(ext)s"  # original: "%(id)s-%(title)s.%(ext)s"

        command = [
            "yt-dlp",
            url,
            "--extract-audio",
            "--audio-format", "mp3",
            "--output", output_template,
            "--paths", self.theme_download_dir,  # exlusively for theme setup
            "--verbose",
            # "--write-pages",
            # "--print-traffic",
            "--download-archive", self.download_archive_path,
        ]

        self.bot.logger.info(f"[YouTube] [info] Start downloading {url}")
        # get data with sync process
        result = subprocess.run(command, capture_output=True, text=True)
        stdout, _ = result.stdout, result.stderr  # stdout, stderr
        if result.returncode == 0:
            self.bot.logger.info(f"[YouTube] [info] Successfully downloaded {url}")

            match_already_recorded = re.search(r'\[download\] (\w+): has already been recorded in the archive', stdout.strip())
            if match_already_recorded:
                file_id = match_already_recorded.group(1)
                file_path = os.path.join(self.theme_download_dir, file_id + ".mp3")
                return file_id, file_path
            else:
                for line in stdout.split("\n"):
                    if len(line) == 0:
                        continue
                    self.bot.logger.info(f"[YouTube] {line}")
                    match_file_path = re.search(r'\[ExtractAudio\] Destination: (.+)', line)

                    if match_file_path:
                        file_path = match_file_path.group(1)
                        file_id = file_path.split("/")[-1].split(".")[0]
                        # print("file_id:", file_id)
                        # print("file_path:", file_path)
                        return file_id, file_path
        else:
            self.bot.logger.error(f"[YouTube] [info] Failed to download {url}")
            return None

    async def _fetch_video_async(self, url: str) -> Union[tuple[str, str], None]:  # file_id, file_path
        """
        This command fetches the audio from the linked YouTube video asynchronously.

        :param url: The url to the YouTube video.
        """
        output_template = "%(id)s.%(ext)s"  # original: "%(id)s-%(title)s.%
        # async process
        # filename = await self._fetch_file_name(url, output_template)
        self.bot.logger.info(f"[YouTube] [info] Start downloading {url}")

        command = [
            "yt-dlp",
            url,
            "--extract-audio",
            "--audio-format", "mp3",
            "--output", output_template,
            "--paths", self.download_dir,
            "--verbose",
            # "--write-pages",
            # "--print-traffic",
            "--download-archive", self.download_archive_path,
        ]

        # process as a coroutine
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            # wait for the subprocess to finish
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
            stdout, stderr = stdout.decode().strip(), stderr.decode().strip()
            stdout = stdout.replace("\r", "\n").strip()

            if process.returncode == 0:
                self.bot.logger.info(f"[YouTube] [info] Successfully downloaded {url}")
                match_already_recorded = re.search(r'\[download\] ([\w-]+): has already been recorded in the archive', stdout)
                if match_already_recorded:
                    self.bot.logger.info("[YouTube] audio already downloaded in the archive.")
                    file_id = match_already_recorded.group(1)
                    file_path = os.path.join(self.download_dir, file_id + ".mp3")
                else:
                    for line in stdout.split("\n"):
                        if len(line) == 0:
                            continue
                        self.bot.logger.info(f"[YouTube] {line}")
                        match_file_path = re.search(r'\[ExtractAudio\] Destination: (.+)', line)

                        if match_file_path:
                            file_path = match_file_path.group(1)
                            file_id = file_path.split("/")[-1].split(".")[0]
                            # print("file_id:", file_id)
                            # print("file_path:", file_path)
                return file_id, file_path
            else:
                self.bot.logger.error(f"Failed to download a video: <{url}>")
                print("STDOUT:")
                print(stdout)
                return None

        except asyncio.TimeoutError:
            self.bot.logger.error(f"Failed to download a video: <{url}> (Timeout)")
            process.kill()
            return None

    # Currently not used
    async def _async_fetch_playlist(self, url: str) -> str:
        """
        This command fetches the audio from the linked YouTube playlist asynchronously.

        :param url: The url to the YouTube playlist.
        """
        # output_template = "%(id)s.%(ext)s"  # original: "%(id)s-%(title)s.%
        # async process
        # filename = await self._fetch_file_name(url, output_template)
        self.bot.logger.info(f"[YouTube] [info] Start downloading {url}")

        command = [
            "yt-dlp",
            url,
            "--yes-playlist",
            "--extract-audio",
            "--audio-format", "mp3",
            "--output", os.path.join(self.download_dir, "%(playlist_index)s-%(title)s.%(ext)s"),
            "--ignore-errors"
        ]

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        # output_template = "%(id)s.%(ext)s"
        if process.returncode == 0:
            # Combine audio files into one
            combined_file_path = os.path.join(self.download_dir, "combined.mp3")
            files = sorted([os.path.join(self.download_dir, f) for f in os.listdir(self.download_dir) if f.endswith('.mp3')])
            ffmpeg_concat_cmd = ["ffmpeg", "-y", "-safe", "0", "-f", "concat", "-i"]
            with open(os.path.join(self.download_dir, "filelist.txt"), "w") as filelist:
                filelist.writelines(f"file '{file}'\n" for file in files)

            ffmpeg_concat_cmd.append(os.path.join(self.download_dir, "filelist.txt"))
            ffmpeg_concat_cmd.extend(["-c", "copy", combined_file_path])

            concat_process = await asyncio.create_subprocess_exec(
                *ffmpeg_concat_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await concat_process.communicate()
            file_id = combined_file_path.split("/")[-1].split(".")[0]
            self.bot.logger.info(f"combined_file_path = {combined_file_path}")
            return file_id, combined_file_path
        else:
            return ""

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after) -> None:
        """
        Handles the on_voice_state_update event.
        This function is used to detect unexpected disconnection of the bot from a voice channel.

        :param member: The member who updated their voice state.
        :param before: The voice state before the update.
        :param after: The voice state after the update.
        """
        if member.id == self.bot.user.id:
            guild_id = member.guild.id
            if before.channel is not None and after.channel is None:
                if self.server_to_expected_disconnection[guild_id]:
                    self.bot.logger.info(f"Detected normal disconnection. (guild id: {guild_id})")
                    self.server_to_expected_disconnection[guild_id] = False
                else:
                    self.bot.logger.info(f"Detected unexpected disconnection. (guild id: {guild_id})")
                    # reconnect to the voice channel
                    try:
                        self.server_to_voice_client[guild_id] = await before.channel.connect()
                        self.bot.logger.info("Reconnected to the voice channel.")
                    except Exception as e:
                        self.bot.logger.error(f"Failed to reconnect to the voice channel: {str(e)}")

                    if self.server_to_if_playnow[guild_id] is False:
                        return

                    if self.server_to_current_song_info[guild_id]:
                        await self._resume_playback(guild_id)

    async def _resume_playback(self, guild_id: int) -> None:
        current_song = self.server_to_current_song_info[guild_id]
        if current_song:
            file_path = current_song['path']
            ffmpeg_options = {
                "options": f"-af 'volume=0.1' -vn -ac 2 -ss {self.server_to_timestamps[guild_id]}",
                "stderr": subprocess.DEVNULL,
            }
            self.server_to_voice_client[guild_id].play(
                discord.FFmpegPCMAudio(file_path, **ffmpeg_options),
                after=self._create_after_callback(guild_id, None)
            )
            if self.server_to_current_loop_status[guild_id]:
                self.bot.logger.info(f"Resuming playback with loop status: on (guild id: {guild_id})")
            else:
                self.bot.logger.info(f"Resuming playback with loop status: off (guild id: {guild_id})")

    async def _play_next(self, guild_id: int, context: Context) -> None:
        if not self.server_to_queue[guild_id].queue:
            embed = discord.Embed(description="The queue is now empty.", color=0xE02B2B)
            await context.send(embed=embed)
            self.server_to_if_playnow[guild_id] = False
            self.server_to_current_song_info[guild_id] = None
            self.server_to_timestamps[guild_id] = 0
            return

        if self.server_to_voice_client[guild_id].is_playing():
            self.bot.logger.info(f"function _play_next is called while playing. (guild id: {guild_id}) ")
            return

        if self.server_to_timestamp_task[guild_id]:
            self.server_to_timestamp_task[guild_id].cancel()

        self.server_to_timestamp_task[guild_id] = await self._start_timestamp_tracking(guild_id)

        next_song_info = await self.server_to_queue[guild_id].get()
        self.server_to_current_song_info[guild_id] = next_song_info
        self.server_to_current_song_info[guild_id]['start_time'] = discord.utils.utcnow()

        ffmpeg_options = {
            "options": "-af 'volume=0.1' -vn -ac 2",
            "stderr": subprocess.DEVNULL,
        }
        self.server_to_voice_client[guild_id].play(
            discord.FFmpegPCMAudio(next_song_info['path'], **ffmpeg_options),
            after=self._create_after_callback(guild_id, context)
        )
        await self._start_timestamp_tracking(guild_id)

        embed = discord.Embed(
            description=f"Playing Now: <{next_song_info['url']}>\nLoop status: {self.server_to_current_loop_status[guild_id]}",
            color=0xE02B2B
        )
        await context.send(embed=embed)

    def _format_time(self, seconds: int) -> str:
        minutes, seconds = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    # async def _clean_up_files(self, guild_id: int) -> None:
    #     current_song = self.server_to_current_song_info.get(guild_id)
    #     if current_song:
    #         try:
    #             os.remove(current_song['path'])
    #         except OSError:
    #             pass

    #     for song in self.server_to_queue[guild_id].queue:
    #         try:
    #             os.remove(song['path'])
    #         except OSError:
    #             pass

    @commands.hybrid_command(
        name="playnow",
        description="Play the audio from the linked YouTube video.",
    )
    async def playnow(self, context: Context, url: str) -> None:
        """
        This command plays the audio from the linked YouTube video, regardless of the queue.

        :param context: The application command context.
        """
        guild_id = context.guild.id
        self.bot.logger.info(f"self.server_to_if_playnow[guild_id] is {self.server_to_if_playnow[guild_id]}.")

        # join the voice channel if not joined
        if self.server_to_voice_client[guild_id] is None or not self.server_to_voice_client[guild_id].is_connected():
            await self.ytjoin(context)

        # fetch the audio
        url = url.strip()
        await context.reply(f"Playing Now: {url} Start downloading...")
        result = await self._fetch_video_async(url)
        if result is not None:
            file_id, file_path = result
        else:
            embed = discord.Embed(
                description="Failed to fetch the audio.", color=0xE02B2B
            )
            await context.reply(embed=embed)
            return

        # play
        ffmpeg_options = {
            "options": "-af 'volume=0.1' -vn -ac 2",
            "stderr": subprocess.DEVNULL,
        }
        self.server_to_current_song_info[guild_id] = {
            'url': url,
            'id': file_id,
            'path': file_path,
            'start_time': discord.utils.utcnow()
        }

        # stop if playing
        if self.server_to_voice_client[guild_id].is_playing():
            self.bot.logger.info(f"Stopping currently playing audio. (guild id: {guild_id}) ")
            self.server_to_voice_client[guild_id].stop()

        # play the audio
        self.server_to_if_playnow[context.guild.id] = True
        self.server_to_voice_client[guild_id].play(
            discord.FFmpegPCMAudio(file_path, **ffmpeg_options),
            after=self._create_after_callback(guild_id, context)
        )
        self.bot.loop.create_task(self._start_timestamp_tracking(guild_id))

        embed = discord.Embed(
            description=f"Playing Now: <{url}>\nLoop status: {self.server_to_current_loop_status[guild_id]}", color=0xE02B2B
        )

        await context.send(embed=embed)

    @commands.hybrid_command(
        name="add",
        description="Add the audio from the linked YouTube video to the queue.",
    )
    async def add(self, context: Context, url: str) -> None:
        """
        This command adds the audio from the linked YouTube video to the queue.

        :param context: The application command context.
        """
        guild_id = context.guild.id
        url = url.strip()
        await context.reply(f"Adding to the queue: {url} Start downloading...")

        result = await self._fetch_video_async(url)

        if result is not None:
            file_id, file_path = result
            song_info = {
                'url': url,
                'id': file_id,
                'path': file_path,
            }
            await self.server_to_queue[guild_id].put(song_info)
            await context.send(f"Added to the queue: <{url}>")

            if not self.server_to_voice_client[guild_id] or not self.server_to_voice_client[guild_id].is_connected():
                await self.ytjoin(context)

            if not self.server_to_voice_client[guild_id].is_playing():
                await self._play_next(guild_id, context)
        else:
            embed = discord.Embed(
                description="Failed to fetch the audio.", color=0xE02B2B
            )
            await context.reply(embed=embed)

    @commands.hybrid_command(
        name="queue",
        description="Show the queue.",
    )
    async def queue(self, context: Context) -> None:
        """
        This command shows the queue.

        :param context: The application command context.
        """
        guild_id = context.guild.id
        queue = self.server_to_queue[guild_id]
        if queue.queue:
            queue_list = [f"{i + 1}. {song['url']}" for i, song in enumerate(queue.queue)]
            embed = discord.Embed(
                title="Queue",
                description="\n".join(queue_list),
                color=0xE02B2B
            )
            await context.send(embed=embed)
        else:
            await context.send("The queue is empty.")

    @commands.hybrid_command(
        name="skip",
        description="Skip the current audio.",
    )
    async def skip(self, context: Context) -> None:
        """
        This command skips the current audio.

        :param context: The application command context.
        """
        guild_id = context.guild.id
        if self.server_to_voice_client[guild_id].is_playing():
            self.server_to_voice_client[guild_id].stop()

            if self.server_to_timestamp_task[guild_id]:
                self.server_to_timestamp_task[guild_id].cancel()
                self.server_to_timestamp_task[guild_id] = None

            self.server_to_current_song_info[guild_id] = None
            self.server_to_timestamps[guild_id] = 0
            embed = discord.Embed(description="Skipped the current audio.", color=0xE02B2B)

            await context.send(embed=embed)
            await self._play_next(guild_id, context)
        else:
            await context.send("No audio is currently playing.")

    @commands.hybrid_command(
        name="stop",
        description="Stop playing the audio and reset the queue.",
    )
    async def stop(self, context: Context) -> None:
        """
        This command stops playing the audio.

        :param context: The application command context.
        """
        guild_id = context.guild.id
        self.server_to_current_loop_status[guild_id] = False
        self.server_to_if_playnow[guild_id] = False

        if self.server_to_voice_client[guild_id].is_playing():
            self.server_to_voice_client[guild_id].stop()
            self.server_to_current_song_info[guild_id] = None
            self.server_to_timestamps[guild_id] = 0
        self.server_to_queue[guild_id] = AsyncioDequeQueue()
        embed = discord.Embed(
            description="Playback stopped and queue cleared.", color=0xE02B2B
        )
        await context.reply(embed=embed)
        return

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
        if_send_embed = True

        if user.voice is None:
            embed = discord.Embed(
                description="You are not connected to a voice channel.", color=0xE02B2B
            )
            await context.reply(embed=embed)
            return
        if self.server_to_voice_client[context.guild.id] is not None:
            if self.server_to_voice_client[context.guild.id].is_connected() is True:
                self.server_to_expected_disconnection[context.guild.id] = True
                await self.server_to_voice_client[context.guild.id].disconnect()
                self.server_to_voice_client[context.guild.id] = None
                if_send_embed = False

        self.server_to_voice_client[context.guild.id] = await user.voice.channel.connect()
        latency = self.bot.latency * 1000
        if if_send_embed:
            embed = discord.Embed(
                title="YouTube Playback Bot",
                description=(f"Joined {user.voice.channel.mention} (Ping: {latency:.0f}ms)"),
                color=0x00FF00,
            )
            await context.reply(embed=embed)
        else:
            embed = discord.Embed(
                description="Reconnected to the voice channel.", color=0xE02B2B
            )
            await context.send(embed=embed)

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
        self.server_to_if_playnow[context.guild.id] = False
        if voice_client is None:
            embed = discord.Embed(
                description="Youtube Playback Bot is not connected to a voice channel.", color=0xE02B2B
            )
            await context.reply(embed=embed)
            return
        else:
            embed = discord.Embed(
                description=f"Leaving {voice_client.channel.mention} 👋", color=0xE02B2B
            )
            await context.send(embed=embed)
            self.server_to_expected_disconnection[context.guild.id] = True
            await voice_client.disconnect()
            self.server_to_voice_client[context.guild.id] = None

    @commands.hybrid_command(
        name="loop",
        description="Loop the audio.",
    )
    async def loop(self, context: Context) -> None:
        """
        This is a testing command that does nothing.

        :param context: The application command context.
        """
        guild_id = context.guild.id
        self.server_to_current_loop_status[guild_id] = not self.server_to_current_loop_status[guild_id]
        status = "on" if self.server_to_current_loop_status[guild_id] else "off"
        embed = discord.Embed(
            description=f"Loop status: {status}", color=0xE02B2B
        )
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="nowplaying",
        description="Show information about the currently playing audio.",
    )
    async def nowplaying(self, context: Context) -> None:
        """
        This command shows information about the currently playing audio.

        :param context: The application command context.
        """
        guild_id = context.guild.id
        song_info = self.server_to_current_song_info[guild_id]

        if song_info:
            elapsed = self.server_to_timestamps.get(guild_id, 0)
            embed = discord.Embed(
                title="Now Playing",
                description=f"URL: {song_info['url']}\nElapsed time: {self._format_time(elapsed)}",
                color=0xE02B2B
            )
            await context.send(embed=embed)
        else:
            embed = discord.Embed(
                description="No audio is currently playing.", color=0xE02B2B
            )
            await context.send(embed=embed)

    @commands.hybrid_command(
        name="ythelp",
        description="ヘルプを表示します。/ Display help for YouTube cog.",
    )
    async def ythelp(self, context: Context, language: str = "jp") -> None:
        """
        このコマンドは全コマンドの説明を表示します。
        言語パラメータで英語(en)または日本語(jp)を指定できます。

        This command displays descriptions for all commands in the YouTube cog.
        The language parameter can be set to English (en) or Japanese (jp).

        :param context: アプリケーションコマンドのコンテキスト / The application command context.
        :param language: ヘルプを表示する言語("en" または "jp")/ The language to display help in ("en" or "jp").
        """
        if language.lower() not in ["en", "jp"]:
            await context.send("Invalid language. Please use 'en' for English or 'jp' for Japanese.")
            return

        help_text = {
            "en": {
                "playnow": "Play the audio from the specified YouTube video immediately.",
                "add": "Add the audio from the specified YouTube video to the queue.",
                "queue": "Display the current queue.",
                "skip": "Skip the currently playing audio.",
                "stop": "Stop playing audio and reset the queue.",
                "ytjoin": "Join a voice channel.",
                "ytleave": "Leave the voice channel.",
                "loop": "Toggle loop mode for the current audio.",
                "nowplaying": "Show information about the currently playing audio.",
            },
            "jp": {
                "playnow": "YouTubeのURLで指定した曲を再生します。",
                "add": "YouTubeのURLで指定した動画を再生リストに追加します。",
                "queue": "現在の再生リストを表示します。",
                "skip": "今流れている曲をスキップして次の曲に進みます。",
                "stop": "曲の再生を停止し、再生リストを空にします。",
                "ytjoin": "ボットがボイスチャンネルに入室します。",
                "ytleave": "ボットがボイスチャンネルから退出します。",
                "loop": "今の曲をループ再生するかどうかを切り替えます。",
                "nowplaying": "現在再生中の曲の情報を表示します。",
            }
        }

        title = "YouTube Playback Bot"
        description = "List of available commands:" if language == "en" else "利用可能なコマンド一覧:"

        embed = discord.Embed(title=title, description=description, color=0xE02B2B)

        for command, description in help_text[language].items():
            embed.add_field(name=f"/{command}", value=description, inline=False)

        await context.send(embed=embed)

    async def _fetch_raw_video_async(self, url: str):
        """
        This method downloads the YouTube video asynchronously.

        :param url: The url to the YouTube video.
        """
        self.bot.logger.info(f"[YouTube] [info] Start downloading {url}")

        command = [
            "yt-dlp",
            url,
            "--output", "%(id)s.%(ext)s",
            "--format", "bestvideo+bestaudio/best",  # Changed to 'best' for standard quality
            "--paths", self.video_download_dir,
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--no-keep-video",
            "--no-part",
            "--postprocessor-args", "-c:v copy -c:a aac -b:a 192k"
        ]

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
            stdout, stderr = stdout.decode().strip(), stderr.decode().strip()

            print(stdout)

            if process.returncode == 0:
                self.bot.logger.info(f"[YouTube] [info] Successfully downloaded {url}")

                for line in stdout.split("\n"):
                    if len(line) == 0:
                        continue
                    self.bot.logger.info(f"[YouTube] {line}")
                    match_file_path = re.search(r'\[Merger\] Merging formats into(.+)', line)

                    if match_file_path:
                        file_path = match_file_path.group(1)[:-1]
                        file_path = os.path.join(self.video_download_dir, os.path.basename(file_path))
                        return file_path
            else:
                self.bot.logger.error(f"Failed to download video: <{url}>")
                self.bot.logger.error(f"Error: {stderr}")
                return None

        except asyncio.TimeoutError:
            self.bot.logger.error(f"Timeout while downloading video: <{url}>")
            process.kill()
            return None
        except Exception as e:
            self.bot.logger.error(f"Unexpected error while downloading video <{url}>: {str(e)}")
            return None

    @commands.hybrid_command(
        name="ytdownload",
        description="Download a YouTube video and upload it to Google Drive.",
    )
    async def ytdownload(self, context: commands.Context, url: str) -> None:
        try:
            await context.defer()
            user = context.author

            url = url.strip()
            await context.send("Start downloading...")
            result = await self._fetch_raw_video_async(url)
            if result is None:
                embed = discord.Embed(
                    description="Failed to fetch the video.", color=0xE02B2B
                )
                await context.send(embed=embed)
                return

            file_path = result
            # print(f"file path: {file_path}")

            # Upload to GigaFile
            share_link = await self.upload_to_gigafile_async(file_path)

            # Now it's safe to remove the file
            await asyncio.to_thread(os.remove, file_path)

            # send the file to the user
            embed = discord.Embed(
                title="YouTube Download Success",
                description="The video has been successfully downloaded and uploaded.",
                color=0x00FF00
            )
            embed.add_field(name="Original URL", value=url, inline=False)
            embed.add_field(name="Download Link", value=share_link, inline=False)

            try:
                await user.send(embed=embed)
                await context.send("Download and upload completed. Check your DM for details.")
            except discord.Forbidden:
                await context.send("Download and upload completed, but I couldn't send you a DM with the details. Please check your privacy settings.")
            except Exception as e:
                self.bot.logger.error(f"Error sending DM to user {user.id}: {str(e)}")
                await context.send("Download and upload completed, but there was an error sending you the details. Please contact an administrator.")

        except discord.errors.NotFound:
            await context.send("The interaction expired. Please try the command again.")
        except Exception as e:
            self.bot.logger.error(f"Error in ytdownload command: {str(e)}")
            await context.send("An error occurred while processing your request. Please try again later.")

    def upload_to_gigafile(self, file_path: str, lifetime: int = 100) -> str:
        """
        This method uploads a file to GigaFile and returns the download link.

        :param file_path: The path to the file to upload.
        :param lifetime: The lifetime of the file on GigaFile (in days).
        :return: The download link for the uploaded file.
        """
        file_paths = [file_path]  # for compatibility with the existing method
        options = Options()
        options.add_argument("--headless")
        options.page_load_strategy = 'eager'

        driver = webdriver.Chrome(options=options)

        driver.get("https://gigafile.nu/")

        valid_lifetimes = [3, 5, 7, 14, 30, 60, 100]
        if lifetime in valid_lifetimes:
            lifetime_selector = f"li[data-lifetime-val='{lifetime}']"
            element = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.CSS_SELECTOR, lifetime_selector)))

            driver.execute_script("arguments[0].click();", element)
        else:
            print(f"無効な保存期限: {lifetime}。有効な値: {valid_lifetimes}")

        file_input = driver.find_element(By.CSS_SELECTOR, "#upload_panel_button > input")
        file_input.send_keys("\n".join(file_paths))

        progress_bar = tqdm(total=100, desc="Overall Progress", unit="%")

        while True:
            progress_texts = []
            for i in range(len(file_paths)):
                progress_texts.append(driver.find_element(By.CSS_SELECTOR, f"#file_{i} > div.file_info_prog_box > span").text)

            if all(text == "完了！" for text in progress_texts):
                progress_bar.n = 100
                progress_bar.update(0)
                break

            overall_progress = 0
            for text in progress_texts:
                if text != "完了！":
                    percentage = ''.join(filter(str.isdigit, text))
                    if percentage:
                        overall_progress += int(percentage)
                    else:
                        overall_progress += 99

            overall_progress /= len(file_paths)
            progress_bar.n = min(overall_progress, 99)
            progress_bar.update(0)

            time.sleep(1)

        progress_bar.close()

        matomete_link_btn = driver.find_element(By.ID, "matomete_btn")
        driver.execute_script("arguments[0].click();", matomete_link_btn)

        try:
            WebDriverWait(driver, 10).until(EC.alert_is_present(),
                                        'Timed out waiting for PA creation ' +  # noqa: E128, W504
                                        'confirmation popup to appear.')  # noqa: E128

            alert = driver.switch_to.alert
            alert.accept()

        except TimeoutException:
            pass

        matomete_url_element = driver.find_element(By.ID, "matomete_url")
        origin_value = matomete_url_element.get_attribute("origin")

        driver.quit()

        return origin_value

    async def upload_to_gigafile_async(self, file_path: str, lifetime: int = 100) -> str:
        return await asyncio.to_thread(self.upload_to_gigafile, file_path, lifetime)


async def setup(bot) -> None:
    await bot.add_cog(YouTube(bot))
