#/usr/bin/env python
from __future__ import print_function

import commands
import filecmp
import glob
import logging
import json
import os
import re
import shutil
from threading import Timer

import requests
import six.moves.configparser as ConfigParser


LOG = None
LOG_LEVEL = logging.INFO
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
    LOG.setLevel(LOG_LEVEL)


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
        _setup_logging()
        self.photo_timer = self.check_timer = None
        self.parser = ConfigParser.SafeConfigParser()
        self._read_config()
        self.set_timer("check")
        self.set_timer("photo")

        self.current_images = []
        self.host_images = []
        self.inactive_images = []
        self.displayed_name = ""
        self.image_index = 0
        self.load_images()
        self._register()


    def set_timer(self, typ, start=False):
        tmr = None
        if typ == "photo":
            tmr = self.photo_timer = Timer(self.interval, self.navigate)
        elif typ == "check":
            tmr = self.check_timer = Timer(self.check_interval,
                    self.check_host)
        logit("debug", "Timer set for:", typ, tmr.interval, tmr.function)
        if tmr and start:
            tmr.start()
            logit("debug", "Timer started for", typ)


    def start(self):
        self.check_timer.start()
        self.photo_timer.start()
        logit("debug", "Timers started")


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

        self.log_level = safe_get("frame", "log_level", "INFO")
        LOG.setLevel(getattr(logging, self.log_level))

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
            print("ERROR!", resp.status_code, resp.text)
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
        print("Please wait; updating images...", end=" ")
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
        self.show_photo()


    def _download(self, img):
        headers = {"user-agent": "photoviewer"}
        url = "%s/%s" % (self.dl_url, img)
        logit("info", "downloading", img)
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
        logit("debug", "navigate called")
        logit("debug", "current index", self.image_index)
        new_index = self.image_index + 1
        # Boundaries
        max_index = len(self.current_images) - 1
        if new_index > max_index:
            new_index = 0
        else:
            new_index = max(0, min(max_index, new_index))
        logit("debug", "new index", new_index)
        curr_display_list = os.listdir(DISPLAY_PHOTODIR)
        logit("debug", "curr display list:", *curr_display_list)
        if not curr_display_list or new_index != self.image_index:
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
        # Since the displayed image name is always 'display.EXT', keep the true
        # name for comparison later.
        self.displayed_name = fname
        logit("debug", "displayed image name:", self.displayed_name)
        cmd = SHOW_CMD % (fname, fext)
        logit("info", "Changing photo to", just_fname(fname))
        logit("debug", "Command:", cmd)
        os.system(cmd)


    def check_host(self):
        """Contact the host to update local status."""
        logit("debug", "check_host called")
        if self.check_url is None:
            # Not configured
            logit("warning", "No host check URL defined")
            return
        headers = {"user-agent": "photoviewer"}
        url = self.check_url.replace("PKID", self.pkid)
        try:
            resp = requests.get(url, headers=headers)
            if resp.status_code != 200:
                print(resp.status_code, resp.content)
                logit("error", resp.status_code, resp.content)
                return
        except Exception as e:
            logit("error", "check_host", e)
            return
        data = resp.json()
        # See if anything has updated on the server side
        self._update_config(data)
        # Check for image changes
        self.host_images = data["images"]
        self._update_images()
        self.set_timer("check", True)


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
