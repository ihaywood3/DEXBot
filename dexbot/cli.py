#!/usr/bin/env python3
import logging
import os
# we need to do this before importing click
if not "LANG" in os.environ:
    os.environ['LANG'] = 'C.UTF-8'
import click
import signal
import os.path
import os
import sys
import appdirs

from .ui import (
    verbose,
    chain,
    unlock,
    configfile,
    confirmwarning,
    confirmalert,
    warning,
    alert,
)


from .bot import BotInfrastructure
from .cli_conf import configure_dexbot
from . import errors
from . import storage

log = logging.getLogger(__name__)

# inital logging before proper setup.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)


@click.group()
@click.option(
    "--configfile",
    default=os.path.join(appdirs.user_config_dir("dexbot"), "config.yml"),
)
@click.option(
    '--verbose',
    '-v',
    type=int,
    default=3,
    help='Verbosity (0-15)')
@click.option(
    '--systemd/--no-systemd',
    '-d',
    default=False,
    help='Run as a daemon from systemd')
@click.option(
    '--pidfile',
    '-p',
    type=str,
    default='',
    help='File to write PID')
@click.pass_context
def main(ctx, **kwargs):
    ctx.obj = {}
    for k, v in kwargs.items():
        ctx.obj[k] = v


@main.command()
@click.pass_context
@configfile
@chain
@unlock
@verbose
def run(ctx):
    """ Continuously run the bot
    """
    if ctx.obj['pidfile']:
        with open(ctx.obj['pidfile'], 'w') as fd:
            fd.write(str(os.getpid()))
    try:
        try:
            bot = BotInfrastructure(ctx.config)
            # set up signalling. do it here as of no relevance to GUI

            killbots = bot_job(bot, bot.stop)
            # These first two UNIX & Windows
            signal.signal(signal.SIGTERM, kill_bots)
            signal.signal(signal.SIGINT, kill_bots)
            try:
                # These signals are UNIX-only territory, will ValueError here
                # on Windows
                signal.signal(signal.SIGHUP, kill_bots)
                # TODO: reload config on SIGUSR1
                # signal.signal(signal.SIGUSR1, lambda x, y: bot.do_next_tick(bot.reread_config))
            except AttributeError:
                log.debug(
                    "Cannot set all signals -- not available on this platform")
            bot.run()
        finally:
            if ctx.obj['pidfile']:
                os.unlink(ctx.obj['pidfile'])
    except errors.NoBotsAvailable:
        sys.exit(70)  # 70= "Software error" in /usr/include/sysexts.h


@main.command()
@click.pass_context
def configure(ctx):
    """ Interactively configure dexbot
    """
    cfg_file = ctx.obj["configfile"]
    if os.path.exists(ctx.obj['configfile']):
        with open(ctx.obj["configfile"]) as fd:
            config = yaml.load(fd)
    else:
        config = {}
        storage.mkdir_p(os.path.dirname(ctx.obj['configfile']))
    configure_dexbot(config)
    with open(cfg_file, "w") as fd:
        yaml.dump(config, fd, default_flow_style=False)
    click.echo("new configuration saved")
    if config['systemd_status'] == 'installed':
        # we are already installed
        click.echo("restarting dexbot daemon")
        os.system("systemctl --user restart dexbot")
    if config['systemd_status'] == 'install':
        os.system("systemctl --user enable dexbot")
        click.echo("starting dexbot daemon")
        os.system("systemctl --user start dexbot")


def bot_job(bot, job):
    return lambda x, y: bot.do_next_tick(job)


if __name__ == '__main__':
    main()
