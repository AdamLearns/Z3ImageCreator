import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
import render


class RendererTests(unittest.TestCase):
    def test_invalid_profiles(self):
        for data in ({}, {'buttons': {}}, {'buttons': {'typo': {'bindings': ['a']}}},
                     {'buttons': {'scroll': {'bindings': 'a'}}},
                     {'buttons': {'scroll': {'bindings': ['a'], 'background': '#fff'}}},
                     {'buttons': {'scroll': {'bindings': []}}}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                render.validate(data)

    def test_default_colors_are_unique_and_stable(self):
        font = render.load_font(28)
        profile = {'buttons': {name: {'bindings': ['Example']} for name in render.BUTTONS}}
        cards = render.make_cards(profile, font)
        colors = {c['name']: c['color'] for c in cards}
        self.assertEqual(len(set(colors.values())), len(render.BUTTONS))
        wow = json.loads((render.ROOT/'WoW.json').read_text())
        wow_cards = render.make_cards(wow, font)
        self.assertEqual(len({c['color'] for c in wow_cards}), len(wow_cards))
        for card in wow_cards:
            self.assertEqual(card['color'], colors[card['name']])

    def test_contrast(self):
        for r in range(0, 256, 17):
            for g in range(0, 256, 17):
                for b in range(0, 256, 17):
                    color = f'#{r:02x}{g:02x}{b:02x}'
                    rgb = [c/255 for c in (r,g,b)]
                    light = sum((c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4)*w
                                for c,w in zip(rgb,(.2126,.7152,.0722)))
                    ratio = (light+.05)/.05 if render.foreground(color) == '#000000' else 1.05/(light+.05)
                    self.assertGreaterEqual(ratio, 4.5)

    def test_long_cards_do_not_overlap(self):
        font = render.load_font(28)
        data = {'buttons': {name: {'title': 'Long heading '*4,
                    'bindings': ['Long binding with Unicode → ↑ ↓ '*5, 'x'*100]}
                    for name in render.BUTTONS}}
        cards = render.make_cards(data, font)
        (width,height), (ox,oy) = render.layout(cards, 78)
        boxes = [c['box'] for c in cards] + [(ox,oy,ox+1036,oy+1218)]
        for i,a in enumerate(boxes):
            self.assertTrue(0 <= a[0] < a[2] <= width and 0 <= a[1] < a[3] <= height)
            for b in boxes[i+1:]:
                self.assertFalse(a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1])

    def test_render_wow_and_all_buttons(self):
        wow = json.loads((render.ROOT/'WoW.json').read_text())
        self.assertIn('Right finger: end movement', wow['buttons']['top_thumb']['bindings'])
        profiles = [wow, {'buttons': {name: {'title': name, 'bindings': ['Example → binding']}
                                      for name in render.BUTTONS}}]
        with tempfile.TemporaryDirectory() as directory:
            for index, profile in enumerate(profiles):
                path = Path(directory)/f'{index}.png'
                size = render.render(profile,path)
                with Image.open(path) as image:
                    self.assertEqual(image.size,size)
                    self.assertEqual(image.format,'PNG')

    def test_batch_continues_after_invalid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'a.json').write_text('{bad')
            (root/'b.JSON').write_text(json.dumps({'buttons': {'scroll': {'bindings': ['Map']}}}))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = render.main(['--input-dir',directory,'--output-dir',str(root/'out')])
            self.assertEqual(code,1)
            self.assertTrue((root/'out/b.png').exists())
            self.assertIn('Processed b.JSON',out.getvalue())
            self.assertIn('Skipped a.json',err.getvalue())

    def test_routes_avoid_obstacle(self):
        mask = Image.new('L',(120,120))
        from PIL import ImageDraw
        ImageDraw.Draw(mask).rectangle((45,0,75,85),fill=255)
        path = render.route(mask,(12,12),(108,12))
        line = Image.new('L',mask.size)
        ImageDraw.Draw(line).line(path,fill=255)
        self.assertFalse(any(a and b for a,b in zip(mask.tobytes(),line.tobytes())))


if __name__ == '__main__':
    unittest.main()
