import inspect
import json
import logging
from subprocess import Popen, PIPE

import etcd3
from six.moves import StringIO


LOG = None
LOG_FILE = None
LOG_LEVEL = logging.INFO
etcd_client = None


def runproc(cmd, wait=True):
    if wait:
        kwargs = dict(stdin=PIPE, stdout=PIPE, stderr=PIPE)
    else:
        kwargs = dict(stdin=None, stdout=None, stderr=None)
    proc = Popen([cmd], shell=True, close_fds=True, **kwargs)
    if wait:
        stdout_text, stderr_text = proc.communicate()
        return stdout_text.decode("utf-8"), stderr_text.decode("utf-8")


def get_etcd_client():
    global etcd_client
    if not etcd_client:
        etcd_client = etcd3.client(host="dodata")
    return etcd_client


def make_key(uuid, action):
    return BASE_KEY.format(uuid=uuid, action=action)


def read_key(key):
    """Returns the value of the specified key, or None if it is not present."""
    clt = get_etcd_client()
    val, meta = clt.get(key)
    if val is not None:
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
    clt = get_etcd_client()
    logit("debug", "Starting watch for", prefix)
    events_iterator, cancel = clt.watch_prefix(prefix)
    for event in events_iterator:
        full_key = str(event.key, "UTF-8")
        logit("debug", "Received key:", full_key)
        key = full_key.split(prefix)[-1]
        value = str(event.value, "UTF-8")
        data = json.loads(value)
        logit("debug", "Data:", data)
        logit("debug", "Calling", callback)
        callback(key, data)
    logit("error", "WATCH ENDED" * 22)


def _setup_logging():
    global LOG
    LOG = logging.getLogger("photo")
    hnd = logging.FileHandler(LOG_FILE)
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
    LOG.setLevel(getattr(logging, level))


def logit(level, *msgs):
    if not LOG:
        _setup_logging()
    text = " ".join(["%s" % msg for msg in msgs])
    log_method = getattr(LOG, level)
    log_method(text)


def trace():
    import pudb
    pudb.set_trace()


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

    stackSection = stack[-1*levels:]
    for stackLine in stackSection:
        frame, filename, line, funcname, lines, unknown = stackLine
        if filename.endswith("/unittest.py"):
            # unittest.py code is a boring part of the traceback
            continue
        if filename.startswith("./"):
            filename = filename[2:]
        output.write("%s:%s in %s:\n" % (filename, line, funcname))
        if lines:
            output.write("    %s\n" % "".join(lines)[:-1])
    s = output.getvalue()
    # I actually logged the result, but you could also print it:
    return s
