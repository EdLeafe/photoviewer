#/usr/bin/env python
from __future__ import print_function

import commands
import glob
import logging
import json
import os
import re
import shutil

import requests
import six.moves.configparser as ConfigParser

import dabo
dabo.ui.loadUI("wx")
from dabo.dApp import dApp

from fullimage import FullImage

LOG = logging.getLogger("photo")
hnd = logging.FileHandler("log/photo.log")
formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
hnd.setFormatter(formatter)
LOG.addHandler(hnd) 
LOG.setLevel(logging.INFO)

PHOTODIR = "images"
INACTIVE_PHOTODIR = "inactive_images"
IMG_PAT = re.compile(r".+\.[jpg|jpeg|gif|png]")
CONFIG_FILE = "photo.cfg"


def logit(level, *msgs):
    text = " ".join(["%s" % msg for msg in msgs])
    log_method = getattr(LOG, level)
    log_method(text)


def just_fname(path):
    return os.path.split(path)[-1]


def _normalize_interval(time_, units):
    unit_key = units[0].lower()
    factor = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit_key, 1)
    # Need to convert to milliseconds
    return time_ * factor * 1000


class ImgForm(dabo.ui.dForm):
    def beforeInit(self):
        self.SaveRestorePosition = False
        self.ShowStatusBar = False
        self.parser = ConfigParser.SafeConfigParser()
        self._read_config()


    def afterInit(self):
#        self.WindowState = "Fullscreen"
        self.WindowState = "normal"
        self.Size = (800, 600)
        self.BackColor = "black"
        self.ShowSystemMenu = True
        self.mainPanel = mp = dabo.ui.dPanel(self, BackColor="black")
        self.Sizer.append1x(mp)
        mp.Sizer = sz = dabo.ui.dSizer("v")
        self.img = FullImage(mp, Picture=None)
        self.img.bindEvent(dabo.dEvents.MouseLeftClick, self.handle_nav)
        mp.bindEvent(dabo.dEvents.MouseLeftClick, self.handle_nav)
        mp.bindEvent(dabo.dEvents.Resize, self.img.fill)

        self.current_images = []
        self.inactive_images = []
        self.image_index = -1
        self.load_images()
        self._register()
        dabo.ui.callAfter(self.navigate)

        self.picture_timer = dabo.ui.dTimer(mp, Interval=self.interval,
                OnHit=self.handle_nav)
        self.picture_timer.start()
        self.check_timer = dabo.ui.dTimer(mp, Interval=self.check_interval,
                OnHit=self.check_host)
        self.check_timer.start()


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
            print("No registration URL configured in photo.cfg; exiting")
            exit()
        self.dl_url = safe_get("host", "dl_url")
        if not self.dl_url:
            print("No download URL configured in photo.cfg; exiting")
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
        check_interval = int(safe_get("frame", "host_check", 120))
        check_units = safe_get("frame", "host_check_units", "minutes")
        self.check_interval = _normalize_interval(check_interval, check_units)


    def _register(self):
        headers = {"user-agent": "photoviewer"}
        # Get free disk space
        freespace = commands.getoutput('df .').split('\n')[1].split()[3]
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
        for img in to_remove:
            logit("info", "removing", img)
            curr_loc = os.path.join(PHOTODIR, img)
            new_loc = os.path.join(INACTIVE_PHOTODIR, img)
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
        

    def handle_nav(self, evt):
        """Handles mouse clicks and timer events that rotate the displayed
        image.
        """
        self.navigate()


    def navigate(self):
        """Moves to the next image. """
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
        self.img.Picture = fname
        self.img.fill()


    def check_host(self, evt):
        """Contact the host to update local status."""
        if self.check_url is None:
            # Not configured
            print("No host check URL defined")
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
            print("Setting timer to", self.interval)
            self.picture_timer.Interval = self.interval
            self.picture_timer.start()


if __name__ == "__main__":
    with open("photo.pid", "w") as ff:
        ff.write("%s" % os.getpid())
    app = dApp()
#    app.DEBUG = True
    app.MainFormClass = ImgForm
    app.start()

