import inspect
import logging
from subprocess import Popen, PIPE

from six.moves import StringIO


LOG = None
LOG_LEVEL = logging.INFO

def runproc(cmd, wait=True):
    if wait:
        kwargs = dict(stdin=PIPE, stdout=PIPE, stderr=PIPE)
    else:
        kwargs = dict(stdin=None, stdout=None, stderr=None)
    proc = Popen([cmd], shell=True, close_fds=True, **kwargs)
    if wait:
        stdout_text, stderr_text = proc.communicate()
        return stdout_text.decode("utf-8"), stderr_text.decode("utf-8")


def _setup_logging():
    global LOG
    LOG = logging.getLogger("photo")
    hnd = logging.FileHandler("log/photo.log")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    hnd.setFormatter(formatter)
    LOG.addHandler(hnd)
    LOG.setLevel(LOG_LEVEL)


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
