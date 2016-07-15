#/usr/bin/env python

import dabo
dabo.ui.loadUI("wx")
from dabo.dApp import dApp

PHOTO = "images/test.jpg"


class ImgForm(dabo.ui.dForm):
    def afterInit(self):
        self.Caption = "Photo"
        self.mainPanel = mp = dabo.ui.dPanel(self)
        self.Sizer.append1x(mp)
        sz = dabo.ui.dSizer("v")
        mp.Sizer = sz
#        self.timer = dabo.ui.dTimer(mp, Interval=15, OnHit=self.zoomit,
#                Enabled=True)
        self.img = dabo.ui.dImage(mp, Picture=PHOTO)
        sz.append1x(self.img)

    def zoomit(self, evt):
        self.ShowFullScreen(True)


if __name__ == "__main__":
    app = dApp()
    app.MainFormClass = ImgForm
    app.start()

