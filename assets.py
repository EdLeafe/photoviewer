from datetime import datetime

import httpx

import utils
from utils import debug


def format_datetime(dt=None):
    dt = dt or datetime.now()
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


START = format_datetime(datetime(2000, 1, 1))
END = format_datetime(datetime(9999, 12, 31))


class AssetManager:
    def __init__(self):
        self._version = "v1.2"
        self.base_uri = f"http://localhost/api/{self._version}/assets"
        self.image_uri = "https://com-leafe-images.nyc3.cdn.digitaloceanspaces.com/photoviewer"

    def list_assets(self):
#         debug(f"list_assets() GET {self.base_uri}")
        resp = httpx.get(self.base_uri)
        debug(f"list_assets() RESPONSE: {resp.status_code}")
        content = resp.json()
#         debug(f"list_assets() RESPONSE: {content}")
        return content

    def add_assets(self, assets):
        curr_names = [img["name"] for img in self.list_assets()]
        for name in [nm for nm in assets if nm not in curr_names]:
            debug(f"Calling add_asset() with '{name}'.")
            self.add_asset(name)

    def add_asset(
        self,
        name,
        uri=None,
        enabled=False,
        duration=10,
        active=False,
        processing=False,
        nocache=False,
        play_order=0,
        skip_asset_check=False,
    ):
        uri = utils.url_quote(uri or f"{self.image_uri}/{name}")
        body = {
            "name": name,
            "uri": uri,
            "is_enabled": (1 if enabled else 0),
            "duration": f"{duration or 10000}",
            "mimetype": "image",
            "start_date": START,
            "end_date": END,
            "is_active": (1 if active else 0),
            "is_processing": (1 if processing else 0),
            "nocache": (1 if nocache else 0),
            "play_order": play_order,
            "skip_asset_check": (1 if skip_asset_check else 0),
        }
        debug(f"add_asset() POST {uri}")
        debug(f"add_asset() BODY: {body}")
        resp = httpx.post(self.base_uri, json=body)
        debug(f"add_asset() RESPONSE: {resp.status_code}")
        return 200 <= resp.status_code < 300

    def show(self, name):
        imgs = self.list_assets()
        new_image_ids = [img["asset_id"] for img in imgs if img["name"] == name]
        if not new_image_ids:
            # ID doesn't exist yet
            return False
        new_image_id = new_image_ids[0]
        active_ids = [img["asset_id"] for img in imgs if img["is_active"]]
        [self.set_active(id_, False) for id_ in active_ids]
        return self.set_active(new_image_id, True)

    def set_active(self, img_or_id, active):
        img_id = img_or_id if isinstance(img_or_id, str) else img_or_id["asset_id"]
        uri = f"{self.base_uri}/{img_id}"
        val = 1 if active else 0
        body = {"is_active": val, "is_enabled": val}
        debug(f"set_active() PATCH {uri}")
        debug(f"set_active() BODY: {body}")
        resp = httpx.patch(uri, json=body)
        debug(f"set_active() RESPONSE: {resp.status_code}")
        return 200 <= resp.status_code < 300

    def delete_asset(self, img_or_id):
        """Removes the specified asset"""
        img_id = img_or_id if isinstance(img_or_id, str) else img_or_id["asset_id"]
        uri = f"{self.base_uri}/{img_id}"
        resp = httpx.delete(uri)
        debug(f"delete() RESPONSE: {resp.status_code}")
        return 200 <= resp.status_code < 300
