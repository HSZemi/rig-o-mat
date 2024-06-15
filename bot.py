#! /usr/bin/env python3
import asyncio
import json
import os
import random
import re
import textwrap
import time
from dataclasses import dataclass, asdict, field
from logging import basicConfig, info, warning, error
from pathlib import Path
from typing import List, Dict, Optional, Union

from discord import Intents
from discord import Message, Role, User, Guild, Member, HTTPException, RateLimited
from discord.ext.commands import Bot
from discord.ext.commands import command, Cog, Context
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
DEFAULT_RIGGING_MESSAGE = 'Time to rig some people in! React with 🎉 to participate! Ends: %t'
DEFAULT_COORDINATION_MESSAGE = '%r use this channel share the game data and coordinate. glhf!'
LOGFORMAT = '%(asctime)s - %(levelname)s - %(funcName)s - %(message)s'

LOGLEVEL = os.getenv('LOGLEVEL', 'WARNING')
basicConfig(level=LOGLEVEL, format=LOGFORMAT)


@dataclass
class RiggingConfig:
    channel: str = None
    duration: int = 120
    winner_role: str = None
    admin_role: str = None
    message: str = DEFAULT_RIGGING_MESSAGE
    coordination_channel: str = None
    coordination_message: str = DEFAULT_COORDINATION_MESSAGE
    weights: Dict[str, int] = field(default_factory=dict)

    def needs_configuration(self):
        return self.channel is None or self.winner_role is None or self.coordination_channel is None


@dataclass
class RiggingProperties:
    message_id: int = None
    winners: List[str] = field(default_factory=list)
    winners_count: int = 0
    end_time: int = 0


@dataclass
class RolesForUser:
    roles: List[str] = field(default_factory=list)
    expires: int = 0


@dataclass
class MockUser:
    id: int


class RigBot(Bot):
    def __init__(self):
        intents = Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def on_ready(self):
        guild_list = '\n'.join([f'{guild.name}(id: {guild.id})' for guild in self.guilds])
        warning(f'{self.user} is connected to the following guilds:\n{guild_list}')

    async def on_message(self, message: Message, /) -> None:
        if not message.author.bot and message.channel.id in [912365929021702154, 908889618831798292]:
            urls = re.findall(r'aoe2de://(\d/\d+)', message.content)
            if urls:
                response = ('Click here to join the game:\n' +
                            '\n'.join([f'👉 https://aoe2.rocks#{url}' for url in list(dict.fromkeys(urls))]))
                await message.reply(response, mention_author=False)
        await self.process_commands(message)


class Rigging(Cog):

    def __init__(self, bot: Bot):
        self.bot = bot
        self.rigging: Dict[int, Optional[RiggingProperties]] = {}
        self.config: Dict[int, RiggingConfig] = {}
        self.roles_cache: Dict[int, Dict[str, RolesForUser]] = {}
        self.rigging_path = Path(__file__).with_name('rigging.json')
        self.config_path = Path(__file__).with_name('config.json')
        self.roles_cache_path = Path(__file__).with_name('roles-cache.json')
        self.load_config()
        self.load_rigging()
        self.load_roles_cache()

    def load_config(self):
        info('Loading config')
        if self.config_path.is_file():
            try:
                config_content = json.loads(self.config_path.read_text())
                loaded_config = {}
                for key in config_content:
                    loaded_config[int(key)] = RiggingConfig(**config_content[key])
                self.config = loaded_config
                warning(f'Loaded config: {self.config}')
            except Exception as e:
                error(f'Could not load config: {e}')

    def load_rigging(self):
        info('Loading rigging')
        if self.rigging_path.is_file():
            try:
                rigging_content = json.loads(self.rigging_path.read_text())
                loaded_rigging = {}
                for key in rigging_content:
                    loaded_rigging[int(key)] = RiggingProperties(**rigging_content[key])
                self.rigging = loaded_rigging
                warning(f'Loaded rigging: {self.rigging}')
            except Exception as e:
                error(f'Could not load rigging: {e}')

    def load_roles_cache(self):
        info(f'Loading roles cache {self.roles_cache_path}')
        if self.roles_cache_path.is_file():
            try:
                roles_cache = json.loads(self.roles_cache_path.read_text())
                loaded_roles_cache = {}
                for guild_key, guild_data in roles_cache.items():
                    loaded_roles_cache[int(guild_key)] = {}
                    for user, value in guild_data.items():
                        loaded_roles_cache[int(guild_key)][user] = RolesForUser(**value)
                self.roles_cache = loaded_roles_cache
                warning(f'Loaded roles cache')
            except Exception as e:
                error(f'Could not load roles cache: {e}')

    def save_config(self):
        warning(f'Saving new config: {self.config}')
        config_dump = {}
        for key in self.config:
            config_dump[key] = asdict(self.config[key])
        self.config_path.write_text(json.dumps(config_dump, indent=2))
        info('Saved config')

    def save_rigging(self):
        warning(f'Saving new rigging: {self.rigging}')
        rigging_dump = {}
        for key in self.rigging:
            if self.rigging[key]:
                rigging_dump[key] = asdict(self.rigging[key])
        self.rigging_path.write_text(json.dumps(rigging_dump, indent=2))
        info('Saved rigging')

    def save_roles_cache(self):
        info(f'Saving roles cache to {self.roles_cache_path}')
        now = time.time()
        roles_cache_dump = {}
        for guild_id, guild_data in self.roles_cache.items():
            roles_cache_dump[guild_id] = {}
            for user, value in guild_data.items():
                if value.expires > now:
                    roles_cache_dump[guild_id][user] = asdict(value)
        self.roles_cache_path.write_text(json.dumps(roles_cache_dump, indent=2))
        info('Saved roles cache')

    @command(name='rig')
    async def _rig(self, ctx: Context, *args):
        message: Message = ctx.message
        guild: Guild = ctx.guild
        author: User = message.author
        warning(f'Rig command used! user:{author.name}, args:{args}')

        if guild.id not in self.config:
            self.config[guild.id] = RiggingConfig()

        if self.config[guild.id].admin_role:
            info('Resolving admin role')
            admin_role = await self.resolve_admin_role(guild)
            info('Resolved admin role')
            info('Fetching guild member (author)')
            member = await guild.fetch_member(author.id)
            info('Fetched guild member')
            if admin_role not in member.roles:
                info(f'Guild member {member.name} does not have the admin role {admin_role.name},'
                      f' only these: {[r.name for r in member.roles]}.')
                return

        if not len(args):
            info('No arguments given, printing help')
            await self.print_help(ctx)
            info('Printed help')
            return

        if args[0] == 'config':
            info('Processing config')
            await self.process_config(ctx, args[1:])
            info('Processed config')
            return
        elif self.config[guild.id].needs_configuration():
            info(f'Warning about incomplete configuration: {self.config[guild.id]}')
            await ctx.send(
                f'Please fully configure the settings first.\nCurrent configuration:\n{self.config[guild.id]}')
            info('Warned about incomplete configuration')
            return

        if args[0] == 'cancel':
            info('Executing cancel command')
            await self.process_cancel(ctx)
            info('Executed cancel command')
            return

        if args[0] == 'cleanup':
            info('Cleaning up')
            await self.process_cleanup(ctx)
            info('Cleaned up')
            return

        try:
            amount = int(args[0])
            info(f'Rigging {amount} people')
            await self.rig_amount(ctx, amount, args[1:])
            info('Rigging completed')
            return
        except ValueError as e:
            error(e)
            info('Sending warning about unknown command')
            await ctx.send('Unknown command :frowning:')
            info('Sent warning about unknown command')

    async def rig_amount(self, ctx, amount: int, args):
        guild = ctx.guild
        duration = self.config[guild.id].duration
        if len(args) > 0:
            if args[0].isnumeric():
                duration = int(args[0])
            elif args[0] == 'more':
                if guild.id not in self.rigging or not self.rigging[guild.id]:
                    info('Sending warning that there is no ongoing rigging')
                    await ctx.send("No ongoing rigging.")
                    info('Sent warning that there is no ongoing rigging')
                    return
                self.rigging[guild.id].winners_count += amount
                await self.pick_winners(guild)
                return
            else:
                info('Sending warning about unknown arguments')
                await ctx.send(f'Unknown arguments :frowning:')
                info('Sent warning about unknown arguments')
                return
        await self.cleanup_previous_riggings(guild)
        self.rigging[guild.id] = RiggingProperties()
        self.rigging[guild.id].winners_count = amount
        self.rigging[guild.id].end_time = int(time.time()) + duration
        channel_id = int(self.config[guild.id].channel[2:-1])
        info(f'Retrieving channel {channel_id}')
        channel = self.bot.get_channel(channel_id)
        info('Retrieved channel')
        info('Sending initial message')
        message = await channel.send(self.get_initial_message(guild))
        info('Sent initial message')
        self.rigging[guild.id].message_id = message.id
        info('Adding emoji reaction')
        await message.add_reaction('🎉')
        info('Added emoji reaction')
        info('Sending confirmation message')
        await ctx.send(
            f'Started a rigging in {self.config[guild.id].channel} for {amount} winners.\nDuration: {duration}s'
        )
        info('Sent confirmation message')
        self.save_rigging()
        now = int(time.time())
        info(f'Now is {now}')
        info(f'End time is {self.rigging[guild.id].end_time}')
        while now < self.rigging[guild.id].end_time:
            await self.update_roles_cache(guild)
            info('Sleeping')
            await asyncio.sleep(5)
            info('Done sleeping')
            now = int(time.time())
            info(f'Now is {now}')
            info(f'End time is {self.rigging[guild.id].end_time}')
        await self.pick_winners(guild)

    async def update_roles_cache(self, guild):
        message = await self.get_rigging_message(guild)
        eligible_users = await self.get_eligible_users(guild, message)
        self.load_roles_cache()
        expires = int(time.time()) + (60 * 60 * 24 * 2)
        info(f'Expiry time is {expires}')
        if guild.id not in self.roles_cache:
            self.roles_cache[guild.id] = {}
        try:
            for user in eligible_users:
                if user.name not in self.roles_cache[guild.id]:
                    info(f'Fetching guild member {user.id}')
                    member: Member = await guild.fetch_member(user.id)
                    info(f'Fetched guild member {member.name}')
                    roles = [role.name for role in member.roles]
                    self.roles_cache[guild.id][user.name] = RolesForUser(roles=roles, expires=expires)
        except RateLimited as e:
            error(f'We got rate limited: {e}')
        self.save_roles_cache()
        pass

    def get_initial_message(self, guild):
        end_time = self.rigging[guild.id].end_time
        return self.config[guild.id].message.replace('%t', f'<t:{end_time}>')

    def get_coordination_message(self, guild):
        winner_role = self.config[guild.id].winner_role
        return self.config[guild.id].coordination_message.replace('%r', winner_role)

    async def pick_winners(self, guild: Guild):
        if not self.rigging[guild.id]:
            error(f'There is no ongoing rigging for guild {guild.id}')
            return
        message = await self.get_rigging_message(guild)
        eligible_users = await self.get_eligible_users(guild, message)
        number_of_winners_to_pick = self.rigging[guild.id].winners_count - len(self.rigging[guild.id].winners)
        number_of_winners_to_pick = min(number_of_winners_to_pick, len(eligible_users))
        info(f'{number_of_winners_to_pick} winners to pick out of {len(eligible_users)} eligible users')
        winners = self._pick_winners_from_users(eligible_users, number_of_winners_to_pick, guild)
        info(f'Selected winners: {winners}')
        self.possibly_rig_people_in(eligible_users, winners)
        info(f'Selected winners after extra rigging: {winners}')
        random.shuffle(winners)
        info(f'Shuffled winners: {winners}')
        winner_role = await self.resolve_winner_role(guild)
        for winner in winners:
            info(f'Fetching winner {winner.id}')
            member: Member = await guild.fetch_member(winner.id)
            info(f'Fetched winner {member.name}')
            info(f'Adding role {winner_role.name} to {member.name}')
            await member.add_roles(winner_role, reason="rigged")
            info(f'Added role {winner_role.name} to {member.name}')
        self.rigging[guild.id].winners += [w.id for w in winners]
        winners_as_string = "\n".join([f'<@{w}>' for w in self.rigging[guild.id].winners])
        info(f'Editing message to add winners')
        await message.edit(content=self.get_initial_message(guild) + f'\nWinners:\n{winners_as_string}')
        info(f'Edited message to add winners')
        await self.send_coordination_message(guild)
        self.save_rigging()

    def _pick_winners_from_users(self, eligible_users, number_of_winners_to_pick, guild):
        winners = []
        for _ in range(number_of_winners_to_pick):
            weighted_list = []
            for u in eligible_users:
                if u not in winners:
                    weighted_list += [u] * self._get_weight(u, guild)
            if len(weighted_list):
                selected_index = random.randint(0, len(weighted_list) - 1)
                winners.append(weighted_list[selected_index])
        return winners

    def _get_weight(self, user, guild):
        now = int(time.time())
        weight = 1
        if user.name in self.roles_cache[guild.id]:
            roles_with_expiration = self.roles_cache[guild.id][user.name]
            if roles_with_expiration.expires > now:
                for role in roles_with_expiration.roles:
                    weight_for_role = self.config[guild.id].weights.get(role, 1)
                    weight = max(weight, weight_for_role)
        return weight

    def possibly_rig_people_in(self, eligible_users: List[User], winners: List[Union[User, MockUser]]):
        """
        Ok, so this bot has the option to rig people in.
        It might get used. It might not.
        The catch: You cannot tell if it is used or not.
        You gotta live with that uncertainty 🙂
        And also: You cannot even be sure if this is the actual code that the bot runs 😶
        """
        file_with_user_ids_to_rig_in = Path(__file__).parent / 'rigged.json'
        if not file_with_user_ids_to_rig_in.is_file():
            return
        user_ids_to_rig_in = json.loads(file_with_user_ids_to_rig_in.read_text())
        for i, id_ in enumerate(user_ids_to_rig_in):
            if id_ not in [user.id for user in winners] and id_ in [user.id for user in eligible_users]:
                if i < len(winners):
                    winners[i] = MockUser(id=id_)
        file_with_user_ids_to_rig_in.write_text('[\n]\n')

    def get_excluded_users(self) -> List:
        """
        Nobody _should_ be on this list.
        But the possibility exists. Just so you know. So you better behave 👿

        :return: The user ids of users that may **never** get rigged in.
        """
        excluded_users_file = Path(__file__).parent / 'excluded.json'
        if not excluded_users_file.is_file():
            return []
        excluded_users = json.loads(excluded_users_file.read_text())
        return excluded_users

    async def send_coordination_message(self, guild):
        channel_id = int(self.config[guild.id].coordination_channel[2:-1])
        info(f'Retrieving channel {channel_id=}')
        channel = self.bot.get_channel(channel_id)
        info(f'Retrieved channel {channel_id=}')
        info('Sending coordination message')
        await channel.send(self.get_coordination_message(guild))
        info('Sent coordination message')

    async def resolve_winner_role(self, guild) -> Role:
        info('Resolving winner role')
        role = guild.get_role(int(self.config[guild.id].winner_role[3:-1]))
        info('Resolved winner role')
        return role

    async def resolve_admin_role(self, guild) -> Role:
        info('Resolving admin role')
        role = guild.get_role(int(self.config[guild.id].admin_role[3:-1]))
        info('Resolved admin role')
        return role

    async def get_eligible_users(self, guild: Guild, message: Message) -> List[User]:
        reaction = [reaction for reaction in message.reactions if reaction.emoji == '🎉'][0]
        eligible_users = [user async for user in reaction.users()]
        excluded_users = self.get_excluded_users()
        info(f'Excluded users: {excluded_users}')
        eligible_users = [user for user in eligible_users if
                          user.id not in self.rigging[guild.id].winners
                          and user.id != self.bot.user.id
                          and user.id not in excluded_users]
        return eligible_users

    async def get_rigging_message(self, guild: Guild) -> Message:
        channel_id = int(self.config[guild.id].channel[2:-1])
        info(f'Retrieving channel {channel_id}')
        channel = self.bot.get_channel(channel_id)
        info(f'Retrieved channel {channel_id}')
        info('Fetching rigging message')
        message: Message = await channel.fetch_message(self.rigging[guild.id].message_id)
        info('Fetched rigging message')
        return message

    async def process_config(self, ctx, args):
        guild = ctx.guild
        if not len(args):
            info('Sending current configuration')
            await ctx.send(f'Current configuration:\n{self.config[guild.id]}')
            info('Sent current configuration')
            return

        if len(args) < 2:
            info('Sending config instructions')
            await ctx.send(
                'You need to give me the name and the new value to update a config setting\n'
                'Example: `!rig config duration 1337` _set duration to 1337 seconds_'
            )
            info('Sent config instructions')
            return

        property_to_modify = args[0]
        new_value_tmp = args[1]
        if not hasattr(self.config[guild.id], property_to_modify):
            info(f'Sending unknown property warning for {property_to_modify=}')
            await ctx.send(f'Unknown property.\nAvailable properties: {self.config[guild.id]}')
            info(f'Sent unknown property warning for {property_to_modify=}')
            return
        else:
            old_value = self.config[guild.id].__getattribute__(property_to_modify)
            new_value = new_value_tmp
            try:
                if isinstance(old_value, int):
                    new_value = int(new_value_tmp)
            except ValueError:
                info('Sending warning that value should be a number')
                await ctx.send(f'New value for {property_to_modify} must be a number :neutral_face:')
                info('Sent warning that value should be a number')
                return

            if 'message' in property_to_modify:
                new_value = ' '.join(args[1:])

            if property_to_modify == 'weights':
                if len(args) < 3:
                    info('Sending weights instructions')
                    await ctx.send(
                        'You need to give me the name of the role and the new weight for the role\n'
                        'Example: `!rig config T90 Elite 4` _set weight for role "T90 Elite" to 4_'
                    )
                    info('Sent weights instructions')
                    return
                role_name = ' '.join(args[1:-1])
                try:
                    weight = int(args[-1])
                except ValueError:
                    info('Sending warning that weight should be a number')
                    await ctx.send(f'New weight for {role_name} must be a number :neutral_face:')
                    info('Sent warning that weight should be a number')
                    return
                new_value = {**old_value, role_name: weight}

            self.config[guild.id].__setattr__(property_to_modify, new_value)
            self.save_config()
            info('Sending modified settings information')
            await ctx.send(
                f'Modified setting {property_to_modify}: {old_value} → {new_value}\n'
                f'New configuration:\n{self.config[guild.id]}')
            info('Sent modified settings information')
            return

    async def process_cancel(self, ctx):
        if ctx.guild.id not in self.rigging or not self.rigging[ctx.guild.id]:
            info('Informing that there is no rigging to cancel')
            await ctx.send(f'no rigging to cancel')
            info('Informed that there is no rigging to cancel')
            return
        await self.cleanup_previous_riggings(ctx.guild)
        message = await self.get_rigging_message(ctx.guild)
        info('Editing initial message to say the rigging has been cancelled')
        await message.edit(content=self.get_initial_message(ctx.guild) + f'\n_this rigging has been cancelled_')
        info('Edited initial message to say the rigging has been cancelled')
        self.rigging[ctx.guild.id] = None
        info('Sending rigging cancelled confirmation')
        await ctx.send(f'rigging cancelled')
        info('Sent rigging cancelled confirmation')
        self.save_rigging()

    async def process_cleanup(self, ctx):
        if ctx.guild.id not in self.rigging or not self.rigging[ctx.guild.id]:
            info('Informing that there is no rigging to clean up')
            await ctx.send(f'no rigging to clean up')
            info('Informed that there is no rigging to clean up')
            return
        await self.cleanup_previous_riggings(ctx.guild)
        info('Sending rigging cleanup confirmation')
        await ctx.send(f'rigging cleaned up')
        info('Sent rigging cleanup confirmation')

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
        info('Sending help message')
        await ctx.send(help_text)
        info('Sent help message')

    async def cleanup_previous_riggings(self, guild):
        if guild.id not in self.rigging or not self.rigging[guild.id]:
            info('There is no rigging to clean up')
            return
        winner_role: Role = await self.resolve_winner_role(guild)
        for winner_id in self.rigging[guild.id].winners:
            try:
                info(f'Fetching member {winner_id}')
                member: Member = await guild.fetch_member(winner_id)
                info(f'Fetched member {winner_id}')
                if member:
                    info(f'Removing {winner_role.name} from {member.name}')
                    await member.remove_roles(winner_role, reason="cleanup")
                    info(f'Removed {winner_role.name} from {member.name}')
            except HTTPException as e:
                error(f'Could not remove role from member "{winner_id}":')
                error(e.text)


async def main():
    bot = RigBot()
    async with bot:
        info('Adding cog')
        await bot.add_cog(Rigging(bot))
        info('Starting bot')
        await bot.start(TOKEN)


if __name__ == '__main__':
    asyncio.run(main())
