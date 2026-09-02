"""
gcp_handler.py

Helpers to record surveyed Ground Control Points (GCPs) captured from the
GNSS receiver and export them in formats suitable for photogrammetry
pipelines such as WebODM / OpenDroneMap.

Two artefacts are produced:

1. A survey **CSV** (``append_gcp_csv``) holding the full field record for
   each point - name, position, accuracy, fix type, satellites and
   timestamp. This is the durable human/general-purpose record.

2. A WebODM / ODM **gcp_list.txt** starter (``write_gcp_list``). The ODM
   format is::

       <crs header, e.g. EPSG:4326>
       geo_x geo_y geo_z pixel_x pixel_y image_name [gcp_label]

   The pixel/image columns can only be filled once imagery exists, so the
   exported rows carry placeholder pixel/image tokens for you to complete in
   the WebODM GCP interface. For EPSG:4326 the ODM convention is
   geo_x = longitude, geo_y = latitude.

   See https://docs.opendronemap.org/gcp/

Created on 2 Sep 2026

:author: semuadmin (Steve Smith)
:copyright: 2020 semuadmin
:license: BSD 3-Clause
"""

import csv
from pathlib import Path

# CSV column order for the survey record
GCP_CSV_FIELDS = (
    "name",
    "lat",
    "lon",
    "hae",
    "msl",
    "hacc",
    "vacc",
    "fix",
    "sip",
    "utc",
    "datetime",
)
# placeholder tokens for the ODM pixel/image columns (completed in WebODM)
ODM_PIXEL_PLACEHOLDER = "0 0"
ODM_IMAGE_PLACEHOLDER = "EDIT_IN_WEBODM.jpg"


def append_gcp_csv(csvfile: str, gcp: dict) -> Path:
    """
    Append a single GCP record to the survey CSV, writing a header row first
    if the file is new or empty.

    :param str csvfile: fully-qualified path to survey CSV
    :param dict gcp: GCP record keyed by :data:`GCP_CSV_FIELDS`
    :return: path written
    :rtype: Path
    """

    path = Path(csvfile)
    path.parent.mkdir(parents=True, exist_ok=True)
    newfile = not path.exists() or path.stat().st_size == 0
    with open(path, "a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=GCP_CSV_FIELDS, extrasaction="ignore")
        if newfile:
            writer.writeheader()
        writer.writerow({key: gcp.get(key, "") for key in GCP_CSV_FIELDS})
    return path


def write_gcp_list(
    txtfile: str,
    gcps: list,
    crs: str = "EPSG:4326",
    alt_key: str = "hae",
) -> Path:
    """
    Write a WebODM / ODM ``gcp_list.txt`` starter from a list of GCP records.

    The pixel and image-name columns are written as placeholders; complete
    them in the WebODM GCP interface once you have imagery. For EPSG:4326 the
    exported column order is longitude latitude altitude.

    :param str txtfile: fully-qualified path to output gcp_list.txt
    :param list gcps: list of GCP record dicts (see :data:`GCP_CSV_FIELDS`)
    :param str crs: coordinate reference system header line (GDAL/proj/EPSG)
    :param str alt_key: which altitude to use for geo_z ('hae' or 'msl')
    :return: path written
    :rtype: Path
    """

    path = Path(txtfile)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write(f"{crs}\n")
        for gcp in gcps:
            lat = float(gcp.get("lat", 0.0))
            lon = float(gcp.get("lon", 0.0))
            alt = float(gcp.get(alt_key, 0.0))
            name = str(gcp.get("name", "")).replace(" ", "_") or "GCP"
            # ODM order for geographic CRS is geo_x=lon geo_y=lat geo_z=alt
            file.write(
                f"{lon:.9f} {lat:.9f} {alt:.3f} "
                f"{ODM_PIXEL_PLACEHOLDER} {ODM_IMAGE_PLACEHOLDER} {name}\n"
            )
    return path
