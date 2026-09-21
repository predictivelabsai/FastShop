"""Assemble verified browser captures into a reproducible, labelled product tour."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = ROOT / "output/playwright/dual-flow"
SLIDES = [
    ("desktop-chat-preview.png", "Build with chat + a shared draft preview"),
    ("desktop-classical-design.png", "Switch to classical design controls"),
    ("desktop-merchant-details.png", "Replace and review sample merchant details"),
    ("desktop-merchant-review.png", "Approve proposed shipping changes explicitly"),
    ("mobile-chat.png", "Continue on your phone"),
    ("mobile-preview.png", "Preview the same draft on mobile"),
    ("desktop-demo-checkout.png", "Try checkout with no real payment"),
    ("mobile-demo-account.png", "Manage demo orders and subscriptions"),
    ("desktop-demo-inbox.png", "Keep simulated email in a private local inbox"),
]


def main():
    font_file = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    font = ImageFont.truetype(str(font_file), 20) if font_file.exists() else ImageFont.load_default()
    frames = []
    for filename, label in SLIDES:
        with Image.open(CAPTURES / filename) as source:
            capture = ImageOps.contain(source.convert("RGB"), (1000, 660))
        frame = Image.new("RGB", (1000, 740), "#f5f7f1")
        draw = ImageDraw.Draw(frame)
        draw.rectangle((0, 0, 1000, 51), fill="#174c39")
        draw.text((20, 14), "FastShop | " + label, font=font, fill="white")
        frame.paste(capture, ((1000 - capture.width) // 2, 58))
        draw.text((20, 714), "Local guided demo · draft changes only · no real payments", font=font, fill="#244333")
        frames.append(frame.quantize(colors=128, method=Image.Quantize.MEDIANCUT))
    target = ROOT / "static/productdemo.gif"
    frames[0].save(target, save_all=True, append_images=frames[1:], duration=3000, loop=0, optimize=True, disposal=2)
    with Image.open(target) as result:
        assert result.n_frames == len(SLIDES) and result.size == (1000, 740)
    print({"path": str(target), "frames": len(SLIDES), "bytes": target.stat().st_size})


if __name__ == "__main__":
    main()
