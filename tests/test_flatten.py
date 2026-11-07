"""flatten(): post metadata -> one videos.csv row."""
from __future__ import annotations

import json

from tiktok_dl.sites import tiktok

VIDEO_ITEM = {
    "id": "7300000000000000001",
    "createTime": "1700000000",
    "desc": "Hello #fyp @friend",
    "author": {"uniqueId": "tiktok", "nickname": "TikTok", "id": "107955", "verified": True},
    "music": {"id": "m1", "title": "original sound", "authorName": "TikTok", "original": True, "duration": "15"},
    "video": {
        "duration": 15,
        "width": "1080",
        "height": 1920,
        "ratio": "720p",
        "codecType": "h264",
        "bitrate": "1500000",
        "size": "2900000",
        "subtitleInfos": [{"LanguageCodeName": "eng-US"}, {"LanguageCodeName": "vie-VN"}],
    },
    "stats": {"playCount": 100, "diggCount": 10, "commentCount": 2, "shareCount": 1, "collectCount": 3},
    "statsV2": {"playCount": "123456789", "repostCount": "4"},
    "textExtra": [{"hashtagName": "fyp"}, {"userUniqueId": "friend"}, {"hashtagName": ""}],
    "locationCreated": "VN",
    "textLanguage": "en",
    "isAd": False,
    "IsAigc": False,
    "diversificationLabels": ["Comedy", "Entertainment"],
    "suggestedWords": ["funny"],
    "stickersOnItem": [{"stickerText": ["hi", "there"]}, {"stickerText": ["again"]}],
    "effectStickers": [{"name": "Green Screen"}],
    "duetEnabled": True,
    "stitchEnabled": True,
    "shareEnabled": True,
    "itemCommentStatus": 0,
    "privateItem": False,
    "isPinnedItem": True,
    "warnInfo": [{"text": "Sensitive"}],
}

SLIDE_ITEM = {
    "id": "7300000000000000002",
    "createTime": 1700000000,
    "desc": "",
    "author": {"uniqueId": "photos"},
    "imagePost": {"images": [{"imageURL": {}}, {"imageURL": {}}, {"imageURL": {}}]},
}


def test_flatten_video():
    row = tiktok.flatten(VIDEO_ITEM)
    assert row["id"] == "7300000000000000001"
    assert row["url"] == "https://www.tiktok.com/@tiktok/video/7300000000000000001"
    assert row["type"] == "video"
    assert row["created"] == "2023-11-14T22:13:20+00:00"
    assert row["username"] == "tiktok"
    assert row["nickname"] == "TikTok"
    assert row["author_verified"] is True
    assert row["description"] == "Hello #fyp @friend"
    assert row["hashtags"] == "#fyp"
    assert row["mentions"] == "@friend"
    # statsV2 (strings) overrides stats; numbers become ints.
    assert row["plays"] == 123456789
    assert row["likes"] == 10
    assert row["reposts"] == 4
    assert row["width"] == 1080
    assert row["bitrate"] == 1500000
    assert row["file_size"] == 2900000
    assert row["definition"] == "720p"
    assert row["image_count"] is None
    assert row["subtitles"] == "eng-US vie-VN"
    assert row["music_duration"] == 15
    assert row["diversification_labels"] == "Comedy | Entertainment"
    assert row["sticker_texts"] == "hi | there | again"
    assert row["effects"] == "Green Screen"
    assert row["pinned"] is True
    assert json.loads(row["warn_info"]) == [{"text": "Sensitive"}]


def test_flatten_slideshow():
    row = tiktok.flatten(SLIDE_ITEM)
    assert row["type"] == "slide"
    assert row["image_count"] == 3
    assert row["url"] == "https://www.tiktok.com/@photos/video/7300000000000000002"


def test_flatten_minimal_item():
    row = tiktok.flatten({"id": "1"})
    assert row["type"] == "video"
    assert row["username"] == ""
    assert row["created"] == "1970-01-01T00:00:00+00:00"
    assert row["hashtags"] == ""
    assert row["plays"] is None
    assert row["subtitles"] is None
    assert row["warn_info"] is None
    assert row["is_ad"] is False
    assert row["duet_enabled"] is False


def test_flatten_columns_are_stable():
    # videos.csv uses the keys as its header, so every row must have the same columns.
    columns = list(tiktok.flatten(VIDEO_ITEM))
    assert list(tiktok.flatten(SLIDE_ITEM)) == columns
    assert list(tiktok.flatten({"id": "1"})) == columns
