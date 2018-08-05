import gc
from PIL import Image, ImageEnhance

from utils import logit

def adjust(img_file, bright, contrast, saturation):
    """Uses the local profile to adjust the image to look good on the local
    monitor.
    """
    logit("info", "Adjusting image '%s'" % img_file)
    img = Image.open(img_file)
    enhancer = ImageEnhance.Brightness(img)
    logit("debug", "Adjusting brightness for image '%s'" % img_file)
    img = enhancer.enhance(float(bright))
    logit("debug", "Finished adjusting brightness for image '%s'" % img_file)
    enhancer = ImageEnhance.Contrast(img)
    logit("debug", "Adjusting contrast for image '%s'" % img_file)
    img = enhancer.enhance(float(contrast))
    logit("debug", "Finished adjusting contrast for image '%s'" % img_file)
    enhancer = ImageEnhance.Color(img)
    logit("debug", "Adjusting saturation for image '%s'" % img_file)
    img = enhancer.enhance(float(saturation))
    logit("debug", "Finished adjusting saturation for image '%s'" % img_file)
    logit("debug", "Saving image '%s'" % img_file)
    img.save(img_file)
    gc.collect()
    logit("debug", "Finished saving image '%s'" % img_file)
    logit("info", "Finished corrections for image '%s'" % img_file)
