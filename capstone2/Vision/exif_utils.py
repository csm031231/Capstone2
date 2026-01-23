from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from typing import Optional, Tuple
from datetime import datetime
from Vision.dto import ExifInfo


def get_exif_data(image: Image.Image) -> dict:
    """이미지에서 EXIF 데이터 추출"""
    exif_data = {}

    try:
        exif = image._getexif()
        if exif:
            for tag_id, value in exif.items():
                tag = TAGS.get(tag_id, tag_id)
                exif_data[tag] = value
    except Exception:
        pass

    return exif_data


def get_gps_info(exif_data: dict) -> Optional[Tuple[float, float]]:
    """EXIF에서 GPS 좌표 추출"""
    if "GPSInfo" not in exif_data:
        return None

    gps_info = exif_data["GPSInfo"]
    gps_data = {}

    for key in gps_info.keys():
        decode = GPSTAGS.get(key, key)
        gps_data[decode] = gps_info[key]

    try:
        lat = gps_data.get("GPSLatitude")
        lat_ref = gps_data.get("GPSLatitudeRef")
        lon = gps_data.get("GPSLongitude")
        lon_ref = gps_data.get("GPSLongitudeRef")

        if lat and lon:
            lat_val = convert_to_degrees(lat)
            lon_val = convert_to_degrees(lon)

            if lat_ref == "S":
                lat_val = -lat_val
            if lon_ref == "W":
                lon_val = -lon_val

            return (lat_val, lon_val)
    except Exception:
        pass

    return None


def convert_to_degrees(value) -> float:
    """GPS 좌표를 도(degree)로 변환"""
    d = float(value[0])
    m = float(value[1])
    s = float(value[2])
    return d + (m / 60.0) + (s / 3600.0)


def get_datetime(exif_data: dict) -> Optional[datetime]:
    """EXIF에서 촬영 시간 추출"""
    datetime_str = exif_data.get("DateTimeOriginal") or exif_data.get("DateTime")

    if datetime_str:
        try:
            return datetime.strptime(datetime_str, "%Y:%m:%d %H:%M:%S")
        except Exception:
            pass

    return None


def get_device_info(exif_data: dict) -> Optional[str]:
    """EXIF에서 기기 정보 추출"""
    make = exif_data.get("Make", "")
    model = exif_data.get("Model", "")

    if make or model:
        return f"{make} {model}".strip()

    return None


def extract_exif_info(image: Image.Image) -> ExifInfo:
    """이미지에서 모든 EXIF 정보 추출"""
    exif_data = get_exif_data(image)

    gps = get_gps_info(exif_data)
    taken_at = get_datetime(exif_data)
    device = get_device_info(exif_data)

    return ExifInfo(
        latitude=gps[0] if gps else None,
        longitude=gps[1] if gps else None,
        taken_at=taken_at,
        device=device
    )
