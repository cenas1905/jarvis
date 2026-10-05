"""Lightweight, text-free room visualization. No network or image assets."""
import math
from PIL import Image, ImageTk


class RoomVisual:
    BACKGROUND = "#030710"

    def __init__(self):
        self._size = None
        self._glow = None
        self._energy = 0.0
        self._color = (74, 214, 219)

    def draw(self, canvas, width, height, tick, state, level=0.0):
        cx, cy = width / 2, height / 2
        radius = min(width, height) * 0.205
        size = (width, height)
        if self._size != size:
            # Cache the atmospheric background; resizing is the only PIL work.
            source = Image.new("RGB", (240, 160))
            pixels = source.load()
            for y in range(160):
                for x in range(240):
                    d = ((x - 120) / 85) ** 2 + ((y - 80) / 67) ** 2
                    a = math.exp(-d * 2.4)
                    pixels[x, y] = (int(3 + 4*a), int(7 + 16*a), int(16 + 30*a))
            self._glow = ImageTk.PhotoImage(source.resize(size, Image.Resampling.BILINEAR))
            self._size = size
        canvas.create_image(0, 0, image=self._glow, anchor="nw")
        palette = {
            "SPEAKING": (119, 185, 255), "LISTENING": (70, 227, 213),
            "THINKING": (151, 136, 244), "INITIALISING": (151, 136, 244),
            "PAUSED": (68, 85, 110), "MUTED": (68, 85, 110),
            "ERROR": (255, 108, 132),
        }
        target = palette.get(state, palette["LISTENING"])
        self._color = tuple(a + (b-a)*0.10 for a, b in zip(self._color, target))
        self._energy += (max(0.0, min(1.0, level)) - self._energy)*0.28
        t = tick * 0.04
        energy = self._energy if state == "SPEAKING" else 0.0

        def color(strength, white=0.0):
            rgb = [max(0, min(255, int((c*(1-white)+255*white)*strength))) for c in self._color]
            return "#%02x%02x%02x" % tuple(rgb)

        # Spacious orbit and quiet satellite dots keep the centre uncluttered.
        for factor, strength in ((1.49, 0.065), (1.34, 0.11), (0.68, 0.08)):
            rr = radius*factor
            canvas.create_oval(cx-rr, cy-rr, cx+rr, cy+rr, outline=color(strength))
        for i in range(36):
            angle = i*math.tau/36 + t*0.035
            rr = radius*1.34
            x, y = cx+math.cos(angle)*rr, cy+math.sin(angle)*rr
            dot = 1.7 if i % 9 == 0 else 0.8
            canvas.create_oval(x-dot, y-dot, x+dot, y+dot,
                               fill=color(0.5 if i % 9 == 0 else 0.18), outline="")

        # Several continuous ribbons: gentle idle breathing, audio-driven motion.
        breath = 1 + 0.018*math.sin(t*1.5) + energy*0.12
        for layer in range(9):
            phase = layer*0.28
            points = []
            for i in range(145):
                a = math.tau*i/144
                ripple = (0.027 + energy*0.08)*math.sin(a*3+t*1.5+phase)
                ripple += (0.016+energy*0.025)*math.cos(a*5-t*1.1-phase)
                rr = radius*(breath+ripple+(layer-4)*0.009)
                points.extend((cx+math.cos(a)*rr, cy+math.sin(a)*rr))
            strength = 0.14 + (1-abs(layer-4)/5)*0.50
            canvas.create_line(*points, fill=color(strength, 0.15),
                               width=1.4 if layer != 4 else 2.6, smooth=True)
        # Soft inner motes have deterministic positions; no per-frame randomness.
        for i in range(54):
            angle = i*2.39996+t*(0.055 if i % 2 else -0.045)
            rr = radius*(0.15+0.58*math.sqrt((i+1)/54))
            x, y = cx+math.cos(angle)*rr, cy+math.sin(angle)*rr*0.88
            dot = 0.7 + (i % 4)*0.25
            strength = 0.16+0.19*(0.5+0.5*math.sin(t+i))
            canvas.create_oval(x-dot, y-dot, x+dot, y+dot, fill=color(strength), outline="")
        # Tiny bottom light is the microphone/connection state, without labels.
        dot = 3
        canvas.create_oval(cx-dot, height-42-dot, cx+dot, height-42+dot,
                           fill=color(0.75), outline="")
