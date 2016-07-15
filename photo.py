#/usr/bin/env python

import dabo
dabo.ui.loadUI("wx")
from dabo.dApp import dApp

from fullimage import FullImage

PHOTO = "images/test.jpg"


class ImgForm(dabo.ui.dForm):
    def beforeInit(self):
        self.SaveRestorePosition = False
        self.ShowStatusBar = False

    def afterInit(self):
#        self.WindowState = "Fullscreen"
        self.WindowState = "normal"
        self.Size = (400, 300)
        self.BackColor = "red"
        self.mainPanel = mp = dabo.ui.dPanel(self, BackColor="black")
        self.Sizer.append1x(mp)
        mp.Sizer = sz = dabo.ui.dSizer("v")
        self.img = FullImage(mp, Picture=PHOTO)
        mp.bindEvent(dabo.dEvents.Resize, self.img.fill)


if __name__ == "__main__":
    app = dApp()
    app.MainFormClass = ImgForm
    app.start()

