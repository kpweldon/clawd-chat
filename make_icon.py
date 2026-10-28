# Draws the Clawd Chat app icon: Claude-orange squircle, cream speech bubble,
# pixel Clawd inside it. Needs Pillow:
#   pip install pillow && python3 make_icon.py   (icon1024.png is already in the repo)
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024
ORANGE, DEEP, CREAM, INK = (217, 119, 87), (193, 95, 60), (250, 247, 241), (31, 30, 29)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
# vertical orange gradient squircle (macOS icon grid: 824 px body, 100 px inset)
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
for y in range(S):
    t = y / S
    gd.line([(0, y), (S, y)], fill=tuple(int(ORANGE[i] * (1 - t) + DEEP[i] * t) for i in range(3)) + (255,))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([100, 100, 924, 924], radius=185, fill=255)
img.paste(grad, (0, 0), mask)

d = ImageDraw.Draw(img)
# speech bubble with a tail bottom-left
d.rounded_rectangle([200, 250, 824, 700], radius=110, fill=CREAM)
d.polygon([(290, 660), (430, 690), (250, 800)], fill=CREAM)

# Clawd, 18x10 grid, centred in the bubble
cells = [(4, 0, 10, 4), (2, 4, 14, 2), (4, 6, 10, 2), (5, 8, 1, 2), (7, 8, 1, 2), (10, 8, 1, 2), (12, 8, 1, 2)]
eyes = [(6, 2, 1, 2), (11, 2, 1, 2)]
u = 26
ox, oy = (S - 18 * u) // 2, 475 - 5 * u
for x, y, w, h in cells:
    d.rectangle([ox + x * u, oy + y * u, ox + (x + w) * u - 1, oy + (y + h) * u - 1], fill=ORANGE)
for x, y, w, h in eyes:
    d.rectangle([ox + x * u, oy + y * u, ox + (x + w) * u - 1, oy + (y + h) * u - 1], fill=INK)

img.save(os.path.join(HERE, "icon1024.png"))
print("icon1024.png")
