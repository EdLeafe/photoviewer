#/usr/bin/env python

import glob
import os
import re

import dabo
dabo.ui.loadUI("wx")
from dabo.dApp import dApp

from fullimage import FullImage

PHOTODIR = "images"
INACTIVE_PHOTODIR = "inactive_images"
IMG_PAT = re.compile(r".+\.jpg|jpeg|gif|png")


class ImgForm(dabo.ui.dForm):
    def beforeInit(self):
        self.SaveRestorePosition = False
        self.ShowStatusBar = False

    def afterInit(self):
#        self.WindowState = "Fullscreen"
        self.WindowState = "normal"
        self.Size = (800, 600)
        self.BackColor = "red"
        self.mainPanel = mp = dabo.ui.dPanel(self, BackColor="black")
        self.Sizer.append1x(mp)
        mp.Sizer = sz = dabo.ui.dSizer("v")
        self.img = FullImage(mp, Picture=None)
        self.img.bindEvent(dabo.dEvents.MouseLeftClick, self.handle_click)
        mp.bindEvent(dabo.dEvents.Resize, self.img.fill)

        self.current_images = []
        self.inactive_images = []
        self.image_index = -1
        self.load_images()


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
        new_index = max(0, min(max_index, new_index))
        if new_index != self.image_index:
            self.image_index = new_index
            self.show_photo()

    def show_photo(self):
        if not self.current_images:
            return
        fname = self.current_images[self.image_index]
        self.img.Picture = fname



if __name__ == "__main__":
    app = dApp()
    app.DEBUG = True
    app.MainFormClass = ImgForm
    app.start()

