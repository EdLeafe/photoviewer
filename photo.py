# /usr/bin/env python3
import configparser
import datetime
import http.server
import os
import random
import signal
import socketserver
import sys
from threading import Thread, Timer
import time
import urllib.parse

import requests

import utils
from utils import debug, enc, error, info, runproc, BASE_KEY, CONFIG_FILE

MONITOR_CMD = "echo 'on 0' | /usr/bin/cec-client -s -d 1"
BROWSER_CYCLE = 60
# Used to weigh the odds of halflife expiring
HALFLIFE_FACTOR = 0.67

PORT = 9001


def get_freespace():
    stat = os.statvfs(".")
    freespace = stat.f_frsize * stat.f_bavail
    debug("Free disk space =", freespace)
    return freespace


def get_log_content(path):
    parts = path.split("?")
    line_count = 1000
    term = ""
    if len(parts) > 1:
        qp = parts[1]
        qparts = qp.split("&")
        for qpart in qparts:
            key, val = qpart.split("=")
            if key == "size":
                line_count = int(val)
            elif key == "filter":
                term = val
    return f"<pre>{utils.read_log(line_count, term)}</pre>"


def run_webserver(mgr):
    with socketserver.TCPServer(("0.0.0.0", PORT), PhotoHandler) as httpd:
        httpd.mgr = mgr
        info("Webserver running on port", PORT)
        httpd.serve_forever()


class PhotoHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        debug(f"do_GET called; path={self.path}")
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
            debug(f"Writing photo URL to browser: {url}")
            self.wfile.write(enc(url))
        elif self.path.startswith("/log"):
            debug("Log called!")
            self.send_response(200)
            self.end_headers()
            content = get_log_content(self.path)
            self.wfile.write(enc(content))
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
        self._show_start = None
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
        self._set_signals()
        self.set_timer()
        debug("In start(); checking webserver")
        while not self.check_webserver():
            debug("Port not listening; restarting webserver")
            time.sleep(2)
        self._started = True
        self.show_photo()
        self.main_loop()

    def main_loop(self):
        """Listen for changes on the key for this host."""
        debug("Entering main loop; watching", self.watch_key)
        # Sometimes the first connection can be very slow - around 2 minutes!
        power_key = f"{self.watch_key}power_state"
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
        time.sleep(5)
        self.check_webserver()

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

    def _calc_interval(self):
        if self.use_halflife:
            # Check every minute
            debug(f"Halflife interval of {self.interval} seconds; setting check in 60 seconds")
            return 60
        else:
            diff = self.interval * (self.variance_pct / 100)
            return round(random.uniform(self.interval - diff, self.interval + diff))

    def set_timer(self, start=True):
        interval = self._calc_interval()
        if self.photo_timer:
            self.photo_timer.cancel()
        self.photo_timer = Timer(interval, self.on_timer_expired)
        debug(
            f"{'Halflife' if self.use_halflife else 'Variance'} timer {id(self.photo_timer)} "
            f"created with interval {interval}"
        )
        if not self.use_halflife:
            next_change = datetime.datetime.now() + datetime.timedelta(seconds=interval)
            info(
                f"New interval: {utils.human_time(interval)}. Next photo change scheduled for "
                f"{next_change.strftime('%H:%M:%S')}"
            )
        if start:
            self.photo_timer.start()
            self.timer_start = time.time()
            log_mthd = debug if self.use_halflife else info
            log_mthd("Timer started")

    def on_timer_expired(self):
        log_mthd = debug if self.use_halflife else info
        log_mthd("Timer Expired")
        if self.use_halflife:
            return self.check_halflife_expired()
        self.reset_timer()

    def reset_timer(self):
        self._register(heartbeat=True)
        self.check_webserver()
        self.navigate()

    def check_halflife_expired(self):
        interval_minutes = self.interval / 60
        threshold = HALFLIFE_FACTOR / interval_minutes
        rand_num = random.random()
        debug(f"Halflife threshold: {threshold}; val: {rand_num}")
        if rand_num < threshold:
            # Halflife triggered!
            self.reset_timer()
        else:
            self.set_timer()

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

    def _reboot(self, val):
        cmd = "/usr/bin/sudo reboot now"
        info("reboot called")
        runproc(cmd, wait=False)

    def process_event(self, key, val):
        info("process_event called; clearing heartbeat flag")
        utils.clear_heartbeat_flag()
        actions = {
            "power_state": self._set_power_state,
            "change_photo": self._change_photo,
            "settings": self._set_settings,
            "images": self._set_images,
            "reboot": self._reboot,
        }
        info(f"Received key: {key} and val: {val}")
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
        parser = utils.parse_config_file()

        self.pkid = utils.safe_get(parser, "frame", "pkid")
        self.watch_key = BASE_KEY.format(pkid=self.pkid)
        settings_key = f"{self.watch_key}settings"
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
            # Do we use halflife decay pattern for the interval?
            self.use_halflife = bool(settings.get("use_halflife", False))
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
            # Do we use halflife decay pattern for the interval?
            self.use_halflife = False
            self.brightness = 1.0
            self.contrast = 1.0
            self.saturation = 1.0

        utils.set_log_level(self.log_level)
        self.reg_url = utils.safe_get(parser, "host", "reg_url")
        if not self.reg_url:
            error("No registration URL in photo.cfg; exiting")
            sys.exit()
        self.dl_url = utils.safe_get(parser, "host", "dl_url")
        if not self.dl_url:
            error("No download URL configured in photo.cfg; exiting")
            sys.exit()
        self.interval = utils.normalize_interval(self.interval_time, self.interval_units)
        self.set_image_interval()
        self._in_read_config = False

    def set_image_interval(self):
        if not self.photo_timer:
            # Starting up
            return
        self.set_timer()

    def _register(self, heartbeat=False):
        if heartbeat:
            # Set the heartbeat flag
            info("Setting heartbeat file...")
            utils.set_heartbeat_flag()
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
            pkid, images = resp.json()
            if pkid != self.pkid:
                parser = utils.parse_config_file()
                parser.set("frame", "pkid", pkid)
                with open(CONFIG_FILE, "w") as ff:
                    parser.write(ff)
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
            debug("AAAAHHHHHHH!!!!!!!!")
            info("Webserver port not listening; restarting")
            time.sleep(3)
            self.start_server()
            return False
        return True

    def navigate(self, signum=None, forward=True, frame=None):
        """Moves to the next image."""
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
        if self._show_start:
            elapsed = datetime.datetime.now() - self._show_start
            info(
                f"Halflife: changing photo after {utils.human_time(elapsed.seconds)} seconds; "
                f"halflife={utils.human_time(self.interval)}"
            )
        self._show_start = datetime.datetime.now()
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
        parser = utils.parse_config_file()
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
                parser.set(section, key, str(val))
                changed = True
                new_interval = new_interval or "interval" in key
        if changed:
            with open(CONFIG_FILE, "w") as ff:
                parser.write(ff)
        if new_interval:
            self.interval = utils.normalize_interval(self.interval_time, self.interval_units)
            info("Setting timer to", self.interval)
            self.set_image_interval()

    def kill_timer(self):
        """Kills the photo timer on receiving a Ctrl-C."""
        info("Killing timer")
        self.photo_timer.cancel()
        info("Timer canceled")


if __name__ == "__main__":
    with open("photo.pid", "w") as ff:
        ff.write(f"{os.getpid()}")
    img_mgr = ImageManager()
    try:
        debug("And we're off!")
        img_mgr.start()
    except KeyboardInterrupt:
        img_mgr.kill_timer()
