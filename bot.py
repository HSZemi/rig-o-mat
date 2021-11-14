#! /usr/bin/env python3
import asyncio
import json
import os
import random
import textwrap
from dataclasses import dataclass, asdict, field
import time
from pathlib import Path
from typing import List, Dict, Optional

from discord import Message, Role, User, Guild, Member
from discord.ext.commands import Bot, command, Cog, Context
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
DEFAULT_RIGGING_MESSAGE = 'Time to rig some people in! React with 🎉 to participate! Ends: %t'


@dataclass
class RiggingConfig:
    channel: str = None
    duration: int = 120
    winner_role: str = None
    admin_role: str = None
    message: str = DEFAULT_RIGGING_MESSAGE

    def needs_configuration(self):
        return self.channel is None or self.winner_role is None


@dataclass
class RiggingProperties:
    message_id: str = None
    winners: List[str] = field(default_factory=list)
    winners_count: int = 0
    end_time: int = 0


class RigBot(Bot):
    def __init__(self):
        super().__init__(command_prefix="!")

    async def on_ready(self):
        guild_list = '\n'.join([f'{guild.name}(id: {guild.id})' for guild in self.guilds])
        print(f'{self.user} is connected to the following guilds:\n{guild_list}')


class Rigging(Cog):

    def __init__(self, bot: Bot):
        self.bot = bot
        self.rigging: Dict[int, Optional[RiggingProperties]] = {}
        self.config: Dict[int, RiggingConfig] = {}
        self.rigging_path = Path(__file__).with_name('rigging.json')
        self.config_path = Path(__file__).with_name('config.json')
        self.load_config()
        self.load_rigging()

    def load_config(self):
        if self.config_path.is_file():
            try:
                config_content = json.loads(self.config_path.read_text())
                loaded_config = {}
                for key in config_content:
                    loaded_config[int(key)] = RiggingConfig(**config_content[key])
                self.config = loaded_config
                print(f'Loaded config: {self.config}')
            except Exception as e:
                print(f'Could not load config: {e}')

    def load_rigging(self):
        if self.rigging_path.is_file():
            try:
                rigging_content = json.loads(self.rigging_path.read_text())
                loaded_rigging = {}
                for key in rigging_content:
                    loaded_rigging[int(key)] = RiggingProperties(**rigging_content[key])
                self.rigging = loaded_rigging
                print(f'Loaded rigging: {self.rigging}')
            except Exception as e:
                print(f'Could not load rigging: {e}')

    def save_config(self):
        print(f'Saving new config: {self.config}')
        config_dump = {}
        for key in self.config:
            config_dump[key] = asdict(self.config[key])
        self.config_path.write_text(json.dumps(config_dump))

    def save_rigging(self):
        print(f'Saving new rigging: {self.rigging}')
        rigging_dump = {}
        for key in self.rigging:
            if self.rigging[key]:
                rigging_dump[key] = asdict(self.rigging[key])
        self.rigging_path.write_text(json.dumps(rigging_dump))

    @command(name='rig')
    async def _rig(self, ctx: Context, *args):
        message: Message = ctx.message
        guild: Guild = ctx.guild
        author: User = message.author
        print(f'user:{author.name}\nargs:{args}\n-----')

        if guild.id not in self.config:
            self.config[guild.id] = RiggingConfig()

        if self.config[guild.id].admin_role:
            admin_role = await self.resolve_admin_role(guild)
            member = await guild.fetch_member(author.id)
            if admin_role not in member.roles:
                return

        if not len(args):
            await self.print_help(ctx)
            return

        if args[0] == 'config':
            await self.process_config(ctx, args[1:])
            return
        elif self.config[guild.id].needs_configuration():
            await ctx.send(f'Please fully configure the settings first.\nCurrent configuration:\n{self.config[guild.id]}')
            return

        if args[0] == 'cancel':
            await self.process_cancel(ctx)
            return

        if args[0] == 'cleanup':
            await self.process_cleanup(ctx)
            return

        try:
            amount = int(args[0])
            await self.rig_amount(ctx, amount, args[1:])
            return
        except ValueError as e:
            print(e)
            await ctx.send('Unknown command :frowning:')

    async def rig_amount(self, ctx, amount: int, args):
        guild = ctx.guild
        duration = self.config[guild.id].duration
        if len(args) > 0:
            if args[0].isnumeric():
                duration = int(args[0])
            elif args[0] == 'more':
                if guild.id not in self.rigging or not self.rigging[guild.id]:
                    await ctx.send("No ongoing rigging.")
                    return
                self.rigging[guild.id].winners_count += amount
                await self.pick_winners(guild)
                return
            else:
                await ctx.send(f'Unknown arguments :frowning:')
                return
        await self.cleanup_previous_riggings(guild)
        self.rigging[guild.id] = RiggingProperties()
        self.rigging[guild.id].winners_count = amount
        self.rigging[guild.id].end_time = int(time.time()) + duration
        channel_id = int(self.config[guild.id].channel[2:-1])
        channel = self.bot.get_channel(channel_id)
        message = await channel.send(self.get_initial_message(guild))
        self.rigging[guild.id].message_id = message.id
        await message.add_reaction('🎉')
        await ctx.send(
            f'Started a rigging in {self.config[guild.id].channel} for {amount} winners.\nDuration: {duration}s'
        )
        self.save_rigging()
        await asyncio.sleep(duration)
        await self.pick_winners(guild)

    def get_initial_message(self, guild):
        end_time = self.rigging[guild.id].end_time
        return self.config[guild.id].message.replace('%t', f'<t:{end_time}>')

    async def pick_winners(self, guild: Guild):
        if not self.rigging[guild.id]:
            return
        message = await self.get_rigging_message(guild)
        eligible_users = await self.get_eligible_users(guild, message)
        number_of_winners_to_pick = self.rigging[guild.id].winners_count - len(self.rigging[guild.id].winners)
        number_of_winners_to_pick = min(number_of_winners_to_pick, len(eligible_users))
        winners = random.choices(eligible_users, k=number_of_winners_to_pick)
        winner_role = await self.resolve_winner_role(guild)
        for winner in winners:
            member: Member = await guild.fetch_member(winner.id)
            await member.add_roles(winner_role, reason="rigged")
        self.rigging[guild.id].winners += [w.id for w in winners]
        winners_as_string = "\n".join([f'<@{w}>' for w in self.rigging[guild.id].winners])
        await message.edit(content=self.get_initial_message(guild) + f'\nWinners:\n{winners_as_string}')
        self.save_rigging()

    async def resolve_winner_role(self, guild) -> Role:
        return guild.get_role(int(self.config[guild.id].winner_role[3:-1]))

    async def resolve_admin_role(self, guild) -> Role:
        return guild.get_role(int(self.config[guild.id].admin_role[3:-1]))

    async def get_eligible_users(self, guild: Guild, message: Message) -> List[User]:
        reaction = [reaction for reaction in message.reactions if reaction.emoji == '🎉'][0]
        eligible_users = await reaction.users().flatten()
        eligible_users = [user for user in eligible_users if
                          user.id not in self.rigging[guild.id].winners
                          and user.id != self.bot.user.id]
        return eligible_users

    async def get_rigging_message(self, guild: Guild) -> Message:
        channel_id = int(self.config[guild.id].channel[2:-1])
        channel = self.bot.get_channel(channel_id)
        message: Message = await channel.fetch_message(self.rigging[guild.id].message_id)
        return message

    async def process_config(self, ctx, args):
        guild = ctx.guild
        if not len(args):
            await ctx.send(f'Current configuration:\n{self.config[guild.id]}')
            return

        if len(args) < 2:
            await ctx.send(
                'You need to give me the name and the new value to update a config setting\n'
                'Example: `!rig config duration 1337` _set duration to 1337 seconds_'
            )
            return

        property_to_modify = args[0]
        new_value_tmp = args[1]
        if not hasattr(self.config[guild.id], property_to_modify):
            await ctx.send(f'Unknown property.\nAvailable properties: {self.config[guild.id]}')
            return
        else:
            old_value = self.config[guild.id].__getattribute__(property_to_modify)
            new_value = new_value_tmp
            try:
                if isinstance(old_value, int):
                    new_value = int(new_value_tmp)
            except ValueError:
                await ctx.send(f'New value for {property_to_modify} must be a number :neutral_face:')
                return

            if property_to_modify == 'message':
                new_value = ' '.join(args[1:])

            self.config[guild.id].__setattr__(property_to_modify, new_value)
            self.save_config()
            await ctx.send(
                f'Modified setting {property_to_modify}: {old_value} → {new_value}\n'
                f'New configuration:\n{self.config[guild.id]}')
            return

    async def process_cancel(self, ctx):
        if ctx.guild.id not in self.rigging or not self.rigging[ctx.guild.id]:
            await ctx.send(f'no rigging to cancel')
            return
        await self.cleanup_previous_riggings(ctx.guild)
        message = await self.get_rigging_message(ctx.guild)
        await message.edit(content=self.get_initial_message(ctx.guild) + f'\n_this rigging has been cancelled_')
        self.rigging[ctx.guild.id] = None
        await ctx.send(f'rigging cancelled')
        self.save_rigging()

    async def process_cleanup(self, ctx):
        if ctx.guild.id not in self.rigging or not self.rigging[ctx.guild.id]:
            await ctx.send(f'no rigging to clean up')
            return
        await self.cleanup_previous_riggings(ctx.guild)
        await ctx.send(f'rigging cleaned up')

    async def print_help(self, ctx: Context):
        help_text = textwrap.dedent('''
            Usage examples:
            `!rig 7`  _start a new rigged drawing for seven people_
            `!rig 7 180`  _start a new rigged drawing for seven people and 180 seconds instead of the default duration_
            `!rig 2 more`  _rig two more people into the current game_
            `!rig config`  _print the current configuration_
            `!rig config channel #general`  _set the channel where the rigging takes place to #general_
            `!rig cancel`  _cancel an ongoing rigging and reset the roles_
            `!rig cleanup`  _only reset the roles_
            ''')
        await ctx.send(help_text)

    async def cleanup_previous_riggings(self, guild):
        if guild.id not in self.rigging or not self.rigging[guild.id]:
            return
        winner_role: Role = await self.resolve_winner_role(guild)
        for winner_id in self.rigging[guild.id].winners:
            member: Member = await guild.fetch_member(winner_id)
            if member:
                await member.remove_roles(winner_role, reason="cleanup")


def main():
    bot = RigBot()
    bot.add_cog(Rigging(bot))
    bot.run(TOKEN)


if __name__ == '__main__':
    main()
