#/usr/bin/env python
from __future__ import print_function

import commands
import glob
import logging
import json
import os
import re
import shutil
import threading

import requests
import six.moves.configparser as ConfigParser

import spt


LOG = None
PHOTODIR = "images"
INACTIVE_PHOTODIR = "inactive_images"
DISPLAY_PHOTODIR = "display"
IMG_PAT = re.compile(r".+\.[jpg|jpeg|gif|png]")
CONFIG_FILE = "photo.cfg"
SHOW_CMD = "rm -f %s/*; cp %%s %s/display%%s" % (
        DISPLAY_PHOTODIR, DISPLAY_PHOTODIR)
ONE_GB = 1024 ** 3


def _setup_logging():
    global LOG
    LOG = logging.getLogger("photo")
    hnd = logging.FileHandler("log/photo.log")
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    hnd.setFormatter(formatter)
    LOG.addHandler(hnd)
    LOG.setLevel(logging.INFO)


def logit(level, *msgs):
    if not LOG:
        _setup_logging()
    text = " ".join(["%s" % msg for msg in msgs])
    log_method = getattr(LOG, level)
    log_method(text)


def just_fname(path):
    return os.path.split(path)[-1]


def _normalize_interval(time_, units):
    unit_key = units[0].lower()
    factor = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit_key, 1)
    return time_ * factor


def get_freespace():
    return commands.getoutput('df .').split('\n')[1].split()[3]


class ImageManager(object):
    def __init__(self):
        self._timer = self.photo_timer = self.check_timer = None
        self._nav_called = False
        self.parser = ConfigParser.SafeConfigParser()
        self._read_config()
        self._timer = spt.SPT()
        self.check_timer = self._timer.add_timer("check", self.check_host,
                self.check_interval, 0)
        self.photo_timer = self._timer.add_timer("photo", self.navigate,
                self.interval, 1)
        self.current_images = []
        self.inactive_images = []
        self.image_index = -1

        self.load_images()
        self._register()


    def start(self):
        self._timer.start()
        logit("info", "Timer started")


    def _read_config(self):
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


    def set_image_interval(self):
        if not self._timer:
            # Starting up
            return
        if self.photo_timer:
            self._timer.cancel("photo")
        self.photo_timer = self._timer.add_timer("photo", self.navigate,
                self.interval, 1)
        logit("info", "timer events", self._timer._events)


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
            self._update_images(images)
        else:
            print("ERROR!", resp.status_code, resp.text)
            exit()


    def _update_images(self, images):
        """Compares the associated images received from the server, and updates
        the local copies if needed.
        """
        curr = set([just_fname(img) for img in self.current_images])
        upd = set(images)
        if upd == curr:
            # No changes
            return
        print("Please wait; updating images...", end=" ")
        to_remove = curr - upd
        freespace = get_freespace()
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
        for img in to_get:
            logit("info", "adding", img)
            # Check if it's local
            inactive_loc = os.path.join(INACTIVE_PHOTODIR, img)
            if os.path.exists(inactive_loc):
                logit("info", "retrieving from inactive")
                active_loc = os.path.join(PHOTODIR, img)
                shutil.move(inactive_loc, active_loc)
                continue
            # Not local, so download it
            self._download(img)
        print("Done!")
	self.load_images()


    def _download(self, img):
        headers = {"user-agent": "photoviewer"}
        url = "%s/%s" % (self.dl_url, img)
        logit("info", "downloading", img, "from url", url)
        resp = requests.get(url, headers=headers, stream=True)
        if resp.status_code == 200:
            outfile = os.path.join(PHOTODIR, img)
            with open(outfile, "wb") as ff:
                resp.raw.decode_content = True
                shutil.copyfileobj(resp.raw, ff)


    def load_images(self, directory=None):
        """ Loads images from the specified directory, or, if unspecified, the
        PHOTODIR directory.
        """
        directory = directory or PHOTODIR
        fnames = glob.glob("%s/*" % os.path.abspath(directory))
        self.current_images = [fname for fname in fnames
                if IMG_PAT.match(fname)]


    def navigate(self):
        """Moves to the next image. """
        if not self._nav_called:
            # Initial setting
            self._nav_called = True
            return
        new_index = self.image_index + 1
        # Boundaries
        max_index = len(self.current_images) - 1
        if new_index > max_index:
            new_index = 0
        else:
            new_index = max(0, min(max_index, new_index))
        if new_index != self.image_index:
            self.image_index = new_index
            self.show_photo()


    def show_photo(self):
        if not self.current_images:
            return
        fname = self.current_images[self.image_index]
        fext = os.path.splitext(fname)[-1]
        cmd = SHOW_CMD % (fname, fext)
        logit("info", "Changing photo to", fname)
        os.system(cmd)


    def check_host(self):
        """Contact the host to update local status."""
        if self.check_url is None:
            # Not configured
            logit("warning", "No host check URL defined")
            return
        headers = {"user-agent": "photoviewer"}
        url = self.check_url.replace("PKID", self.pkid)
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            print(resp.status_code, resp.content)
            return
        data = resp.json()
        # See if anything has updated on the server side
        self._update_config(data)
        # Check for image changes
        self._update_images(data["images"])


    def _update_config(self, data):
        changed = False
        new_interval = False
        for key in ("name", "description", "interval_time", "interval_units"):
            val = data.get(key, None)
            if val is None:
                continue
            local_val = getattr(self, key)
            if local_val != val:
                setattr(self, key, val)
                self.parser.set("frame", key, str(val))
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


if __name__ == "__main__":
    with open("photo.pid", "w") as ff:
        ff.write("%s" % os.getpid())
    img_mgr = ImageManager()
    img_mgr.start()
    logit("info", "And we're off!")
