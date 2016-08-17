#/usr/bin/env python
from __future__ import print_function

import six.moves.configparser as ConfigParser

import dabo
dabo.ui.loadUI("wx")
from dabo.dApp import dApp

from fullimage import FullImage

PHOTO = "images/test.jpg"


class ImgForm(dabo.ui.dForm):
    def beforeInit(self):
        self.SaveRestorePosition = False
        self.ShowStatusBar = False
        self._read_config

    def afterInit(self):
#        self.WindowState = "Fullscreen"
        self.WindowState = "normal"
        self.Size = (400, 300)
        self.BackColor = "black"
        self.mainPanel = mp = dabo.ui.dPanel(self, BackColor="black")
        self.Sizer.append1x(mp)
        mp.Sizer = sz = dabo.ui.dSizer("v")
        self.img = FullImage(mp, Picture=PHOTO)
        mp.bindEvent(dabo.dEvents.Resize, self.img.fill)

    def _read_config(self):
        cfg = ConfigParser.SafeConfigParser()
        try:
            cfg.read(config_file)
        except ConfigParser.MissingSectionHeaderError as e:
            # The file exists, but doesn't have the correct format.
            raise exc.InvalidConfigurationFile(e)

        def safe_get(section, option, default=None):
            try:
                return cfg.get(section, option)
            except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
                return default

        self.host_url = safe_get("host", "url")
        if not self.host_url:
            print("No host URL configured in photo.cfg; exiting")
            exit()
        else:
            print("Yay! %s" % self.host_url)


if __name__ == "__main__":
    app = dApp()
    app.MainFormClass = ImgForm
    app.start()

