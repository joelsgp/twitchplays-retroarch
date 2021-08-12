"""Twitch Plays application for RetroArch/FBNeo, with input queue and chat control toggle shortcut.

Files:
- config.toml - private configuration file
- config.example.toml - template configuration file, may be stored in package directory or bundled exe.
Return codes:
- 0 ok
- 1 no config
- 2 config created from template

Contains custom Bot class and main function.

For more info check README.md.
"""


import argparse
import concurrent.futures
import queue
import threading
import time
import sys
import logging as log
from pathlib import Path

import keyboard
import toml
import twitchio.ext.commands

from twitchplays_retroarch import util

if sys.platform == 'win32':
    import pydirectinput
else:
    import pyautogui


# todo: sync command set with RetroArch config files
# todo: hotkey sound?
# todo: add bot commands like list twitchplays commands
# todo: check if running as admin on startup
# todo: check if keys in config are valid on startup?


__version__ = '0.4.0'


CONFIG_NAME = 'config.toml'
CONFIG_TEMPLATE_NAME = 'config.example.toml'


stream_handler = log.StreamHandler(stream=sys.stdout)
stream_handler.setLevel(log.INFO)
stream_formatter = log.Formatter('%(message)s')
stream_handler.setFormatter(stream_formatter)
log.basicConfig(
    level=log.DEBUG,
    handlers=[
        stream_handler
    ]
)


class TwitchPlaysRetroArchBot(twitchio.ext.commands.bot.Bot):
    """Implementation of Bot."""
    test_keys_fbneo = {
        'up': 'up',
        'down': 'down',
        'left': 'left',
        'right': 'right',
        'button1': 'z',
        'button2': 'x',
        'start': 'enter',
        'COIN': 'shiftright',
    }

    def __init__(
            self,
            *args,
            keypress_delay: float = 0.1, keypress_duration: float = 0.1,
            input_threads: int = 1,
            commandset: dict = None,
            case_insensitive: bool = True,
            **kwargs
    ):
        """Initialise the bot instance.

        The case_insensitive argument is made into an attribute and then passed to the subclassed constructor.
        A ThreadPoolExecutor is created with input_threads as max_workers.
        It could easily be changed to a ProcessPoolExecutor.
        A Queue is also created.
        """
        self.keypress_delay = keypress_delay
        self.keypress_duration = keypress_duration
        self.commandset = commandset
        self.case_insensitive = case_insensitive
        # for pausing user control
        self.twitchplays_commands_enabled = True

        self.input_queue = queue.Queue()
        # can be easily changed to a ProcessPoolExecutor
        self.input_thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=input_threads, thread_name_prefix='InputHandler'
        )

        super().__init__(case_insensitive=case_insensitive, *args, **kwargs)

    async def event_ready(self):
        """Log ready message."""
        log.info('Bot started.')

    def twitchplays_commands_toggle(self):
        """Switch self.twitchplays_commands_enabled, and log a status message."""
        self.twitchplays_commands_enabled = not self.twitchplays_commands_enabled

        if self.twitchplays_commands_enabled:
            status = 'enabled'
        else:
            status = 'disabled'
        log.info(f'Twitch Plays commands {status}.')

    def input_queue_pop(self):
        """Get one key name from self.input_queue and press it with PyAutoGUI or PyDirectInpt.

        Designed to be submitted to a threadpool.
        """
        thread_name = threading.currentThread().name
        log.info('%s: Handling one input from queue.', thread_name)

        key_to_press = self.input_queue.get()
        log.info('%s: Executing input: %s.', thread_name, key_to_press)

        if sys.platform == 'win32':
            input_emulator = pydirectinput
        else:
            input_emulator = pyautogui
        input_emulator.keyDown(key_to_press)
        time.sleep(self.keypress_duration)
        input_emulator.keyUp(key_to_press)

        time.sleep(self.keypress_delay)

    async def process_twitchplays_commands(self, message: twitchio.Message) -> bool:
        """Check the message's contents to see if it matches self.commandset, and queue an input to self.input_queue.

        Return True if a Twitch Plays command was read and queued, False otherwise.
        Uses str.casefold() on the message if self.case_insensitive is set to True.
        """
        commandset = self.commandset

        command = message.content
        if self.case_insensitive:
            command = command.casefold()

        if command in commandset:
            key_to_press = commandset[command]
            log.info(f'Queueing input: {key_to_press}.')
            self.input_queue.put(key_to_press)
            self.input_thread_pool.submit(self.input_queue_pop)
            return True

        return False

    async def event_message(self, message: twitchio.Message):
        """Override event_message - log the message and call self.process_twitchplays_commands on it."""
        # ignore messages from the bot
        if message.echo:
            log.info('Ignoring message from bot: %s', message.content)
            return

        log.info('Got user message: %s: %s', message.author.name, message.content)
        if self.twitchplays_commands_enabled:
            await self.process_twitchplays_commands(message)

        # handle commands, like in the base event_message
        await self.handle_commands(message)

    async def close(self):
        """Override this method to close the threadpool and also log a message."""
        log.info('Shutting down bot.')
        self.input_thread_pool.shutdown()
        await super().close()


def find_config(
        config_name: str = CONFIG_NAME,
        config_template_name: str = CONFIG_TEMPLATE_NAME
) -> Path:
    """Check if a config file named config_name exists in the working dir, if it doesn't prompt to create it.

    Calls sys.exit with code 1 if user declines to create config and 2 if config tempalte is created successfully.
    Searches in three locations for the config template with name config_tempalte_name:
        - The working directory
        - The package resources, with pkg_resources module - works with all setuptools installs, like from PyPI
        - The temp dir used by pyinstaller
    """
    config_path = Path(config_name)
    if config_path.is_file():
        return config_path

    log.warning('Config file %s does not exist.', config_name)
    create_template = util.yn(f'Create a template config file from {config_template_name}?\nY/n\n')
    if not create_template:
        log.fatal('No config file.')
        util.q(1)

    template_contents = ''

    # try to find the template depending on distribution
    # if cloned from source, the example will be in the working directory
    if not template_contents:
        config_template_local = Path(config_template_name)
        if config_template_local.is_file():
            template_contents = config_template_local.read_text(encoding='utf-8')

    # if installed via pip/pypi, the example will be in package resources
    if not template_contents:
        try:
            import pkg_resources
        except ImportError:
            # for pycharm's dumb inspections
            pkg_resources = None
            assert pkg_resources is None
        else:
            if pkg_resources.resource_exists(__name__, config_template_name):
                template_contents = pkg_resources.resource_string(__name__, config_template_name)

    # if bundled with pyinstaller, sys.frozen will be True
    # and the example will be in the temporary directory indicated by sys._MEIPASS
    if not template_contents:
        if getattr(sys, 'frozen', False):
            # noinspection PyProtectedMember
            bundle_dir = sys._MEIPASS
            config_template_meipass = Path(bundle_dir) / config_template_name
            template_contents = config_template_meipass.read_text(encoding='utf-8')

    with open(config_path, 'x', encoding='utf-8') as config_file:
        config_file.write(template_contents)

    log.warning(
        'Template config file created. '
        'Fill in essential details like the Twitch login token, and run the program again.'
    )
    util.q(2)


def get_parser() -> argparse.ArgumentParser:
    """Return the ArgumentParser for this script.

    Has a description from __doc__, and a version command from __version__.
    """
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument(
        'config_file', nargs='?', default=CONFIG_NAME,
        help='Name of the file to get settings from, default %(default)s.\n'
             "If it doesn't exist, you will be prompted to automatically create it from the built in template."
    )

    return parser


def main():
    """Main entry point - load config and run a bot."""
    parser = get_parser()
    args = parser.parse_args()

    config_path = find_config(args.config_file)

    log.info('Loading config.')
    with open(config_path) as config_file:
        config = toml.load(config_file)

    bot = TwitchPlaysRetroArchBot(
        token=config['twitch']['token'],
        prefix=config['bot']['prefix'],
        initial_channels=[config['twitch']['channel_to_join']],
        case_insensitive=config['bot']['case_insensitive'],
        input_threads=config['bot']['input_threads'],
        keypress_duration=config['bot']['keypress_duration'],
        keypress_delay=config['bot']['keypress_duration'],
        commandset=config['keys']
    )

    keyboard.add_hotkey(
        config['hotkeys']['toggle_allow_twitchplays_commands'],
        bot.twitchplays_commands_toggle,
        suppress=True
    )

    log.info('Starting bot.')
    bot.run()
