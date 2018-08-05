#/usr/bin/env python3
from __future__ import print_function

import datetime
import filecmp
import glob
import json
from multiprocessing import Pool
import os
import random
import re
import shutil
import signal
from threading import Thread
from threading import Timer

import requests
import six
import six.moves.configparser as ConfigParser

import image
from utils import logit
from utils import runproc
from utils import set_log_level
from utils import trace


PHOTODIR = "images"
INACTIVE_PHOTODIR = "inactive_images"
DISPLAY_PHOTODIR = "display"
IMG_PAT = re.compile(r".+\.[jpg|jpeg|gif|png]")
CONFIG_FILE = "photo.cfg"
SHOW_CMD = "rm -f %s/display.*; cp %%s %s/display%%s" % (
        DISPLAY_PHOTODIR, DISPLAY_PHOTODIR)
MONITOR_CMD = "echo 'on 0' | cec-client -s -d 1"
VIEWER_CMD = "vcgencmd display_power 1 > /dev/null 2>&1; sudo fbi -a --noverbose -T 1 %s >/dev/null 2>&1"
ONE_MB = 1024 ** 2
ONE_GB = 1024 ** 3


def just_fname(path):
    return os.path.split(path)[-1]


def _normalize_interval(time_, units):
    unit_key = units[0].lower()
    factor = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit_key, 1)
    return time_ * factor


def get_freespace():
    out, err = runproc("df -BM .")
    ret = out.split('\n')[1].split()[3]
    # Remove the trailing 'M'
    ret = ret.replace("M", "")
    ret = int(ret) * ONE_MB
    logit("debug", "Free disk space =", ret)
    return ret


class ImageManager(object):
    def __init__(self):
        self._started = False
        self.photo_timer = self.check_timer = None
        self.parser = ConfigParser.SafeConfigParser()
        self._read_config()
        self.initial_interval = self._set_start()
        self._set_power_on()
        self.in_check_host = False
        self.set_timer("check")
        self.set_timer("photo")

        self.current_images = []
        self.host_images = []
        self.inactive_images = []
        self.displayed_name = ""
        self.image_index = 0
        self.load_images()
        self._register()
        self.start()

    def _set_power_on(self):
        # Power on the monitor and HDMI output
        logit("debug", "Powering on the monitor")
        out, err = runproc(MONITOR_CMD)
        logit("debug", "Power result", out, err)


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
        start_hour = (start_hour if start_minute >= now.minute
                else start_hour + 1)
        start_day = now.day if start_hour >= now.hour else now.day + 1
        if start_minute >= 60:
            start_minute = start_minute % 60
            start_hour += 1
        if start_hour >= 24:
            start_hour = start_hour % 24
            start_day += 1
        start = now.replace(day=start_day, hour=start_hour,
                minute=start_minute, second=0, microsecond=0)
        offset = start - now
        offset_secs = offset.total_seconds()
        return offset_secs if offset_secs > 0 else 0


    def set_timer(self, typ, start=False):
        # Clear the current timer first
        curr_tmr = self.photo_timer if typ == "photo" else self.check_timer
        if curr_tmr:
            curr_tmr.cancel()
        tmr = None
        if typ == "photo":
            interval = self.initial_interval or self.interval
            self.initial_interval = 0
            tmr = self.photo_timer = Timer(interval, self.navigate)
        elif typ == "check":
            tmr = self.check_timer = Timer(self.check_interval,
                    self.check_host)
        logit("debug", "Timer set for:", typ, tmr.interval, tmr.function)
        if tmr and start:
            tmr.start()
            logit("debug", "Timer started for", typ)


    def start(self):
        signal.signal(signal.SIGHUP, self._read_config)
        signal.signal(signal.SIGURG, self.check_host)
        signal.signal(signal.SIGTSTP, self.pause)
        signal.signal(signal.SIGCONT, self.resume)
        signal.signal(signal.SIGTRAP, self.navigate)
        if not self.check_timer.is_alive():
            self.check_timer.start()
        if not self.photo_timer.is_alive():
            self.photo_timer.start()
        logit("debug", "Timers started")
        self._started = True
        self.show_photo()


    def pause(self, signum=None, frame=None):
        self.photo_timer.cancel()
        logit("info", "Photo timer stopped")


    def resume(self, signum=None, frame=None):
        self.set_timer("photo", True)
        logit("info", "Photo timer started")


    def _read_config(self, signum=None, frame=None):
        logit("info", "_read_config called!")
        try:
            self.parser.read(CONFIG_FILE)
        except ConfigParser.MissingSectionHeaderError as e:
            # The file exists, but doesn't have the correct format.
            raise exc.InvalidConfigurationFile(e)

        def safe_get(section, option, default=None):
            try:
                return self.parser.get(section, option)
            except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
                return default

        self.log_level = safe_get("frame", "log_level", "INFO")
        set_log_level(self.log_level)

        self.reg_url = safe_get("host", "reg_url")
        if not self.reg_url:
            logit("error", "No registration URL in photo.cfg; exiting")
            exit()
        self.dl_url = safe_get("host", "dl_url")
        if not self.dl_url:
            logit("error", "No download URL configured in photo.cfg; exiting")
            exit()
        self.check_url = safe_get("host", "check_url", None)
        self.frameset = safe_get("frameset", "name", "")
        self.name = safe_get("frame", "name", "undefined")
        self.pkid = safe_get("frame", "pkid", "")
        self.description = safe_get("frame", "description", "")
        self.orientation = safe_get("frame", "orientation", "H")
        # When to start the image rotation
        self.interval_base = safe_get("frame", "interval_base", "*:*")
        # How often to change image
        self.interval_time = int(safe_get("frame", "interval_time", 10))
        # Units of time for the image change interval
        self.interval_units = safe_get("frame", "interval_units", "minutes")
        self.interval = _normalize_interval(self.interval_time,
                self.interval_units)
        logit("info", "Setting image interval to", self.interval)
        self.set_image_interval()
        check_interval = int(safe_get("frame", "host_check", 120))
        check_units = safe_get("frame", "host_check_units", "minutes")
        self.check_interval = _normalize_interval(check_interval, check_units)
        logit("info", "Setting host check interval to", self.check_interval)
        self.brightness = safe_get("monitor", "brightness")
        self.contrast = safe_get("monitor", "contrast")
        self.saturation = safe_get("monitor", "saturation")


    def set_image_interval(self):
        if not self.photo_timer:
            # Starting up
            return
        self.photo_timer.cancel()
        self.set_timer("photo", True)


    def _register(self):
        headers = {"user-agent": "photoviewer"}
        # Get free disk space
        freespace = get_freespace()
        data = {"pkid": self.pkid, "name": self.name,
                "description": self.description,
                "interval_time": self.interval_time,
                "interval_units": self.interval_units,
                "orientation": self.orientation, "freespace": freespace,
                "frameset": self.frameset}
        resp = requests.post(self.reg_url, data=data, headers=headers)
        if 200 <= resp.status_code <= 299:
            # Success!
            pkid, images = resp.json()
            if pkid != self.pkid:
                self.parser.set("frame", "pkid", pkid)
                with open(CONFIG_FILE, "w") as ff:
                    self.parser.write(ff)
            self.host_images = images
            self._update_images()
        else:
            logit("error", resp.status_code, resp.text)
            exit()


    def _update_images(self):
        """Compares the associated images received from the server, and updates
        the local copies if needed.
        """
        images = self.host_images
        curr = set([just_fname(img) for img in self.current_images])
        upd = set(images)
        if upd == curr:
            logit("debug", "No changes in _update_images")
            # No changes
            return
        logit("debug", "updating images...")
        to_remove = curr - upd
        logit("debug", "To remove:", *to_remove)
        freespace = get_freespace()
        logit("debug", "Freespace", freespace)
        for img in to_remove:
            curr_loc = os.path.join(PHOTODIR, img)
            if freespace < ONE_GB:
                # Just delete it
                logit("info", "deleting", curr_loc)
                os.unlink(curr_loc)
            else:
                new_loc = os.path.join(INACTIVE_PHOTODIR, img)
                logit("info", "inactivating", img)
                shutil.move(curr_loc, new_loc)
        to_get = upd - curr
        logit("debug", "To get:", *to_get)
        num_to_get = len(to_get)
        images_to_process = []
        for pos, img in enumerate(to_get):
            logit("info", "adding", img)
            logit("debug", "PROCESSING IMAGE #%s of %s" % (pos, num_to_get))
            # Check if it's local
            inactive_loc = os.path.join(INACTIVE_PHOTODIR, img)
            if os.path.exists(inactive_loc):
                logit("info", "retrieving from inactive")
                active_loc = os.path.join(PHOTODIR, img)
                shutil.move(inactive_loc, active_loc)
                continue
            # Not local, so download it
            img_file = self._download(img)
            if img_file:
                images_to_process.append(img_file)
        if images_to_process:
            num_imgs = len(images_to_process)
            logit("debug", "Processing  %s images" % num_imgs)
            for pos, img in enumerate(images_to_process):
                logit("info", "Processing image %s (%s of %s)" %
                        (img, pos+1, num_imgs))
                image.adjust(img, self.brightness, self.contrast,
                        self.saturation)
        logit("info", "Image update is done!")
        self.load_images()


    def _download(self, img):
        headers = {"user-agent": "photoviewer"}
        url = "%s/%s" % (self.dl_url, img)
        logit("debug", url)
        logit("info", "downloading", img)
        resp = requests.get(url, headers=headers, stream=True)
        if resp.status_code == 200:
            outfile = os.path.join(PHOTODIR, img)
            with open(outfile, "wb") as ff:
                resp.raw.decode_content = True
                shutil.copyfileobj(resp.raw, ff)
            return outfile


    def load_images(self, directory=None):
        """ Loads images from the specified directory, or, if unspecified, the
        PHOTODIR directory.
        """
        directory = directory or PHOTODIR
        fnames = glob.glob("%s/*" % os.path.abspath(directory))
        self.current_images = [fname for fname in fnames
                if IMG_PAT.match(fname)]
        random.shuffle(self.current_images)


    def navigate(self, signum=None, frame=None):
        """Moves to the next image. """
        logit("debug", "navigate called; current index", self.image_index)

        new_index = self.image_index + 1
        # Boundaries
        max_index = len(self.current_images) - 1
        if new_index > max_index:
            new_index = 0
            # Shuffle the images
            logit("info", "All images shown; shuffling order.")
            random.shuffle(self.current_images)
        else:
            new_index = max(0, min(max_index, new_index))
        logit("debug", "new index", new_index)
        if new_index != self.image_index:
            self.image_index = new_index
            self.show_photo()
        elif new_index == 0:
            # There is only one image to display; make sure that it is the same
            # as the one currently displayed.
            # First, handle case where current image has been deleted locally
            img_exists = os.path.exists(self.displayed_name)
            logit("debug", "image exists:", img_exists)
            if not img_exists:
                self.current_images.remove(self.displayed_name)
                logit("debug", "refreshing missing images")
                self._update_images()
            displayed_file = "%s/%s" % (DISPLAY_PHOTODIR, curr_display_list[0])
            logit("debug", "comparing:", self.displayed_name, displayed_file)
            same_files = filecmp.cmp(self.displayed_name, displayed_file)
            logit("debug", "Files are", "same" if same_files else "different")
            if not same_files:
                self.show_photo()
        self.set_timer("photo", True)


    def show_photo(self):
        if not self._started:
            return
        if not self.current_images:
            return
        try:
            fname = self.current_images[self.image_index]
        except IndexError as e:
            logit("error", "BAD INDEX", e)
            if self.current_images:
                fname = self.current_images[-1]
            else:
                # Something's screwy
                logit("error", "No images!")
                self.load_images()
                return
        fext = os.path.splitext(fname)[-1]

        if fname == self.displayed_name:
            return
        self.displayed_name = fname
        logit("debug", "displayed image name:", self.displayed_name)
        cmd = VIEWER_CMD % fname
        logit("info", "Changing photo to", just_fname(fname))
        logit("debug", "Command:", cmd)
        runproc("sudo killall fbi")
        runproc(cmd, wait=False)


    def check_host(self, signum=None, frame=None):
        """Contact the host to update local status."""
        logit("info", "check_host called")
        if self.check_url is None:
            # Not configured
            logit("warning", "No host check URL defined")
            return
        # If we are already in check_host, skip
        if self.in_check_host:
            logit("debug", "check_host called; already in the process")
            return
        self.in_check_host = True
        headers = {"user-agent": "photoviewer"}
        url = self.check_url.replace("PKID", self.pkid)
        try:
            resp = requests.get(url, headers=headers)
            if resp.status_code >= 300:
                logit("error", resp.status_code, resp.content)
                self.in_check_host = False
                self.set_timer("check", True)
                return
        except Exception as e:
            logit("error", "check_host", resp.status_code, e)
            self.in_check_host = False
            self.set_timer("check", True)
            return
        data = resp.json()
        logit("debug", "Data:", data)
        # See if anything has updated on the server side
        self._update_config(data)
        # Check for image changes
        self.host_images = data["images"]
        logit("debug", "Updating images")
        self._update_images()
        logit("debug", "check_host done.")
        self.set_timer("check", True)
        self.in_check_host = False
        self.load_images()


    def _update_config(self, data):
        changed = False
        new_interval = False
        for key in ("name", "description", "interval_time", "interval_units",
                "brightness", "contrast", "saturation"):
            val = data.get(key, None)
            logit("debug", "key:", key, "; val:", val)
            if val is None:
                continue
            local_val = getattr(self, key)
            if local_val != val:
                setattr(self, key, val)
                monitor_keys = ("brightness", "contrast", "saturation")
                section = "monitor" if key in monitor_keys else "frame"
                self.parser.set(section, key, str(val))
                changed = True
                new_interval = new_interval or "interval" in key
        if changed:
            with open(CONFIG_FILE, "w") as ff:
                self.parser.write(ff)
        if new_interval:
            self.interval = _normalize_interval(self.interval_time,
                    self.interval_units)
            logit("info", "Setting timer to", self.interval)
            self.set_image_interval()


    def kill_all(self):
        """Kills all timers on receiving a Ctrl-C."""
        logit("error", "Killing timers")
        self.photo_timer.cancel()
        self.check_timer.cancel()
        logit("error", "Timers canceled")


if __name__ == "__main__":
    with open("photo.pid", "w") as ff:
        ff.write("%s" % os.getpid())
    img_mgr = ImageManager()
    try:
        img_mgr.start()
        logit("debug", "And we're off!")
    except KeyboardInterrupt:
        img_mgr.kill_all()
