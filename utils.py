import configparser
import functools
import inspect
import json
import logging
import os
import six
from six.moves import StringIO
import socket
from subprocess import Popen, PIPE
import time

import etcd3
from etcd3 import exceptions as etcd_exceptions
import pudb
import tenacity


APPDIR = os.path.expanduser("~/projects/photoviewer")
CONFIG_FILE = os.path.join(APPDIR, "photo.cfg")
HEARTBEAT_FLAG_FILE = os.path.join(APPDIR, "HEARTBEAT")

LOG = None
LOG_LEVEL = logging.INFO
LOG_DIR = os.path.join(APPDIR, "log")
# Make sure that all the necessary directories exist.
for pth in (APPDIR, LOG_DIR):
    os.makedirs(pth, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "photo.log")

MINUTE_SECS = 60
HOUR_SECS = MINUTE_SECS * 60
DAY_SECS = HOUR_SECS * 24
RETRY_INTERVAL = 5
etcd_client = None
BASE_KEY = "/{pkid}:"


class EtcdConnectionError(Exception):
    pass


def logit(level, *msgs):
    if not LOG:
        _setup_logging()
    text = " ".join([f"{msg}" for msg in msgs])
    log_method = getattr(LOG, level)
    log_method(text)


info = functools.partial(logit, "info")
debug = functools.partial(logit, "debug")
error = functools.partial(logit, "error")


def enc(val):
    """Returns the passed value utf-8 encoded if it is a string, or unchanged
    if it is already bytes.
    """
    try:
        return val.encode("utf-8")
    except AttributeError:
        # Not a string
        return val


def trace():
    pudb.set_trace()


def runproc(cmd, wait=True):
    if wait:
        kwargs = dict(stdin=PIPE, stdout=PIPE, stderr=PIPE)
    else:
        kwargs = dict(stdin=None, stdout=None, stderr=None)
    proc = Popen([cmd], shell=True, close_fds=True, **kwargs)
    if wait:
        stdout_text, stderr_text = proc.communicate()
        return stdout_text.decode("utf-8"), stderr_text.decode("utf-8")


def human_time(seconds):
    """Given a time value in seconds, returns a more human-readable string."""
    days, seconds = divmod(seconds, DAY_SECS)
    hours, seconds = divmod(seconds, HOUR_SECS)
    minutes, seconds = divmod(seconds, MINUTE_SECS)
    seconds = round(seconds, 2)
    if days:
        return f"{int(days)}d, {int(hours)}h, {int(minutes)}m, {seconds}s"
    elif hours:
        return f"{int(hours)}h, {int(minutes)}m, {seconds}s"
    elif minutes:
        return f"{int(minutes)}m, {seconds}s"
    return f"{seconds}s"


def parse_config_file():
    parser = configparser.ConfigParser()
    try:
        parser.read(CONFIG_FILE)
    except configparser.MissingSectionHeaderError as e:
        # The file exists, but doesn't have the correct format.
        raise Exception("Invalid Configuration File")
    return parser


def safe_get(parser, section, option, default=None):
    try:
        return parser.get(section, option)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return default


def set_heartbeat_flag():
    with open(HEARTBEAT_FLAG_FILE, "w"):
        pass


def clear_heartbeat_flag(val=None):
    if os.path.exists(HEARTBEAT_FLAG_FILE):
        os.unlink(HEARTBEAT_FLAG_FILE)


def normalize_interval(time_, units):
    unit_key = units[0].lower()
    factor = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit_key, 1)
    return time_ * factor


@tenacity.retry(wait=tenacity.wait_exponential())
def get_etcd_client():
    global etcd_client
    if not etcd_client:
        etcd_client = etcd3.client(host="dodata")
        debug("Created client", etcd_client)
    # Make sure the client connection is still good
    try:
        status = etcd_client.status()
        debug("Client status OK")
    except etcd_exceptions.ConnectionFailedError as e:
        error("Couldn't connect to the etcd server")
        raise EtcdConnectionError
    return etcd_client


def read_key(key):
    """Returns the value of the specified key, or None if it is not present."""
    clt = get_etcd_client()
    val, meta = clt.get(key)
    if val is not None:
        val = six.ensure_text(val)
        return json.loads(val)


def write_key(key, val):
    clt = get_etcd_client()
    payload = json.dumps(val)
    clt.put(key, payload)


def watch(prefix, callback):
    """Watches the specified prefix for changes, and posts those changes to the
    supplied callback function. The callback must accept two parameters,
    representing the key and value.
    """
    info("Starting watch for", prefix)
    debug("WATCH", prefix, callback)

    while True:
        clt = None
        while not clt:
            try:
                clt = get_etcd_client()
            except EtcdConnectionError:
                info("FAILED TO GET CLIENT; SLEEPING...")
                time.sleep(RETRY_INTERVAL)
        try:
            debug(f"WATCHING PREFIX '{prefix}'")
            event = clt.watch_prefix_once(prefix, timeout=30)
            info("Got etcd event")
            debug("Event", type(event), event)
            # Make sure it isn't a connection event
            if not hasattr(event, "key"):
                error(str(event))
                continue
            full_key = str(event.key, "UTF-8")
            key = full_key.split(prefix)[-1]
            value = str(event.value, "UTF-8")
            data = json.loads(value)
            callback(key, data)
        except ValueError as e:
            debug("VALUE ERROR!")
        except etcd_exceptions.WatchTimedOut as e:
            debug("TIMED OUT")


def check_port(port, host="localhost"):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((host, port))
    # A result of zero means the port is open
    return result == 0


def check_browser():
    out, err = runproc("pgrep chromium")
    return bool(out)


def start_browser(url):
    cmd = (
        "chromium-browser --kiosk --ignore-certificate-errors "
        f"--disable-restore-session-state {url}"
    )
    info(f"Starting webbrowser with command: {cmd}")
    out, err = runproc(cmd)
    info(f"Result: {out}. Error: {err}")


def _setup_logging():
    global LOG
    LOG = logging.getLogger("photo")
    hnd = logging.FileHandler(LOG_FILE or "temp_logit.log")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    hnd.setFormatter(formatter)
    LOG.addHandler(hnd)
    LOG.setLevel(LOG_LEVEL)


def set_log_file(pth):
    global LOG_FILE
    LOG_FILE = pth


def set_log_level(level):
    if not LOG:
        _setup_logging()
    info("Setting log level to", level)
    LOG.setLevel(getattr(logging, level))


def log_point(msg="", levels=None):
    if levels is None:
        # Default to 6, which works in most cases
        levels = 6
    stack = inspect.stack()
    # get rid of logPoint's part of the stack:
    stack = stack[1:]
    stack.reverse()
    output = StringIO()
    if msg:
        output.write(ustr(msg) + "\n")

    start_level = -1 * levels
    stackSection = stack[start_level:]
    for stackLine in stackSection:
        frame, filename, line, funcname, lines, unknown = stackLine
        if filename.endswith("/unittest.py"):
            # unittest.py code is a boring part of the traceback
            continue
        if filename.startswith("./"):
            filename = filename[2:]
        output.write(f"{filename}:{line} in {funcname}:\n")
        if lines:
            output.write("    {" ".join(lines)[:-1]}\n")
    s = output.getvalue()
    # I actually logged the result, but you could also print it:
    return s
