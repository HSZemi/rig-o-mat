#! /usr/bin/env python3
import asyncio
import json
import os
import random
import re
import textwrap
import time
from dataclasses import dataclass, asdict, field, fields
from logging import basicConfig, info, warning, error
from pathlib import Path
from typing import List, Dict, Optional, Union

import requests
from discord import Intents, Interaction, app_commands, Object, TextChannel
from discord import Message, Role, User, Guild, Member, HTTPException, RateLimited
from discord.ext.commands import Bot, CommandInvokeError, Cog, GroupCog
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN', 'missing_discord_token')
DEFAULT_RIGGING_MESSAGE = 'Time to rig some people in! React with 🎉 to participate! Ends: %t'
DEFAULT_COORDINATION_MESSAGE = 'use this channel share the game data and coordinate. glhf!'
LOGFORMAT = '%(asctime)s - %(levelname)s - %(funcName)s - %(message)s'

LOGLEVEL = os.getenv('LOGLEVEL', 'WARNING')
basicConfig(level=LOGLEVEL, format=LOGFORMAT)


class IncompleteConfigurationException(Exception):
    pass


@dataclass
class RiggingConfig:
    channel: str = None
    duration: int = 120
    winner_role: str = None
    message: str = DEFAULT_RIGGING_MESSAGE
    coordination_channel: str = None
    coordination_message: str = DEFAULT_COORDINATION_MESSAGE
    weights: Dict[str, int] = field(default_factory=dict)

    def needs_configuration(self):
        return self.channel is None or self.winner_role is None or self.coordination_channel is None


FIELDS = {f.name for f in fields(RiggingConfig())}

@dataclass
class RiggingProperties:
    message_id: int = None
    winners: List[int] = field(default_factory=list)
    winners_count: int = 0
    end_time: int = 0


@dataclass
class RolesForUser:
    roles: List[str] = field(default_factory=list)
    expires: int = 0


@dataclass
class MockUser:
    id: int


def get_lobby_title(lobby_id: int) -> str:
    try:
        headers = {'User-Agent': 'T90 Rig-O-Mat 97.1'}
        result = requests.get('https://aoe-api.worldsedgelink.com/community/advertisement/findAdvertisements?title=age2', headers=headers)
        result_json = result.json()
        all_titles = {m['id']: m['description'] for m in result_json['matches']}
        title = all_titles.get(lobby_id, '')
        if title:
            title = title.replace(']', '')
            title = title.replace(')', '')
            title = title.replace('>', '')
            title = f'**{title}**'
        return title or '???'
    except Exception:
        return '???'


class RigBot(Bot):
    def __init__(self):
        intents = Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        info('Adding cogs')
        await self.add_cog(Rigging(self))
        await self.add_cog(LobbyCog(self))
        synced_commands = await self.tree.sync()
        info(synced_commands)

    async def on_ready(self):
        guild_list = '\n'.join([f'{guild.name}(id: {guild.id})' for guild in self.guilds])
        warning(f'{self.user} is connected to the following guilds:\n{guild_list}')


class LobbyCog(Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(name='lobby')
    async def _lobby(self, interaction: Interaction, lobby_url: str, password: str | None = None) -> None:
        """Post neatly formatted lobby info

        :param lobby_url: The url to the lobby in the format aoe2de://0/123456789
        :param password: (Optional) the password for the lobby
        :return:
        """
        if not re.match(r'aoe2de://(\d/\d+)', lobby_url):
            await interaction.response.send_message("Invalid lobby url")
            return
        lobby_id = int(lobby_url[11:])
        title = get_lobby_title(lobby_id)
        response = f'Lobby Name: {title}'
        if password:
            response += f'\nPassword: `{password}`'
        response += f'\nClick here to join the game:\n👉 https://aoe2.rocks#0/{lobby_id}'
        info(f'Sending lobby message {lobby_url=} {password=}')
        await interaction.response.send_message(response)
        info(f'Sent lobby message {lobby_url=} {password=}')


class Rigging(GroupCog, name="rig", description="Manage riggings"):
    config_group = app_commands.Group(name="config", description="Configure riggings")

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
        super().__init__()

    def load_config(self):
        info('Loading config')
        if self.config_path.is_file():
            try:
                config_content = json.loads(self.config_path.read_text())
                loaded_config = {}
                for key in config_content:
                    config_content[key] = {k: v for k, v in config_content[key].items() if k in FIELDS}
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
        winners = self.possibly_rig_people_in(eligible_users, winners)
        info(f'Selected winners after extra rigging: {winners}')
        random.shuffle(winners)
        info(f'Shuffled winners: {winners}')
        winner_role = await self.resolve_winner_role(guild)
        for winner in winners:
            info(f'Fetching winner {winner.id}')
            member: Member = await guild.fetch_member(winner.id)
            info(f'Fetched winner {member.name}')
            info(f'Adding role {winner_role.name} to {member.name}')
            try:
                await member.add_roles(winner_role, reason="rigged")
                info(f'Added role {winner_role.name} to {member.name}')
            except CommandInvokeError as ex:
                warning(f'Could not add role: {ex}')
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

    def possibly_rig_people_in(self, eligible_users: List[User],
                               winners: List[Union[User, MockUser]]) -> List[Union[User, MockUser]]:
        """
        Ok, so this bot has the option to rig people in.
        It might get used. It might not.
        The catch: You cannot tell if it is used or not.
        You gotta live with that uncertainty 🙂
        And also: You cannot even be sure if this is the actual code that the bot runs 😶
        """
        file_with_user_groups_to_rig_in = Path(__file__).parent / 'rigged.json'
        if not file_with_user_groups_to_rig_in.is_file():
            return winners
        user_groups_to_rig_in: list[list[int]] = json.loads(file_with_user_groups_to_rig_in.read_text())
        remaining_user_groups_to_rig_in = []
        users_to_rig_in = set()
        eligible_user_ids = [user.id for user in eligible_users]
        for user_group in user_groups_to_rig_in:
            eligible_ids_from_group = [user_id for user_id in user_group if user_id in eligible_user_ids]
            next_users_to_rig_in = users_to_rig_in.union(set(eligible_ids_from_group))
            if len(eligible_ids_from_group) == len(user_group) and len(next_users_to_rig_in) <= len(winners):
                users_to_rig_in = next_users_to_rig_in
            else:
                remaining_user_groups_to_rig_in.append(user_group)
        unrigged_winners = [user for user in winners if user.id not in users_to_rig_in]
        new_winners = [MockUser(id=id_) for id_ in users_to_rig_in]
        remaining_spots = len(winners) - len(users_to_rig_in)
        new_winners.extend(unrigged_winners[:remaining_spots])
        file_with_user_groups_to_rig_in.write_text(json.dumps(remaining_user_groups_to_rig_in))
        return new_winners

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

    async def send_coordination_message(self, guild: Guild):
        channel_id = int(self.config[guild.id].coordination_channel[2:-1])
        info(f'Retrieving channel {channel_id=}')
        channel = self.bot.get_channel(channel_id)
        info(f'Retrieved channel {channel_id=}')
        info('Sending coordination message')
        await channel.send(self.config[guild.id].coordination_message)
        info('Sent coordination message')

    async def resolve_winner_role(self, guild: Guild) -> Role:
        info('Resolving winner role')
        role = guild.get_role(int(self.config[guild.id].winner_role[3:-1]))
        info('Resolved winner role')
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

    async def cleanup_previous_riggings(self, guild: Guild):
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


    async def pre_check(self, interaction: Interaction, skip_config_check=False)->None:
        if interaction.guild.id not in self.config:
            self.config[interaction.guild.id] = RiggingConfig()

        if not skip_config_check and self.config[interaction.guild.id].needs_configuration():
            info(f'Warning about incomplete configuration: {self.config[interaction.guild.id]}')
            await interaction.response.send_message(
                f'Please fully configure the settings first.\nCurrent configuration:\n{self.config[interaction.guild.id]}')
            info('Warned about incomplete configuration')
            raise IncompleteConfigurationException

    @app_commands.command(name='help', description='Print usage examples')
    async def _help(self, interaction: Interaction) -> None:
        await self.pre_check(interaction)
        help_text = textwrap.dedent('''
                    Usage examples:
                    `/rig start amount: 7`  _start a new rigged drawing for seven people_
                    `/rig start amount: 7 duration: 180`  _start a new rigged drawing for seven people and 180 seconds instead of the default duration_
                    `/rig more amount: 2`  _rig two more people into the current game_
                    `/rig cancel`  _cancel an ongoing rigging and reset the roles_
                    `/rig cleanup`  _only reset the roles_
                    ''')
        info('Sending help message')
        await interaction.response.send_message(help_text)
        info('Sent help message')

    @config_group.command(name='show', description='Show the current configuration')
    async def _config_show(self, interaction: Interaction) -> None:
        assert interaction.guild_id
        await self.pre_check(interaction, skip_config_check=True)
        info('Sending current configuration')
        await interaction.response.send_message(f'Current configuration:\n{self.config[interaction.guild_id]}\n')
        info('Sent current configuration')

    async def _set_config(self, interaction: Interaction, key: str, new_value: str | int) -> None:
        assert interaction.guild_id
        await self.pre_check(interaction, skip_config_check=True)
        old_value = self.config[interaction.guild_id].__getattribute__(key)

        self.config[interaction.guild_id].__setattr__(key, new_value)
        self.save_config()
        info('Sending modified settings information')
        await interaction.response.send_message(
            f'Modified setting {key}: {old_value} → {new_value}\n'
            f'New configuration:\n{self.config[interaction.guild_id]}')
        info('Sent modified settings information')
        return

    @config_group.command(name='channel')
    async def _config_channel(self, interaction: Interaction, channel: TextChannel) -> None:
        """Edit the channel for riggings

        :param channel: The channel where riggings take place
        :return:
        """
        property_to_modify = 'channel'
        new_value = f"<#{channel.id}>"
        await self._set_config(interaction, property_to_modify, new_value)

    @config_group.command(name='duration')
    async def _config_duration(self, interaction: Interaction, duration: int) -> None:
        """Edit the default duration for riggings

        :param duration: The new default duration of riggings in seconds
        :return:
        """
        property_to_modify = 'duration'
        new_value = duration
        await self._set_config(interaction, property_to_modify, new_value)

    @config_group.command(name='winner_role')
    async def _config_winner_role(self, interaction: Interaction, role: Role) -> None:
        """Edit the role that winners are assigned

        :param role: The role that winners will be assigned
        :return:
        """
        property_to_modify = 'winner_role'
        new_value = f"<@&{role.id}>"
        await self._set_config(interaction, property_to_modify, new_value)

    @config_group.command(name='message')
    async def _config_message(self, interaction: Interaction, message: str) -> None:
        """Edit the message that announces a new rigging

        :param message: The new message that will be posted for people to react to
        :return:
        """
        property_to_modify = 'message'
        new_value = message
        await self._set_config(interaction, property_to_modify, new_value)

    @config_group.command(name='coordination_channel')
    async def _config_coordination_channel(self, interaction: Interaction, channel: TextChannel) -> None:
        """Edit the coordination channel

        :param channel: The channel where coordination takes place after riggings.
         The winner_role should have access.
        :return:
        """
        property_to_modify = 'coordination_channel'
        new_value = f"<#{channel.id}>"
        await self._set_config(interaction, property_to_modify, new_value)

    @config_group.command(name='coordination_message')
    async def _config_coordination_message(self, interaction: Interaction, message: str) -> None:
        """Edit the message that gets posted in the coordination channel

        :param message: The new message that will be posted in the coordination channel after winners are picked
        :return:
        """
        property_to_modify = 'coordination_message'
        new_value = message
        await self._set_config(interaction, property_to_modify, new_value)

    @config_group.command(name='weights', description='Edit the weights for riggings')
    async def _config_weights(self, interaction: Interaction, role: Role, weight: int) -> None:
        """Edit the weights for riggings

        :param role: The role for which to set a new weight
        :param weight: The new weight for that role
        :return:
        """
        role_str = role.name
        assert interaction.guild_id
        await self.pre_check(interaction, skip_config_check=True)
        old_value = self.config[interaction.guild_id].weights.get(role_str)
        new_value = weight

        self.config[interaction.guild_id].weights[role_str] = new_value
        self.save_config()
        info('Sending modified settings information')
        await interaction.response.send_message(
            f'Modified setting weights[{role_str}]: {old_value} → {new_value}\n'
            f'New configuration:\n{self.config[interaction.guild_id]}')
        info('Sent modified settings information')
        return

    @app_commands.command(name='cancel', description='Cancel the current rigging and reset the roles')
    async def _cancel(self, interaction: Interaction) -> None:
        await self.pre_check(interaction)
        if interaction.guild_id not in self.rigging or not self.rigging[interaction.guild.id]:
            info('Informing that there is no rigging to cancel')
            await interaction.response.send_message(f'no rigging to cancel')
            info('Informed that there is no rigging to cancel')
            return
        await self.cleanup_previous_riggings(interaction.guild)
        message = await self.get_rigging_message(interaction.guild)
        info('Editing initial message to say the rigging has been cancelled')
        await message.edit(content=self.get_initial_message(interaction.guild) + f'\n_this rigging has been cancelled_')
        info('Edited initial message to say the rigging has been cancelled')
        self.rigging[interaction.guild.id] = None
        info('Sending rigging cancelled confirmation')
        await interaction.response.send_message(f'rigging cancelled')
        info('Sent rigging cancelled confirmation')
        self.save_rigging()

    @app_commands.command(name='cleanup', description='Clean up the last rigging')
    async def _cleanup(self, interaction: Interaction) -> None:
        await self.pre_check(interaction)
        if interaction.guild.id not in self.rigging or not self.rigging[interaction.guild.id]:
            info('Informing that there is no rigging to clean up')
            await interaction.response.send_message(f'no rigging to clean up')
            info('Informed that there is no rigging to clean up')
            return
        await self.cleanup_previous_riggings(interaction.guild)
        info('Sending rigging cleanup confirmation')
        await interaction.response.send_message(f'rigging cleaned up')
        info('Sent rigging cleanup confirmation')

    @app_commands.command(name='start')
    async def _start(self, interaction: Interaction, amount: int, duration: int | None = None) -> None:
        """Start a new rigging

        :param amount: The number of people to rig in
        :param duration: (Optional) The duration of the rigging in seconds
        :return:
        """
        await self.pre_check(interaction)
        guild = interaction.guild
        duration = duration or self.config[guild.id].duration
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
        await interaction.response.send_message(
            f'Started a rigging in {self.config[guild.id].channel} for {amount} winners.\nDuration: {duration}s'
        )
        info('Sent confirmation message')
        self.save_rigging()
        now = int(time.time())
        info(f'Now is {now}')
        info(f'End time is {self.rigging[guild.id].end_time}')
        while self.rigging[guild.id] and now < self.rigging[guild.id].end_time:
            await self.update_roles_cache(guild)
            info('Sleeping')
            await asyncio.sleep(5)
            info('Done sleeping')
            now = int(time.time())
            info(f'Now is {now}')
            if self.rigging[guild.id]:
                info(f'End time is {self.rigging[guild.id].end_time}')
            else:
                warning('Rigging does not exist anymore')
                return
        await self.pick_winners(guild)

    @app_commands.command(name='more')
    async def _more(self, interaction: Interaction, amount: int) -> None:
        """Add more people to the last rigging

        :param amount: The number of additional people to rig in
        :return:
        """
        await self.pre_check(interaction)
        if interaction.guild.id not in self.rigging or not self.rigging[interaction.guild.id]:
            info('Sending warning that there is no ongoing rigging')
            await interaction.response.send_message("No ongoing rigging.")
            info('Sent warning that there is no ongoing rigging')
            return
        self.rigging[interaction.guild.id].winners_count += amount
        await self.pick_winners(interaction.guild)
        info('Sending confirmation message')
        await interaction.response.send_message(f"Added {amount} more")
        info('Sent confirmation message')


async def main():
    bot = RigBot()
    async with bot:
        info('Starting bot')
        await bot.start(TOKEN)


if __name__ == '__main__':
    asyncio.run(main())
