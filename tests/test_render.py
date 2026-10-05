import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
import render


class RendererTests(unittest.TestCase):
    def test_invalid_profiles(self):
        for data in ({}, {'buttons': {}}, {'buttons': {'typo': {'bindings': ['a']}}},
                     {'buttons': {'scroll': {'bindings': 'a'}}},
                     {'buttons': {'scroll': {'bindings': ['a'], 'background': '#fff'}}},
                     {'buttons': {'scroll': {'bindings': []}}}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                render.validate(data)

    def test_font_size_validation(self):
        for size in (True, 0, 7, 129, 32.5, '32', None):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, 'font_size'):
                render.validate({'font_size': size, 'buttons': {'scroll': {'bindings': ['Map']}}})

    def test_custom_font_size_scales_rendered_text_and_cards(self):
        profile = {'title': 'Font size example',
                   'buttons': {'scroll': {'title': 'Scroll', 'bindings': ['Map → zoom']}}}
        with tempfile.TemporaryDirectory() as directory:
            for size in (None, 32, 64):
                data = dict(profile)
                if size is not None:
                    data['font_size'] = size
                with patch.object(render, 'load_font', wraps=render.load_font) as loader:
                    render.render(data, Path(directory)/f'{size}.png')
                expected = 28 if size is None else size
                self.assertEqual([call.args[0] for call in loader.call_args_list],
                                 [expected, expected+12])
        small = render.make_cards(profile, render.load_font(28))[0]
        large = render.make_cards(profile, render.load_font(64))[0]
        self.assertGreater(large['h'], small['h'])
        self.assertGreater(large['line_height'], small['line_height'])

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

    def test_default_backgrounds_contrast_with_white(self):
        for color in render.PALETTE:
            with self.subTest(color=color):
                rgb = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)]
                light = sum((c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4)*w
                            for c,w in zip(rgb,(.2126,.7152,.0722)))
                self.assertGreaterEqual(1.05/(light+.05), 4.5)

    def test_all_text_is_white_with_black_outline(self):
        profile = {'title': 'Image title', 'buttons': {
            'scroll': {'title': 'Card title', 'bindings': ['Binding'], 'background': '#ffffff'},
            'front_edge': {'bindings': ['Another binding']}}}
        original = ImageDraw.ImageDraw.text
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(ImageDraw.ImageDraw, 'text', autospec=True, side_effect=original) as text:
                render.render(profile, Path(directory)/'text.png')
            self.assertEqual(text.call_count, 4)
            for call in text.call_args_list:
                self.assertEqual(call.kwargs['fill'], '#ffffff')
                self.assertEqual(call.kwargs['stroke_fill'], '#000000')
                self.assertGreater(call.kwargs['stroke_width'], 0)

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

    def test_readability_options_are_opt_in(self):
        wow = json.loads((render.ROOT/'WoW.json').read_text())
        classic = dict(wow)
        classic.pop('layout')
        classic.pop('scale_text_outline')
        font = render.load_font(classic['font_size'])
        cards = render.make_cards(classic, font)
        for card in cards:
            self.assertEqual(card['side'], render.BUTTONS[card['name']][0])
            self.assertLessEqual(card['w'], 446)
        readable_cards = render.make_cards(wow, font)
        self.assertGreater(max(c['w'] for c in readable_cards), 446)
        for options in ({'layout': 'unknown'}, {'layout': None},
                        {'scale_text_outline': 1}, {'scale_text_outline': 'true'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                render.validate(dict(classic, **options))
        original = ImageDraw.ImageDraw.text
        with tempfile.TemporaryDirectory() as directory:
            for scaled, expected in ((False, 1), (True, 2)):
                profile = {'font_size': 64, 'scale_text_outline': scaled,
                           'buttons': {'scroll': {'bindings': ['Zoom']}}}
                with patch.object(ImageDraw.ImageDraw, 'text', autospec=True, side_effect=original) as text:
                    render.render(profile, Path(directory)/f'{scaled}.png')
                self.assertEqual(text.call_args.kwargs['stroke_width'], expected)

    def test_wow_labels_remain_large_at_notes_display_width(self):
        wow = json.loads((render.ROOT/'WoW.json').read_text())
        font = render.load_font(wow['font_size'])
        cards = render.make_cards(wow, font)
        (width,height), (ox,oy) = render.layout(cards, 100)
        # The supplied notes screenshot displays each image at 767px wide.
        self.assertGreaterEqual(font.size * 767 / width, 24)
        sides = {card['name']: card['side'] for card in cards}
        self.assertEqual(sides['right_trigger'], 'top')
        self.assertEqual(sides['top_thumb'], 'bottom')
        self.assertEqual(sides['left_trigger'], 'bottom')
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
