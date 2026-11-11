"""Read Fig8's white markers using axis ticks; no cutting labels used."""

import csv
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image


def main():
    folder = Path(__file__).resolve().parent
    raster = folder / "page9_image_77.png"
    gray = np.asarray(Image.open(raster).convert("L"))
    # Centers of the visible two-krpm and ten-Hz axis ticks, in source pixels.
    x_ticks = [230.5, 327.5, 425.5, 524, 622, 720, 817.5, 915.5, 1013.5,
               1111.5, 1209.5, 1307.5, 1405.5, 1502.5, 1601, 1699.5, 1797.5, 1895.5]
    y_ticks = [18, 226, 434, 641.5, 848.5, 1057.5, 1264.5]
    x_fit = np.polyfit(np.arange(0, 35, 2) * 1000, x_ticks, 1)
    y_fit = np.polyfit(y_ticks, np.arange(730, 669, -10), 1)
    rows = []
    for rpm in [0, *range(3000, 34001, 1000)]:
        x = float(np.polyval(x_fit, rpm))
        left, right = round(x) - 4, round(x) + 5
        yy, _ = np.nonzero(gray[100:1150, left:right] > 128)
        if not len(yy):
            raise ValueError(f"No marker at {rpm} rpm; inspect source instead of filling it.")
        y = float(np.median(yy) + 100)
        rows.append({"rpm": rpm, "frequency_hz": round(float(np.polyval(y_fit, y)), 4),
                     "pixel_x": round(x, 4), "pixel_y": y})
    output = folder / "fig8_frequency.csv"
    if output.exists():
        raise FileExistsError("Keep the first digitization; rerun in a new copied folder.")
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    metadata = {"source_raster_sha256": hashlib.sha256(raster.read_bytes()).hexdigest(),
                "table_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "method": "Median white pixel at prespecified marker rpm; linear axis calibration",
                "x_axis_tick_centers": x_ticks, "y_axis_tick_centers": y_ticks,
                "source": "Gradisek2005 Fig8, p777, independently measured rotating-tool frequency",
                "limitations": "Single-curve raster digitization; not a full operating FRF. No measured compliance or damping table.",
                "frequency_perturbation_hz_for_diagnostic": 0.5,
                "perturbation_is_statistical_confidence_interval": False}
    (folder / "frequency_digitization.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    fig, ax = plt.subplots(figsize=(12, 9))
    ax.imshow(gray, cmap="gray", vmin=0, vmax=255)
    ax.scatter([r["pixel_x"] for r in rows], [r["pixel_y"] for r in rows],
               facecolors="none", edgecolors="red", s=75, linewidths=0.7)
    ax.set_title("Fig8 source marker locations used for frequency-only digitization")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(folder / "frequency_digitization_overlay.png", dpi=160)
    plt.close(fig)
    print(json.dumps({"rows": len(rows), "first": rows[0],
                      "minimum": min(rows, key=lambda r: r["frequency_hz"])}))


if __name__ == "__main__":
    main()
