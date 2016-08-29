#/usr/bin/env python
from __future__ import print_function

import glob
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

PHOTODIR = "images"
INACTIVE_PHOTODIR = "inactive_images"
IMG_PAT = re.compile(r".+\.[jpg|jpeg|gif|png]")
CONFIG_FILE = "photo.cfg"


def just_fname(path):
    return os.path.split(path)[-1]


class ImgForm(dabo.ui.dForm):
    def beforeInit(self):
        self.SaveRestorePosition = False
        self.ShowStatusBar = False
        self.parser = ConfigParser.SafeConfigParser()
        self._read_config()


    def afterInit(self):
        self.WindowState = "Fullscreen"
#        self.WindowState = "normal"
        self.Size = (800, 600)
        self.BackColor = "black"
        self.ShowSystemMenu = True
        self.mainPanel = mp = dabo.ui.dPanel(self, BackColor="black")
        self.Sizer.append1x(mp)
        mp.Sizer = sz = dabo.ui.dSizer("v")
        self.img = FullImage(mp, Picture=None)
        self.img.bindEvent(dabo.dEvents.MouseLeftClick, self.handle_click)
        mp.bindEvent(dabo.dEvents.MouseLeftClick, self.handle_click)
        mp.bindEvent(dabo.dEvents.Resize, self.img.fill)

        self.current_images = []
        self.inactive_images = []
        self.image_index = -1
        self.load_images()
        self._register()
        dabo.ui.callAfter(self.navigate)


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
        self.name = safe_get("frame", "name", "undefined")
        self.pkid = safe_get("frame", "pkid", "")
        self.description = safe_get("frame", "description", "")
        self.orientation = safe_get("frame", "orientation", "H")
        self.frameset = safe_get("frameset", "name", "")


    def _register(self):
        headers = {"user-agent": "photoviewer"}
        data = {"pkid": self.pkid, "name": self.name,
                "description": self.description,
                "orientation": self.orientation,"frameset": self.frameset}
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
        print("Please wait; updating images...")
        to_remove = curr - upd
        for img in to_remove:
            curr_loc = os.path.join(PHOTODIR, img)
            new_loc = os.path.join(INACTIVE_PHOTODIR, img)
            shutil.move(curr_loc, new_loc)
        to_get = upd - curr
        for img in to_get:
            # Check if it's local
            inactive_loc = os.path.join(INACTIVE_PHOTODIR, img)
            if os.path.exists(inactive_loc):
                active_loc = os.path.join(PHOTODIR, img)
                shutil.move(inactive_loc, active_loc)
                continue
            # Not local, so download it
            self._download(img)
	self.load_images()


    def _download(self, img):
        headers = {"user-agent": "photoviewer"}
        url = "%s/%s" % (self.dl_url, img)
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
        

    def handle_click(self, evt):
        """ Handles mouse clicks and their actions."""
        self.navigate()


    def navigate(self, forward=True):
        """ Moves to another image. """
        val = 1 if forward else -1
        new_index = self.image_index + val
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


if __name__ == "__main__":
    app = dApp()
#    app.DEBUG = True
    app.MainFormClass = ImgForm
    app.start()

