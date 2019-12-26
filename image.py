import gc
import os
from PIL import Image, ImageEnhance

from utils import logit

MINSIZE = 3600


def adjust(img_file, bright, contrast, saturation):
    """Uses the local profile to adjust the image to look good on the local
    monitor.
    """
    img_name = os.path.basename(img_file)
    logit("info", "Adjusting image '%s'" % img_name)
    img = Image.open(img_file)
    if img.height < MINSIZE and img.width < MINSIZE:
        img.thumbnail((MINSIZE, MINSIZE), Image.ANTIALIAS)
    logit("debug", "Image %s size: %s x %s" % (img_name, img.width, img.height))
    if img.height > img.width:
        # Rotate 90 degrees for display
        img = img.rotate(90, expand=True)
        logit("info", "Image '%s' has been rotated" % img_name)
    if bright != 1.0:
        enhancer = ImageEnhance.Brightness(img)
        logit("debug", "Adjusting brightness for image '%s'" % img_name)
        img = enhancer.enhance(float(bright))
        logit("debug", "Finished adjusting brightness for image '%s'" % img_name)
    if contrast != 1.0:
        enhancer = ImageEnhance.Contrast(img)
        logit("debug", "Adjusting contrast for image '%s'" % img_name)
        img = enhancer.enhance(float(contrast))
        logit("debug", "Finished adjusting contrast for image '%s'" % img_name)
    if saturation != 1.0:
        enhancer = ImageEnhance.Color(img)
        logit("debug", "Adjusting saturation for image '%s'" % img_name)
        img = enhancer.enhance(float(saturation))
        logit("debug", "Finished adjusting saturation for image '%s'" % img_name)
    logit("debug", "Saving image '%s'" % img_name)
    img.save(img_file, format='JPEG', subsampling=0, quality=95)
    gc.collect()
    logit("debug", "Finished saving image '%s'" % img_name)
    logit("info", "Finished corrections for image '%s'" % img_name)
