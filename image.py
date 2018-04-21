from PIL import Image, ImageEnhance

from utils import logit

def adjust(img_file, bright, contrast, saturation):
    """Uses the local profile to adjust the image to look good on the local
    monitor.
    """
    logit("debug", "Adjusting image '%s'" % img_file)
    img = Image.open(img_file)
    ibright = ImageEnhance.Brightness(img)
    logit("debug", "Adjusting brightness for image '%s'" % img_file)
    img = ibright.enhance(float(bright))
    logit("debug", "Finished adjusting brightness for image '%s'" % img_file)
    icontrast = ImageEnhance.Contrast(img)
    logit("debug", "Adjusting contrast for image '%s'" % img_file)
    img = icontrast.enhance(float(contrast))
    logit("debug", "Finished adjusting contrast for image '%s'" % img_file)
    isat = ImageEnhance.Color(img)
    logit("debug", "Adjusting saturation for image '%s'" % img_file)
    img = isat.enhance(float(saturation))
    logit("debug", "Finished adjusting saturation for image '%s'" % img_file)
    logit("debug", "Saving image '%s'" % img_file)
    img.save(img_file)
    logit("debug", "Finished saving image '%s'" % img_file)
    logit("debug", "Finished corrections for image '%s'" % img_file)
