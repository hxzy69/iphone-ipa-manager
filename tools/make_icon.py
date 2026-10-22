"""Render the geometric app mark for Tk and Windows resources."""

from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def main():
    scale = 4
    image = Image.new("RGBA", (256 * scale, 256 * scale))
    draw = ImageDraw.Draw(image)

    def box(coordinates):
        return tuple(value * scale for value in coordinates)

    draw.rounded_rectangle(box((0, 0, 255, 255)), radius=52 * scale, fill="#101820")
    draw.rounded_rectangle(box((61, 25, 193, 231)), radius=30 * scale, fill="#edf3f7")
    draw.rounded_rectangle(box((71, 35, 183, 221)), radius=20 * scale, fill="#19252f")
    draw.line(box((109, 51, 145, 51)), fill="#9bafbd", width=8 * scale)
    draw.ellipse(box((126, 128, 230, 232)), fill="#101820")
    draw.ellipse(box((132, 134, 224, 226)), fill="#49d8bd")
    draw.line(
        [(156 * scale, 180 * scale), (171 * scale, 195 * scale), (200 * scale, 162 * scale)],
        fill="#101820",
        width=11 * scale,
        joint="curve",
    )
    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    image.save(ROOT / "assets/app.png")
    image.save(
        ROOT / "assets/app.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
