#!/usr/bin/env python3
import yaml
import logging
import click
import signal
import os.path
import os
import sys
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


from dexbot.bot import BotInfrastructure
from dexbot.cli_conf import configure_dexbot
import dexbot.errors as errors


log = logging.getLogger(__name__)

# inital logging before proper setup.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)


@click.group()
@click.option(
    "--configfile",
    default="config.yml",
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
    try:
        bot = BotInfrastructure(ctx.config)
        # set up signalling. do it here as of no relevance to GUI
        killbots = lambda x, y: bot.do_next_tick(bot.stop)
        # these first two UNIX & Windows
        signal.signal(signal.SIGTERM, killbots)
        signal.signal(signal.SIGINT, killbots)
        try:
            # these signals are UNIX-only territory, will ValueError here on Windows
            signal.signal(signal.SIGHUP, killbots)
            # future plan: reload config on SIGUSR1
            #signal.signal(signal.SIGUSR1, lambda x, y: bot.do_next_tick(bot.reread_config))
            signal.signal(signal.SIGUSR2, lambda x, y: bot.do_next_tick(bot.report_now))
        except ValueError:
            log.debug("Cannot set all signals -- not avaiable on this platform")
        bot.init_bots()
        if ctx.obj['systemd']:
            try:
                import sdnotify  # a soft dependency on sdnotify -- don't crash on non-systemd systems
                n = sdnotify.SystemdNotifier()
                n.notify("READY=1")
            except BaseException:
                log.debug("sdnotify not available")
        bot.notify.listen()
    except errors.NoBotsAvailable:
        sys.exit(70)  # 70= "Software error" in /usr/include/sysexts.h


@main.command()
@click.pass_context
@verbose
def configure(ctx):
    """ Interactively configure dexbot
    """
    if os.path.exists(ctx.obj['configfile']):
        with open(ctx.obj["configfile"]) as fd:
            config = yaml.load(fd)
    else:
        config = {}
    configure_dexbot(config)
    cfg_file = ctx.obj["configfile"]
    if not "/" in cfg_file:  # save to home directory unless user wants something else
        cfg_file = os.path.expanduser("~/" + cfg_file)
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


if __name__ == '__main__':
    main()
