# /usr/bin/env python3
import configparser
import datetime
import http.server
import os
import random
import signal
import socketserver
from threading import Thread, Timer
import time

import requests

import utils
from utils import debug, enc, error, info, runproc

APPDIR = os.path.expanduser("~/projects/photoviewer")
LOG_DIR = os.path.join(APPDIR, "log")
# Make sure that all the necessary directories exist.
for pth in (APPDIR, LOG_DIR):
    os.makedirs(pth, exist_ok=True)
utils.set_log_file(os.path.join(LOG_DIR, "photo.log"))

CONFIG_FILE = os.path.join(APPDIR, "photo.cfg")
BASE_KEY = "/{pkid}:"
MONITOR_CMD = "echo 'on 0' | /usr/bin/cec-client -s -d 1"
BROWSER_CYCLE = 60

PORT = 9001


def swapext(pth, ext):
    """Replaces the extension for the specified path with the supplied
    extension.
    """
    ext = ext.lstrip(".")
    dirname = os.path.dirname(pth)
    basename = os.path.basename(pth)
    newname = "%s.%s" % (os.path.splitext(basename)[0], ext)
    return os.path.join(dirname, newname)


def fb_path(pth):
    """Given a path to an image file, returns the corresponding path the the
    frame buffer directory with the same name but with the '.fb' extension.
    """
    img_name = os.path.basename(pth)
    fb_name = swapext(img_name, "fb")
    return os.path.join(FB_PHOTODIR, fb_name)


def clean_fb(pth):
    """Deletes the frame buffer version of an image if it exists"""
    fb = fb_path(pth)
    if os.path.exists(fb):
        os.unlink(fb)


def _normalize_interval(time_, units):
    unit_key = units[0].lower()
    factor = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit_key, 1)
    return time_ * factor


def get_freespace():
    stat = os.statvfs(".")
    freespace = stat.f_frsize * stat.f_bavail
    debug("Free disk space =", freespace)
    return freespace


def run_webserver(mgr):
    with socketserver.TCPServer(("", PORT), PhotoHandler) as httpd:
        httpd.mgr = mgr
        info("Webserver running on port", PORT)
        httpd.serve_forever()


class PhotoHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/status":
            mgr = self.server.mgr
            url = mgr.get_url()
            start = time.time()
            while not url:
                if (time.time() - start) > BROWSER_CYCLE:
                    url = mgr.get_last_url()
                    break
                time.sleep(1)
                url = mgr.get_url()
            mgr.clear_url()
            self.send_response(200)
            self.end_headers()
            debug("Writing photo URL to browser")
            self.wfile.write(enc(url))
        else:
            with open("main.html") as ff:
                html = ff.read()
            debug("Writing HTML to browser")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(enc(html))


class ImageManager(object):
    def __init__(self):
        self._started = False
        self._in_read_config = False
        self.photo_timer = None
        self.timer_start = None
        self.photo_url = ""
        self.last_url = ""
        self.parser = configparser.ConfigParser()
        self._read_config()
        self.initial_interval = self._set_start()
        self._set_power_on()
        self.in_check_host = False
        self.image_list = []
        self.displayed_name = ""
        self.image_index = 0
        self._register()
        self.start_server()

    def start(self):
#        self.check_webbrowser()
        self._set_signals()
        self.set_timer()
        self._started = True
        self.show_photo()
        self.main_loop()

    def main_loop(self):
        """Listen for changes on the key for this host."""
        debug("Entering main loop; watching", self.watch_key)
        # Sometimes the first connection can be very slow - around 2 minutes!
        power_key = "{}power_state".format(self.watch_key)
        debug("Power key:", power_key)
        power_state = utils.read_key(power_key)
        debug("Power State:", power_state)
        self._set_power_state(power_state)
        callback = self.process_event
        utils.watch(self.watch_key, callback)
        # Shouldn't reach here.
        sys.exit(0)

    def start_server(self):
        t = Thread(target=run_webserver, args=(self,))
        t.start()
        debug("Webserver started")

    def _set_power_on(self):
        # Power on the monitor and HDMI output
        debug("Powering on the monitor")
        out, err = runproc(MONITOR_CMD)
        debug("Power result", out.strip(), err.strip())

    def _set_start(self):
        now = datetime.datetime.now()
        base_hour, base_minute = self.interval_base.split(":")
        start_hour = now.hour if base_hour == "*" else int(base_hour)
        if base_minute == "*":
            start_minute = now.minute
        else:
            if start_hour == now.hour:
                # We want to make up the offset from the current time and the
                # start hour. First, determine how many minutes until we hit
                # the start_minute.
                diff = int(base_minute) - now.minute
                if diff < 0:
                    diff += 60
                interval_minutes = int(self.interval / 60)
                start_minute = now.minute + (diff % interval_minutes)
            else:
                start_minute = base_minute
        start_hour = start_hour if start_minute >= now.minute else start_hour + 1
        start_day = now.day if start_hour >= now.hour else now.day + 1
        if start_minute >= 60:
            start_minute = start_minute % 60
            start_hour += 1
        if start_hour >= 24:
            start_hour = start_hour % 24
            start_day += 1
        start = now.replace(
            day=start_day, hour=start_hour, minute=start_minute, second=0, microsecond=0
        )
        offset = start - now
        offset_secs = offset.total_seconds()
        return offset_secs if offset_secs > 0 else 0

    def set_timer(self, start=True):
        diff = self.interval * (self.variance_pct / 100)
        interval = round(random.uniform(self.interval - diff, self.interval + diff))
        if self.photo_timer:
            self.photo_timer.cancel()
        self.photo_timer = Timer(interval, self.on_timer_expired)
        debug("Timer {} created with interval {}".format(id(self.photo_timer), interval))
        next_change = datetime.datetime.now() + datetime.timedelta(seconds=interval)
        info("Next photo change scheduled for {}".format(next_change.strftime("%H:%M:%S")))
        if start:
            self.photo_timer.start()
            self.timer_start = time.time()
            info("Timer started")

    def on_timer_expired(self):
        info("Timer Expired")
#        self.check_webbrowser()
        self._register(heartbeat=True)
        self.check_webserver()
        self.navigate()

    def _set_signals(self):
        signal.signal(signal.SIGHUP, self._read_config)
        signal.signal(signal.SIGTSTP, self.pause)
        signal.signal(signal.SIGCONT, self.resume)
        signal.signal(signal.SIGTRAP, self.navigate)

    @staticmethod
    def _set_power_state(val):
        if val and val.lower() in ("stop", "off"):
            sys.exit()

    def _change_photo(self, val):
        # I want to default to forward unless there is a specific request to
        # move backwards.
        forward = val[:4] != "back"
        self.navigate(forward=forward)

    def _set_settings(self, val):
        """The parameter 'val' will be a dict in the format of:
            setting name: setting value
        """
        self._update_config(val)

    def _set_images(self, val):
        self.image_list = val
        self.navigate()

    def process_event(self, key, val):
        debug("process_event called")
        actions = {
            "power_state": self._set_power_state,
            "change_photo": self._change_photo,
            "settings": self._set_settings,
            "images": self._set_images,
        }
        info("Received key: {key} and val: {val}".format(key=key, val=val))
        mthd = actions.get(key)
        if not mthd:
            error("Unknown action received:", key, val)
            return
        mthd(val)

    def pause(self, signum=None, frame=None):
        self.photo_timer.cancel()
        info("Photo timer stopped")

    def resume(self, signum=None, frame=None):
        self.set_timer()
        info("Photo timer started")

    def _read_config(self, signum=None, frame=None):
        if self._in_read_config:
            # Another process already called this
            return
        self._in_read_config = True
        info("_read_config called!")
        try:
            self.parser.read(CONFIG_FILE)
        except configparser.MissingSectionHeaderError as e:
            # The file exists, but doesn't have the correct format.
            raise exc.InvalidConfigurationFile(e)

        def safe_get(section, option, default=None):
            try:
                return self.parser.get(section, option)
            except (configparser.NoSectionError, configparser.NoOptionError):
                return default

        self.pkid = safe_get("frame", "pkid")
        self.watch_key = BASE_KEY.format(pkid=self.pkid)
        settings_key = "{}settings".format(self.watch_key)
        settings = utils.read_key(settings_key)
        if settings:
            self.log_level = settings.get("log_level", "INFO")
            self.name = settings.get("name", "undefined")
            self.description = settings.get("description", "")
            self.orientation = settings.get("orientation", "H")
            # When to start the image rotation
            self.interval_base = settings.get("interval_base", "*:*")
            # How often to change image
            self.interval_time = int(settings.get("interval_time", 10))
            # Units of time for the image change interval
            self.interval_units = settings.get("interval_units", "minutes")
            # Percentage to vary the display time from photo to photo
            self.variance_pct = int(settings.get("variance_pct", 0))
            self.brightness = settings.get("brightness", 1.0)
            self.contrast = settings.get("contrast", 1.0)
            self.saturation = settings.get("saturation", 1.0)
        else:
            self.log_level = "INFO"
            self.name = "undefined"
            self.description = ""
            self.orientation = "H"
            # When to start the image rotation
            self.interval_base = "*:*"
            # How often to change image
            self.interval_time = 10
            # Units of time for the image change interval
            self.interval_units = "minutes"
            # Percentage to vary the display time from photo to photo
            self.variance_pct = 0
            self.brightness = 1.0
            self.contrast = 1.0
            self.saturation = 1.0

        utils.set_log_level(self.log_level)
        self.reg_url = safe_get("host", "reg_url")
        if not self.reg_url:
            error("No registration URL in photo.cfg; exiting")
            sys.exit()
        self.dl_url = safe_get("host", "dl_url")
        if not self.dl_url:
            error("No download URL configured in photo.cfg; exiting")
            sys.exit()
        self.interval = _normalize_interval(self.interval_time, self.interval_units)
        self.set_image_interval()
        self._in_read_config = False

    def set_image_interval(self):
        if not self.photo_timer:
            # Starting up
            return
        self.set_timer()

    def _register(self, heartbeat=False):
        headers = {"user-agent": "photoviewer"}
        # Get free disk space
        freespace = get_freespace()
        data = {
            "pkid": self.pkid,
            "freespace": freespace,
        }
        resp = requests.post(self.reg_url, data=data, headers=headers)
        if 200 <= resp.status_code <= 299:
            # Success!
            if heartbeat:
                # Just need to ping the server and not update config
                return
            pkid, images = resp.json()
            if pkid != self.pkid:
                self.parser.set("frame", "pkid", pkid)
                with open(CONFIG_FILE, "w") as ff:
                    self.parser.write(ff)
            self.image_list = images
            random.shuffle(self.image_list)
        else:
            error(resp.status_code, resp.text)
            sys.exit()

    @staticmethod
    def check_webbrowser():
        if not utils.check_browser():
            info("Web browser not running; restarting")
            utils.start_browser()

    def check_webserver(self):
        if not utils.check_port(PORT):
            info("Webserver port not listening; restarting")
            self.start_server()

    def navigate(self, signum=None, forward=True, frame=None):
        """Moves to the next image. """
        debug("navigate called; current index", self.image_index)

        num_images = len(self.image_list)
        if not num_images:
            # Currently no images specified for this display, so just return.
            return
        delta = 1 if forward else -1
        new_index = self.image_index + delta
        # Boundaries
        max_index = len(self.image_list) - 1
        min_index = 0
        if new_index > max_index:
            new_index = 0
            # Shuffle the images
            info("All images shown; shuffling order.")
            random.shuffle(self.image_list)
        elif new_index < min_index:
            new_index = max_index
        else:
            new_index = max(0, min(max_index, new_index))
        debug("image index", self.image_index)
        debug("new index", new_index)
        if new_index != self.image_index:
            self.image_index = new_index
            self.show_photo()
        elif new_index == 0:
            self.show_photo()
        self.set_timer()

    def show_photo(self):
        if not self._started:
            return
        if not self.image_list:
            return
        try:
            fname = self.image_list[self.image_index]
        except IndexError as e:
            error("BAD INDEX", e)
            if self.image_list:
                fname = self.image_list[-1]
            else:
                # Something's screwy
                error("No images!")
                return

        if fname == self.displayed_name:
            return
        if self.timer_start:
            elapsed = round(time.time() - self.timer_start, 2)
            if elapsed:
                info("Elapsed time:", utils.human_time(elapsed))
        info("Showing photo", fname)
        self.photo_url = self.last_url = os.path.join(self.dl_url, fname)

    def get_url(self):
        return self.photo_url

    def get_last_url(self):
        return self.last_url

    def clear_url(self):
        self.photo_url = ""

    def _update_config(self, data):
        changed = False
        new_interval = False
        if "log_level" in data:
            self.log_level = data["log_level"]
            utils.set_log_level(self.log_level)
        for key in (
            "name",
            "description",
            "interval_time",
            "interval_units",
            "variance_pct",
            "brightness",
            "contrast",
            "saturation",
        ):
            val = data.get(key, None)
            debug("key:", key, "; val:", val)
            if val is None:
                continue
            local_val = getattr(self, key)
            if local_val != val:
                try:
                    typ = type(local_val)
                    converted = typ(val)
                except Exception:
                    pass
                setattr(self, key, converted)
                monitor_keys = ("brightness", "contrast", "saturation")
                section = "monitor" if key in monitor_keys else "frame"
                self.parser.set(section, key, str(val))
                changed = True
                new_interval = new_interval or "interval" in key
        if changed:
            with open(CONFIG_FILE, "w") as ff:
                self.parser.write(ff)
        if new_interval:
            self.interval = _normalize_interval(self.interval_time, self.interval_units)
            info("Setting timer to", self.interval)
            self.set_image_interval()

    def kill_timer(self):
        """Kills the photo timer on receiving a Ctrl-C."""
        info("Killing timer")
        self.photo_timer.cancel()
        info("Timer canceled")


if __name__ == "__main__":
    with open("photo.pid", "w") as ff:
        ff.write("%s" % os.getpid())
    img_mgr = ImageManager()
    try:
        debug("And we're off!")
        img_mgr.start()
    except KeyboardInterrupt:
        img_mgr.kill_timer()
