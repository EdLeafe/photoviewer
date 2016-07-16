#/usr/bin/env python

import dabo

def debugout(*args):
    try:
        dabo.dAppRef.DEBUG
    except Exception:
        return
    out = ", ".join(["%s" % str(arg) for arg in args])
    print out


class FullImage(dabo.ui.dImage):
    def fill(self, evt=None):
        """ Size and place this image to fill its parent."""
        parent = self.Parent
        pW, pH = parent.Size
        pW, pH = float(pW), float(pH)
        iW, iH = origW, origH = self.Size
        iW, iH = float(iW), float(iH)
        pProp = pW / pH
        iProp = iW / iH
        debugout("Parent", pW, pH)
        debugout("Image", iW, iH)
        debugout("Prop", pProp, iProp)

        szW = pW
        szH = pW / iProp
        pad = diff = None
        if szH > pH:
            # Image is taller than parent; use the height as the limit
            szH = pH
            szW = pH * iProp
            pad = "W"
            diff = pW - szW
        elif szH < pH:
            # Width is the limiting dimension
            pad = "H"
            diff = pH - szH

        if (origW, origH) == (szW, szH):
            # Size hasn't changed
            return

        self.Form.lockDisplay()
        # Resize the image
        self.Size = szW, szH
        # Position on parent
        if pad == "W":
            self.Position = ((diff / 2), 0)
        elif pad == "H":
            self.Position = (0, (diff / 2))
        else:
            # Proportions match
            self.Position = (0, 0)
        dabo.ui.callAfterInterval(150, self.Form.unlockDisplay)

