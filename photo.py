#/usr/bin/env python3
import configparser
import datetime
import filecmp
from functools import partial
import getpass
import glob
import json
import os
import random
import re
import shutil
import signal
from threading import Timer

import requests

import image
import utils
from utils import logit
from utils import runproc

info = partial(logit, "info")
debug = partial(logit, "debug")
error = partial(logit, "error")

APPDIR = "/home/{user}/projects/photoviewer".format(user=getpass.getuser())
PHOTODIR = os.path.join(APPDIR, "images")
INACTIVE_PHOTODIR = os.path.join(APPDIR, "inactive_images")
DOWNLOAD_PHOTODIR = os.path.join(APPDIR, "download")
FB_PHOTODIR = os.path.join(APPDIR, "fb")
LOG_DIR = os.path.join(APPDIR, "log")
# Make sure that all the necessary directories exist.
for pth in (APPDIR, PHOTODIR, INACTIVE_PHOTODIR, DOWNLOAD_PHOTODIR, LOG_DIR,
        FB_PHOTODIR):
    os.makedirs(pth, exist_ok=True)
utils.set_log_file(os.path.join(LOG_DIR, "photo.log"))

IMG_PAT = re.compile(r".+\.[jpg|jpeg|gif|png]")
CONFIG_FILE = os.path.join(APPDIR, "photo.cfg")
BASE_KEY = "/{pkid}:"
MONITOR_CMD = "echo 'on 0' | /usr/bin/cec-client -s -d 1"
FBI_CMD = "/usr/bin/sudo fbi -a --noverbose -T 1 -d /dev/fb0 '%s' >/dev/null 2>&1"
VIEWER_PARTS = ("vcgencmd display_power 1 > /dev/null 2>&1",
        FBI_CMD)
VIEWER_CMD = "; ".join(VIEWER_PARTS)
debug("VIEWER_CMD:", VIEWER_CMD)
ONE_MB = 1024 ** 2
ONE_GB = 1024 ** 3


def swapext(pth, ext):
    """Replaces the extension for the specified path with the supplied
    extension.
    """
    ext = ext.lstrip(".")
    dirname = os.path.dirname(pth)
    basename = os.path.basename(pth)
    newname = "%s.%s" % (os.path.splitext(basename)[0], ext)
    return os.path.join(dirname, newname)


def fb_path(pth):
    """Given a path to an image file, returns the corresponding path the the
    frame buffer directory with the same name but with the '.fb' extension.
    """
    img_name = os.path.basename(pth)
    fb_name = swapext(img_name, "fb")
    return os.path.join(FB_PHOTODIR, fb_name)


def clean_fb(pth):
    """Deletes the frame buffer version of an image if it exists"""
    fb = fb_path(pth)
    if os.path.exists(fb):
        os.unlink(fb)


def _normalize_interval(time_, units):
    unit_key = units[0].lower()
    factor = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit_key, 1)
    return time_ * factor


def get_freespace():
    stat = os.statvfs(".")
    freespace = stat.f_frsize * stat.f_bavail
    debug("Free disk space =", freespace)
    return freespace


class ImageManager(object):
    def __init__(self):
        self._started = False
        self.photo_timer = None
        self.parser = configparser.SafeConfigParser()
        self._read_config()
        self.initial_interval = self._set_start()
        self._set_power_on()
        self.in_check_host = False
        self.set_timer("photo")

        self.current_images = []
        self.host_images = []
        self.inactive_images = []
        self.displayed_name = ""
        self.image_index = 0
        self.load_images()
        self._register()


    def _set_power_on(self):
        # Power on the monitor and HDMI output
        debug("Powering on the monitor")
        out, err = runproc(MONITOR_CMD)
        debug("Power result", out.strip(), err.strip())


    def _set_start(self):
        now = datetime.datetime.now()
        base_hour, base_minute = self.interval_base.split(":")
        start_hour = now.hour if base_hour == "*" else int(base_hour)
        if base_minute == "*":
            start_minute = now.minute
        else:
            if start_hour == now.hour:
                # We want to make up the offset from the current time and the
                # start hour. First, determine how many minutes until we hit
                # the start_minute.
                diff = int(base_minute) - now.minute
                if diff < 0:
                    diff += 60
                interval_minutes = int(self.interval / 60)
                start_minute = now.minute + (diff % interval_minutes)
            else:
                start_minute = base_minute
        start_hour = (start_hour if start_minute >= now.minute
                else start_hour + 1)
        start_day = now.day if start_hour >= now.hour else now.day + 1
        if start_minute >= 60:
            start_minute = start_minute % 60
            start_hour += 1
        if start_hour >= 24:
            start_hour = start_hour % 24
            start_day += 1
        start = now.replace(day=start_day, hour=start_hour,
                minute=start_minute, second=0, microsecond=0)
        offset = start - now
        offset_secs = offset.total_seconds()
        return offset_secs if offset_secs > 0 else 0


    def set_timer(self, typ, start=False):
        # Clear the current timer first
        curr_tmr = self.photo_timer
        if curr_tmr:
            curr_tmr.cancel()
        interval = self.initial_interval or self.interval
        self.initial_interval = 0
        tmr = self.photo_timer = Timer(interval, self.navigate)
        debug("Timer set for:", tmr.interval, tmr.function)
        if tmr and start:
            tmr.start()
            debug("Timer started")


    def start(self):
        # If a prior import crashed during image processing, re-process the
        # images.
        self._process_images()
        signal.signal(signal.SIGHUP, self._read_config)
        signal.signal(signal.SIGTSTP, self.pause)
        signal.signal(signal.SIGCONT, self.resume)
        signal.signal(signal.SIGTRAP, self.navigate)
        if not self.photo_timer.is_alive():
            self.photo_timer.start()
            debug("Timer started")
        self._started = True
        self.show_photo()
        self.main_loop()


    def main_loop(self):
        """Listen for changes on the key for this host."""
        debug("Entering main loop; watching", self.watch_key)
        # Sometimes the first connection can be very slow - around 2 minutes!
        power_key = "%spower_state" % self.watch_key
        debug("Power key:", power_key)
        power_state = utils.read_key(power_key)
        debug("Power State:", power_state)
        self._set_power_state(power_state)
        callback = self.process_event
        utils.watch(self.watch_key, callback)
        # Shouldn't reach here.
        sys.exit(0)


    @staticmethod
    def _set_power_state(val):
        if val and val.lower() in ("stop", "off"):
            sys.exit()


    def _change_photo(self, val):
        self.navigate()


    def _set_settings(self, val):
        """The parameter 'val' will be a dict in the format of:
            setting name: setting value
        """
        self._update_config(val)


    def _set_images(self, val):
        self.host_images = val
        self._update_images()


    def process_event(self, key, val):
        debug("process_event called")
        actions = {"power_state": self._set_power_state,
                "change_photo": self._change_photo,
                "settings": self._set_settings,
                "images": self._set_images,
                }
        debug("Received key: {key} and val: {val}".format(key=key, val=val))
        mthd = actions.get(key)
        if not mthd:
            error("Unknown action received:", key, val)
            return
        mthd(val)


    def pause(self, signum=None, frame=None):
        self.photo_timer.cancel()
        info("Photo timer stopped")


    def resume(self, signum=None, frame=None):
        self.set_timer("photo", True)
        info("Photo timer started")


    def _read_config(self, signum=None, frame=None):
        info("_read_config called!")
        try:
            self.parser.read(CONFIG_FILE)
        except configparser.MissingSectionHeaderError as e:
            # The file exists, but doesn't have the correct format.
            raise exc.InvalidConfigurationFile(e)

        def safe_get(section, option, default=None):
            try:
                return self.parser.get(section, option)
            except (configparser.NoSectionError, configparser.NoOptionError):
                return default

        self.log_level = safe_get("frame", "log_level", "INFO")
        utils.set_log_level(self.log_level)

        self.reg_url = safe_get("host", "reg_url")
        if not self.reg_url:
            error("No registration URL in photo.cfg; exiting")
            exit()
        self.dl_url = safe_get("host", "dl_url")
        if not self.dl_url:
            error("No download URL configured in photo.cfg; exiting")
            exit()
        self.frameset = safe_get("frameset", "name", "")
        self.name = safe_get("frame", "name", "undefined")
        self.pkid = safe_get("frame", "pkid", "")
        self.watch_key = BASE_KEY.format(pkid=self.pkid)
        self.description = safe_get("frame", "description", "")
        self.orientation = safe_get("frame", "orientation", "H")
        # When to start the image rotation
        self.interval_base = safe_get("frame", "interval_base", "*:*")
        # How often to change image
        self.interval_time = int(safe_get("frame", "interval_time", 10))
        # Units of time for the image change interval
        self.interval_units = safe_get("frame", "interval_units", "minutes")
        self.interval = _normalize_interval(self.interval_time,
                self.interval_units)
        info("Setting image interval to", self.interval)
        self.set_image_interval()
        self.brightness = safe_get("monitor", "brightness")
        self.contrast = safe_get("monitor", "contrast")
        self.saturation = safe_get("monitor", "saturation")


    def set_image_interval(self):
        if not self.photo_timer:
            # Starting up
            return
        self.photo_timer.cancel()
        self.set_timer("photo", True)


    def _register(self):
        headers = {"user-agent": "photoviewer"}
        # Get free disk space
        freespace = get_freespace()
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
            self.host_images = images
            self._update_images()
        else:
            error(resp.status_code, resp.text)
            exit()


    def _update_images(self):
        """Compares the associated images received from the server, and updates
        the local copies if needed.
        """
        images = self.host_images
        curr = set([os.path.basename(img) for img in self.current_images])
        upd = set(images)
        if upd == curr:
            debug("No changes in _update_images")
            # No changes
            return False
        info("updating images...")
        to_remove = curr - upd
        debug("To remove:", *to_remove)
        freespace = get_freespace()
        debug("Freespace", freespace)
        for img in to_remove:
            curr_loc = os.path.join(PHOTODIR, img)
            # Remove the frame buffer copy, if any.
            clean_fb(curr_loc)
            if freespace < ONE_GB:
                # Just delete it
                info("deleting", curr_loc)
                os.unlink(curr_loc)
            else:
                new_loc = os.path.join(INACTIVE_PHOTODIR, img)
                info("inactivating", img)
                shutil.move(curr_loc, new_loc)
        to_get = upd - curr
        debug("To get:", *to_get)
        num_to_get = len(to_get)
        for pos, img in enumerate(to_get):
            info("adding", img)
            debug("PROCESSING IMAGE #%s of %s" % (pos, num_to_get))
            # Check if it's local
            inactive_loc = os.path.join(INACTIVE_PHOTODIR, img)
            if os.path.exists(inactive_loc):
                info("retrieving from inactive")
                active_loc = os.path.join(PHOTODIR, img)
                shutil.move(inactive_loc, active_loc)
                continue
            # Check if it's downloaded but unprocessed
            download_loc = os.path.join(DOWNLOAD_PHOTODIR, img)
            if os.path.exists(download_loc):
                info("Image already downloaded")
                continue
            # Not on disk, so download it
            self._download(img)
        self._process_images()
        self.load_images()
        self.show_photo()
        info("Image update is done!")
        return True


    def _process_images(self):
        images_to_process = glob.glob("%s/*.jpg" % DOWNLOAD_PHOTODIR)
        if not images_to_process:
            debug("No images to process")
            return
        num_imgs = len(images_to_process)
        debug("Processing  %s images" % num_imgs)
        for pos, img in enumerate(images_to_process):
            info("Processing image %s (%s of %s)" %
                    (img, pos+1, num_imgs))
            image.adjust(img, self.brightness, self.contrast, self.saturation)
            shutil.move(img, PHOTODIR)
            info("Moved processed image %s from the download to the "
                    "image directory." % img)


    def _download(self, img):
        headers = {"user-agent": "photoviewer"}
        url = "%s/%s" % (self.dl_url, img)
        debug(url)
        info("downloading", img)
        resp = requests.get(url, headers=headers, stream=True)
        if resp.status_code > 299:
            error("Failed to dowload", url, "Message:", resp.text)
            return
        outfile = os.path.join(DOWNLOAD_PHOTODIR, img)
        debug("outfile:", outfile)
        with open(outfile, "wb") as ff:
            resp.raw.decode_content = True
            shutil.copyfileobj(resp.raw, ff)
        return outfile


    def load_images(self, directory=None):
        """ Loads images from the specified directory, or, if unspecified, the
        PHOTODIR directory.
        """
        directory = directory or PHOTODIR
        fnames = glob.glob("%s/*" % os.path.abspath(directory))
        self.current_images = [fname for fname in fnames
                if IMG_PAT.match(fname)]
        random.shuffle(self.current_images)


    def navigate(self, signum=None, frame=None):
        """Moves to the next image. """
        debug("navigate called; current index", self.image_index)

        num_images = len(self.current_images)
        if not num_images:
            # Currently no images specified for this display, so just return.
            return
        new_index = self.image_index + 1
        # Boundaries
        max_index = len(self.current_images) - 1
        if new_index > max_index:
            new_index = 0
            # Shuffle the images
            info("All images shown; shuffling order.")
            random.shuffle(self.current_images)
        else:
            new_index = max(0, min(max_index, new_index))
        debug("new index", new_index)
        if new_index != self.image_index:
            self.image_index = new_index
            self.show_photo()
        elif new_index == 0:
            # There is only one image to display; make sure that it is the same
            # as the one currently displayed.
            # First, handle case where current image has been deleted locally
            img_exists = os.path.exists(self.displayed_name)
            debug("image exists:", img_exists)
            if not img_exists and self.displayed_name in self.current_images:
                self.current_images.remove(self.displayed_name)
                debug("refreshing missing images")
                self._update_images()
            displayed_file = "%s/%s" % (PHOTODIR, curr_display_list[0])
            debug("comparing:", self.displayed_name, displayed_file)
            same_files = filecmp.cmp(self.displayed_name, displayed_file)
            debug("Files are", "same" if same_files else "different")
            if not same_files:
                self.show_photo()
        self.set_timer("photo", True)


    def show_photo(self):
        if not self._started:
            return
        if not self.current_images:
            return
        try:
            fname = self.current_images[self.image_index]
        except IndexError as e:
            error("BAD INDEX", e)
            if self.current_images:
                fname = self.current_images[-1]
            else:
                # Something's screwy
                error("No images!")
                self.load_images()
                return

        if fname == self.displayed_name:
            return
        if os.path.exists(self.displayed_name):
            fb_loc = fb_path(self.displayed_name)
            if not os.path.exists(fb_loc):
                cmd = "cp -f /dev/fb0 '%s'" % fb_loc
                runproc(cmd, wait=True)
                debug("Created frame buffer copy:", fb_loc)
        runproc("/usr/bin/sudo killall fbi")
        self.displayed_name = fname
        debug("displayed image path:", self.displayed_name)
        info("Changing photo to", os.path.basename(fname))
        # See if there is already a frame buffer version of the image
        new_fb_loc = fb_path(fname)
        if os.path.exists(new_fb_loc):
            # Just copy it to the frame buffer device
            cmd = "cp -f '%s' /dev/fb0" % new_fb_loc
            runproc(cmd, wait=False)
            debug("Retrieved frame buffer copy:", self.displayed_name)
            return
        cmd = VIEWER_CMD % fname
        debug("Command:", cmd)
        runproc(cmd, wait=False)


    def _update_config(self, data):
        changed = False
        new_interval = False
        if "log_level" in data:
            self.log_level = data["log_level"]
            utils.set_log_level(self.log_level)
        for key in ("name", "description", "interval_time", "interval_units",
                "brightness", "contrast", "saturation"):
            val = data.get(key, None)
            debug("key:", key, "; val:", val)
            if val is None:
                continue
            local_val = getattr(self, key)
            if local_val != val:
                try:
                    typ = type(local_val)
                    converted = typ(val)
                except Exception:
                    pass
                setattr(self, key, converted)
                monitor_keys = ("brightness", "contrast", "saturation")
                section = "monitor" if key in monitor_keys else "frame"
                self.parser.set(section, key, str(val))
                changed = True
                new_interval = new_interval or "interval" in key
        if changed:
            with open(CONFIG_FILE, "w") as ff:
                self.parser.write(ff)
        if new_interval:
            self.interval = _normalize_interval(self.interval_time,
                    self.interval_units)
            info("Setting timer to", self.interval)
            self.set_image_interval()


    def kill_timer(self):
        """Kills all timers on receiving a Ctrl-C."""
        info("Killing timer")
        self.photo_timer.cancel()
        info("Timer canceled")


if __name__ == "__main__":
    with open("photo.pid", "w") as ff:
        ff.write("%s" % os.getpid())
    img_mgr = ImageManager()
    try:
        debug("And we're off!")
        img_mgr.start()
    except KeyboardInterrupt:
        img_mgr.kill_timer()
