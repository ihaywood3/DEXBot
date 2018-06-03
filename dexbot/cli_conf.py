"""
A module to provide an interactive text-based tool for dexbot configuration
The result is dexbot can be run without having to hand-edit config files.
If systemd is detected it will offer to install a user service unit (under ~/.local/share/systemd
This requires a per-user systemd process to be runnng

Requires the 'whiptail' tool for text-based configuration (so UNIX only)
if not available, falls back to a line-based configurator ("NoWhiptail")

Note there is some common cross-UI configuration stuff: look in basestrategy.py
It's expected GUI/web interfaces will be re-implementing code in this file, but they should
understand the common code so worker strategy writers can define their configuration once
for each strategy class.

"""


import importlib
import os
import os.path
import sys
import collections
import re
import tempfile
import shutil

from bitshares import BitShares
import bitshares.exceptions

from dexbot.whiptail import get_whiptail

# FIXME: auto-discovery of strategies would be cool but can't figure out a way
STRATEGIES = [
    {'tag': 'relative',
     'class': 'dexbot.strategies.relative_orders',
     'name': 'Relative Orders'},
    {'tag': 'stagger',
     'class': 'dexbot.strategies.staggered_orders',
     'name': 'Staggered Orders'}]

SYSTEMD_SERVICE_NAME = os.path.expanduser(
    "~/.local/share/systemd/user/dexbot.service")

SYSTEMD_SERVICE_FILE = """
[Unit]
Description=DEXBot

[Service]
Type=notify
WorkingDirectory={homedir}
ExecStart={exe} --systemd run
TimeoutSec=20m
Environment=PYTHONUNBUFFERED=true
Environment=UNLOCK={passwd}

[Install]
WantedBy=default.target
"""

bitshares_instance = None


def select_choice(current, choices):
    """for the radiolist, get us a list with the current value selected"""
    return [(tag, text, (current == tag and "ON") or "OFF")
            for tag, text in choices]


def process_config_element(elem, d, config):
    """
    process an item of configuration metadata display a widget as appropriate
    d: the Dialog object
    config: the config dctionary for this worker
    """
    if elem.type == "string":
        txt = d.prompt(elem.description, config.get(elem.key, elem.default))
        if elem.extra:
            while not re.match(elem.extra, txt):
                d.alert("The value is not valid")
                txt = d.prompt(
                    elem.description, config.get(
                        elem.key, elem.default))
        config[elem.key] = txt
    if elem.type == "bool":
        config[elem.key] = d.confirm(elem.description)
    if elem.type in ("float", "int"):
        txt = d.prompt(elem.description, str(config.get(elem.key, elem.default)))
        while True:
            try:
                if elem.type == "int":
                    val = int(txt)
                else:
                    val = float(txt)
                if val < elem.extra[0]:
                    d.alert("The value is too low")
                elif elem.extra[1] and val > elem.extra[1]:
                    d.alert("the value is too high")
                else:
                    break
            except ValueError:
                d.alert("Not a valid value")
            txt = d.prompt(elem.description, str(config.get(elem.key, elem.default)))
        config[elem.key] = val
    if elem.type == "choice":
        config[elem.key] = d.radiolist(elem.description, select_choice(
            config.get(elem.key, elem.default), elem.extra))


def setup_systemd(d, config, shell=False):
    global bitshares_instance
    if config.get("systemd_status", "install") == "reject":
        return  # don't nag user if previously said no
    if not os.path.exists("/etc/systemd"):
        return  # no working systemd
    if os.path.exists(SYSTEMD_SERVICE_NAME):
        # dexbot already installed
        # so just tell cli.py to quietly restart the daemon
        config["systemd_status"] = "installed"
        return
    # if shell systemd is automatic
    if shell or d.confirm(
            "Do you want to install dexbot as a background (daemon) process?"):
        for i in ["~/.local", "~/.local/share",
                  "~/.local/share/systemd", "~/.local/share/systemd/user"]:
            j = os.path.expanduser(i)
            if not os.path.exists(j):
                os.mkdir(j)
        passwd = d.prompt(
            """The wallet password
NOTE: this will be saved on disc so the bot can run unattended. This means
anyone with access to this computer can spend all your money""",
            password=True)
        # because we hold password be restrictive
        fd = os.open(SYSTEMD_SERVICE_NAME, os.O_WRONLY | os.O_CREAT, 0o600)
        exe = sys.argv[0]
        if exe.endswith('-shell'):
            exe = exe[:-6]+'-cli'
        with open(fd, "w") as fp:
            fp.write(
                SYSTEMD_SERVICE_FILE.format(
                    exe=exe,
                    passwd=passwd,
                    homedir=os.path.expanduser("~")))
        # signal cli.py to set the unit up after writing config file
        config['systemd_status'] = 'install'
        # actually set up the wallet if required
        if not bitshares_instance:
            bitshares_instance = BitShares()
        if not bitshares_instance.wallet.created():
            bitshares_instance.wallet.create(passwd)
    else:
        config['systemd_status'] = 'reject'


def configure_worker(d, worker):
    strategy = worker.get('module', 'dexbot.strategies.echo')
    for i in STRATEGIES:
        if strategy == i['class']:
            strategy = i['tag']
    worker['module'] = d.radiolist(
        "Choose a bot strategy", select_choice(
            strategy, [(i['tag'], i['name']) for i in STRATEGIES]))
    for i in STRATEGIES:
        if i['tag'] == worker['module']:
            worker['module'] = i['class']
    # its always Strategy now, for backwards compatibilty only
    worker['bot'] = 'Strategy'
    # import the strategy class but we don't __init__ it here
    klass = getattr(
        importlib.import_module(worker["module"]),
        'Strategy'
    )
    # use class metadata for per-bot configuration
    configs = klass.configure()
    if configs:
        for c in configs:
            process_config_element(c, d, worker)
    else:
        d.alert("This strategy does not have configuration information. You will have to check the worker code and add configuration values to config.yml manually")
    return worker


def setup_reporter(d, config):
    reporter_configs = config.get('reports', [])
    while True:
        action = d.menu("DEXBot can report its actions in various ways.", [
            ("EMAIL", "Sending e-mails at regular intervals"),
            ("JABBER", "Using the Jabber (XMPP) chat system"),
            ("QUIT", "Exit this menu")])
        if action == "QUIT":
            break
        elif action == "EMAIL":
            report_module = 'dexbot.report.mail'
        elif action == "JABBER":
            report_module = 'dexbot.report.jabber'
        mod = importlib.import_module(report_module)
        this_reporter_config = None
        for i in reporter_configs:
            if i['module'] == report_module:
                this_reporter_config = i
        if not this_reporter_config:
            this_reporter_config = {'module': report_module}
            reporter_configs.append(this_reporter_config)
        for element in mod.Reporter.configure():
            process_config_element(element, d, this_reporter_config)
    config['reports'] = reporter_configs


def unlock_wallet(d, bitshares_instance):
    if not bitshares_instance.wallet.unlocked():
        while True:
            try:
                bitshares_instance.wallet.unlock(d.prompt("Enter wallet password", password=True))
                return
            except bitshares.exceptions.WrongMasterPasswordException:
                if not d.confirm("Password wrong, do you want to try again?"):
                    sys.exit(1)


def configure_dexbot(config, shell=False):
    global bitshares_instance
    d = get_whiptail()
    workers = config.get('workers', {})
    config['workers'] = workers
    if len(workers) == 0:
        d.view_text("""Welcome to the DEXBot text-based configuration.
You will be asked to set up at least one bot, this will then run
in the background.
You can use the arrow keys and RETURN to select buttons in these
screens, the mouse does not work. Selecting Cancel will exit
the program.
""")
        while True:
            txt = d.prompt("Your name for the bot")
            config['workers'][txt] = configure_worker(d, {})
            if not d.confirm("Set up another bot?\n(DEXBOt can run multiple bots in one instance)"):
                break
        setup_systemd(d, config, shell)
    else:
        menu = [('NEW', 'Create a new bot'),
                ('DEL', 'Delete a bot'),
                ('EDIT', 'Edit a bot'),
                ('REPORT', 'Configure reporting'),
                ('NODE', 'Set the BitShares node'),
                ('KEY', 'Add a private key'),
                ('WIPE', 'Wipe all private keys')]
        if shell:
            menu.extend([('PASSWD', 'Set the account password'),
                         ('LOGOUT', 'Logout of the server')])
        else:
            menu.append(('QUIT', 'Quit without saving'))
        action = d.menu("You have an existing configuration.\nSelect an action:", menu)
        if action == 'EDIT':
            workername = d.menu("Select bot to edit", [(i, i) for i in workers])
            config['workers'][workername] = configure_worker(d, config['workers'][workername])
        elif action == 'DEL':
            workername = d.menu("Select bot to delete", [(i, i) for i in workers])
            del config['workers'][workername]
        elif action == 'NEW':
            txt = d.prompt("Your name for the new bot")
            config['workers'][txt] = configure_worker(d, {})
        elif action == 'REPORT':
            setup_reporter(d, config)
        elif action == 'NODE':
            config['node'] = d.prompt(
                "BitShares node to use",
                default=config['node'])
        elif action == 'LOGOUT':
            sys.exit()
        elif action == 'PASSWD':
            os.system("passwd")
        elif action == 'KEY':
            if not bitshares_instance:
                bitshares_instance = BitShares()
            if bitshares_instance.wallet.created():
                unlock_wallet(d, bitshares_instance)
            else:
                bitshares_instance.wallet.create(d.prompt("Enter password for new wallet", password=True))
            bitshares_instance.wallet.addPrivateKey(d.prompt("New private key in WIF format (begins with a \"5\")"))
        elif action == 'WIPE':
            if not bitshares_instance:
                bitshares_instance = BitShares()
            if bitshares_instance.wallet.created():
                if d.confirm("Really wipe your wallet of keys?", default='no'):
                    unlock_wallet(d, bitshares_instance)
                    bitshares_instance.wallet.wipe(True)
            else:
                d.alert("No wallet to wipe")
        else:
            d.prompt("Unknown command {}".format(action))
    d.clear()
    return config


if __name__ == '__main__':
    print(repr(configure({})))
