import configparser
import datetime
import os
from subprocess import Popen, PIPE
import sys
import time

HOMEDIR = os.path.expanduser("~")
APPDIR = os.path.join(HOMEDIR, "projects/photoviewer")
CONFIG_FILE = os.path.join(APPDIR, "photo.cfg")
HEARTBEAT_FLAG_FILE = "/tmp/PHOTOVIEWER.heartbeat"
PARSER = configparser.ConfigParser()
if sys.platform == "darwin":
    RESTART_COMMAND = (
        f"cd {APPDIR}; kill -9 `cat photo.pid`; source {HOMEDIR}/venvs/viewer/bin/activate; "
        f"python photo.py & ; rm -f {HEARTBEAT_FLAG_FILE}"
    )
else:
    RESTART_COMMAND = "sudo systemctl restart photoviewer.service"


def runproc(cmd, wait=True):
    if wait:
        kwargs = dict(stdin=PIPE, stdout=PIPE, stderr=PIPE)
    else:
        kwargs = dict(stdin=None, stdout=None, stderr=None)
    proc = Popen([cmd], shell=True, close_fds=True, **kwargs)
    if wait:
        stdout_text, stderr_text = proc.communicate()
        return stdout_text.decode("utf-8"), stderr_text.decode("utf-8")


def safe_get(section, option, default=None):
    try:
        return PARSER.get(section, option)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return default


def normalize_interval(time_, units):
    unit_key = units[0].lower()
    factor = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit_key, 1)
    return time_ * factor


def get_interval():
    try:
        PARSER.read(CONFIG_FILE)
    except configparser.MissingSectionHeaderError as e:
        # The file exists, but doesn't have the correct format.
        raise Exception("Invalid Configuration File")
    # How often the image is changed
    interval_time = int(safe_get("frame", "interval_time", 60))
    # Units of time for the image change interval
    interval_units = safe_get("frame", "interval_units", "minutes")
    interval = normalize_interval(interval_time, interval_units)
    return interval


def since_heartbeat():
    if not os.path.exists(HEARTBEAT_FLAG_FILE):
        return -1
    info = os.stat(HEARTBEAT_FLAG_FILE)
    local_time = time.localtime(info.st_atime)
    last_access = time.mktime(local_time)
    return time.time() - last_access


def restart():
    runproc(RESTART_COMMAND)


def printit(txt):
    tm = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("/home/pi/projects/photoviewer/log/heartbeat.log", "a") as ff:
        ff.write(f"{tm} {txt}\n")


def main():
    elapsed = since_heartbeat()
    interval = get_interval()
    if elapsed > (2 * interval):
        printit("RESTARTING!")
        restart()
    else:
        printit(f"Cool - elapsed = {elapsed}, interval = {interval}")


if __name__ == "__main__":
    main()
