#!/usr/bin/env python3
"""Render JSON button bindings on the supplied Swiftpoint mouse photograph."""
from __future__ import annotations

import argparse
import heapq
import json
import math
from pathlib import Path
import re
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
# Distinct dark colors, each with at least 4.5:1 contrast against white.
PALETTE = ('#4b5563', '#79502e', '#566b24', '#b52e39', '#a04c13', '#285bb4',
           '#006d8b', '#ad285b', '#007568', '#7044cb', '#7c6516')
BACKGROUND = '#191919'
# Visible button surfaces, in pixels of the unmodified 1036 x 1218 base.
# Main click surfaces exclude the fingertip caps mounted above them.
BUTTONS = {
    'left_click': ('top', (192, 112), [(104,166),(207,73),(335,250),(272,299),(276,360)]),
    'right_click': ('top', (412, 80), [(282,64),(527,57),(641,213),(574,218),(479,232),(413,215)]),
    'scroll': ('top', (396, 223), [(331,222),(406,203),(466,300),(489,381),(455,420),(412,343)]),
    'front_edge': ('left', (79, 255), [(29,247),(108,190),(184,298),(101,337)]),
    'rear_edge': ('left', (172, 377), [(112,344),(189,313),(291,450),(182,426)]),
    'left_fingertip': ('left', (305, 350), [(281,319),(366,291),(410,370),(365,397),(302,381)]),
    'right_fingertip': ('top', (513, 249), [(479,240),(552,213),(598,257),(612,314),(562,328),(507,291)]),
    'left_trigger': ('right', (487, 499), [(399,477),(527,433),(568,481),(566,519),(433,548),(400,522)]),
    'right_trigger': ('right', (655, 411), [(582,398),(678,355),(722,397),(712,431),(625,472),(584,444)]),
    'top_thumb': ('left', (321, 635), [(275,574),(299,566),(375,682),(378,715),(358,721)]),
    'bottom_thumb': ('left', (255, 590), [(250,552),(259,551),(261,579),(350,725),(399,762),(400,775),(344,750),(253,604)]),
}


def validate(data):
    """Strict validation prevents unrelated JSON files silently becoming images."""
    if not isinstance(data, dict) or set(data) - {'title', 'font_size', 'buttons'}:
        raise ValueError('expected an object with only title, font_size, and buttons')
    if 'title' in data and not isinstance(data['title'], str):
        raise ValueError('title must be a string')
    size = data.get('font_size', 28)
    if type(size) is not int or not 8 <= size <= 128:
        raise ValueError('font_size must be an integer from 8 to 128 pixels')
    if not isinstance(data.get('buttons'), dict) or not data['buttons']:
        raise ValueError('buttons must be a nonempty object')
    for name, item in data['buttons'].items():
        if name not in BUTTONS:
            raise ValueError(f'unknown button {name!r}; see README for names')
        if not isinstance(item, dict) or set(item) - {'title', 'bindings', 'background'}:
            raise ValueError(f'{name}: expected title, bindings, and/or background')
        if 'title' in item and not isinstance(item['title'], str):
            raise ValueError(f'{name}: title must be a string')
        bindings = item.get('bindings')
        if not isinstance(bindings, list) or any(not isinstance(v, str) or not v.strip() for v in bindings):
            raise ValueError(f'{name}: bindings must be an array of nonempty strings')
        if not bindings and not item.get('title', '').strip():
            raise ValueError(f'{name}: provide a title or at least one binding')
        if 'background' in item and (not isinstance(item['background'], str) or
                not re.fullmatch(r'#[0-9a-fA-F]{6}', item['background'])):
            raise ValueError(f'{name}: background must be an opaque #RRGGBB color')
    return data


def load_font(size, override=None):
    candidates = [override] if override else [
        '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        'C:/Windows/Fonts/arial.ttf', 'DejaVuSans.ttf']
    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            pass
    raise ValueError('No Unicode font found. Install DejaVu Sans or pass --font /path/font.ttf')


def wrap(text, font, width):
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split():
            if line and font.getlength(line + ' ' + word) > width:
                lines.append(line)
                line = ''
            # Split long tokens too, so even unbroken bindings stay in their card.
            for char in (' ' if line else '') + word:
                if line and font.getlength(line + char) > width:
                    lines.append(line)
                    line = ''
                line += char
        lines.append(line)
    return lines


def make_cards(data, font):
    cards = []
    for index, name in enumerate(BUTTONS):
        if name not in data['buttons']:
            continue
        item = data['buttons'][name]
        lines = [(line, True) for line in wrap(item['title'], font, 410)] if item.get('title') else []
        for binding in item['bindings']:
            lines.extend((line, False) for line in wrap(binding, font, 410))
        width = math.ceil(max(font.getlength(line) for line, _ in lines)) + 36
        line_height = max(font.size + 10,
                          max(font.getbbox(line, anchor='lt')[3] for line, _ in lines) + 8)
        height = len(lines) * line_height + 28
        cards.append(dict(name=name, side=BUTTONS[name][0], lines=lines,
                          w=width, h=height, line_height=line_height, color=item.get('background', PALETTE[index])))
    return cards


def layout(cards, title_height):
    groups = {side: [c for c in cards if c['side'] == side] for side in ('top', 'left', 'right')}
    groups['top'].sort(key=lambda c: BUTTONS[c['name']][1][0])
    for side in ('left', 'right'):
        groups[side].sort(key=lambda c: {'left_fingertip': 460, 'bottom_thumb': 720}.get(c['name'], BUTTONS[c['name']][1][1]))
    gap, margin = 26, 32
    left = max((c['w'] for c in groups['left']), default=0)
    right = max((c['w'] for c in groups['right']), default=0)
    top = max((c['h'] for c in groups['top']), default=0)
    width = max(1036 + left + right + 4 * margin,
                sum(c['w'] + gap for c in groups['top']) - gap + 2 * margin)
    body_height = max(1218, *(sum(c['h'] + gap for c in groups[s]) + margin for s in ('left', 'right')))
    origin = ((width - 1036 - right + left) // 2, title_height + top + 2 * margin)
    height = origin[1] + body_height + margin
    x = (width - sum(c['w'] for c in groups['top']) - gap * max(0, len(groups['top']) - 1)) // 2
    for card in groups['top']:
        card['box'] = (x, title_height + margin + top - card['h'], x + card['w'], title_height + margin + top)
        x += card['w'] + gap
    for side in ('left', 'right'):
        group = groups[side]
        # Pack near the corresponding button; clamp the whole stack to the image.
        cursor = origin[1]
        for i, card in enumerate(group):
            remaining = sum(c['h'] + gap for c in group[i:]) - gap
            desired = origin[1] + BUTTONS[card['name']][1][1] - card['h'] // 2
            y = max(cursor, min(desired, height - margin - remaining))
            x = origin[0] - margin - card['w'] if side == 'left' else origin[0] + 1036 + margin
            card['box'] = (x, y, x + card['w'], y + card['h'])
            cursor = y + card['h'] + gap
    return (width, height), origin


def smooth_path(mask, path):
    """Replace grid stair steps with the longest collision-free straight segments."""
    def visible(a, b):
        distance = math.ceil(math.dist(a, b))
        for i in range(distance + 1):
            t = i / max(1, distance)
            point = tuple(round(x + (y-x)*t) for x,y in zip(a,b))
            if mask.getpixel(point):
                return False
        return True
    result = [path[0]]
    index = 0
    while index < len(path)-1:
        end = len(path)-1
        while end > index+1 and not visible(path[index],path[end]):
            end -= 1
        result.append(path[end])
        index = end
    return result


def route(mask, start, goal, step=6):
    """A* on an obstacle mask; forbid diagonal corner-cutting."""
    w, h = mask.size
    start, goal = tuple(round(v / step) for v in start), tuple(round(v / step) for v in goal)
    def free(p):
        x, y = p[0] * step, p[1] * step
        return 0 <= x < w and 0 <= y < h and mask.getpixel((x, y)) == 0
    queue = [(0, start)]
    costs, parents = {start: 0}, {}
    while queue:
        _, current = heapq.heappop(queue)
        if current == goal:
            path = [current]
            while current in parents:
                current = parents[current]
                path.append(current)
            return smooth_path(mask, [(x * step, y * step) for x, y in reversed(path)])
        for dx, dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1)):
            nxt = current[0]+dx, current[1]+dy
            if not free(nxt) or (dx and dy and (not free((current[0]+dx,current[1])) or not free((current[0],current[1]+dy)))):
                continue
            cost = costs[current] + (1.4142 if dx and dy else 1)
            if cost < costs.get(nxt, float('inf')):
                costs[nxt], parents[nxt] = cost, current
                heuristic = math.hypot(goal[0]-nxt[0], goal[1]-nxt[1])
                heapq.heappush(queue, (cost + heuristic, nxt))
    raise ValueError('Could not route a pointer without covering another button or annotation')


def render(data, destination, font_path=None):
    validate(data)
    font_size = data.get('font_size', 28)
    font = load_font(font_size, font_path)
    heading_font = load_font(font_size + 12, font_path)
    title_lines = wrap(data.get('title', ''), heading_font, 1000) if data.get('title') else []
    title_line_height = max(heading_font.size + 14,
                            max((heading_font.getbbox(line, anchor='lt')[3] for line in title_lines), default=0) + 8)
    title_height = len(title_lines) * title_line_height + (24 if title_lines else 0)
    cards = make_cards(data, font)
    size, (ox, oy) = layout(cards, title_height)
    canvas = Image.new('RGB', size, BACKGROUND)
    with Image.open(ROOT / 'assets/z3-base.png') as base:
        canvas.paste(base.convert('RGB'), (ox, oy))
    draw = ImageDraw.Draw(canvas)
    occupied = Image.new('L', size)
    occupied_draw = ImageDraw.Draw(occupied)
    for card in cards:
        x1,y1,x2,y2 = card['box']
        occupied_draw.rectangle((x1-10,y1-10,x2+10,y2+10), fill=255)
    paths = []
    for card in cards:
        mask = occupied.copy()
        obstacles = ImageDraw.Draw(mask)
        for name, (_, _, polygon) in BUTTONS.items():
            if name != card['name']:
                points = [(x+ox,y+oy) for x,y in polygon]
                obstacles.polygon(points, fill=255)
                obstacles.line(points + [points[0]], fill=255, width=16)
        x1,y1,x2,y2 = card['box']
        if card['side'] == 'top':
            port, start = ((x1+x2)//2,y2), ((x1+x2)//2,y2+18)
        elif card['side'] == 'left':
            port, start = (x2,(y1+y2)//2), (x2+18,(y1+y2)//2)
        else:
            port, start = (x1,(y1+y2)//2), (x1-18,(y1+y2)//2)
        target = tuple(a+b for a,b in zip(BUTTONS[card['name']][1], (ox,oy)))
        path = [port, *route(mask, start, target), target]
        paths.append((path, card['color']))
        # Reserve each pointer so subsequent pointers cannot overlap it.
        occupied_draw.line(path, fill=255, width=12)
    for path, color in paths:
        draw.line(path, fill=BACKGROUND, width=8, joint='curve')
        draw.line(path, fill=color, width=4, joint='curve')
        x,y = path[-1]
        draw.ellipse((x-5,y-5,x+5,y+5), fill=color, outline=BACKGROUND, width=1)
    for card in cards:
        draw.rounded_rectangle(card['box'], radius=12, fill=card['color'])
        x,y = card['box'][:2]
        ink = '#ffffff'
        outline = '#000000'
        line_height = card['line_height']
        for line, underline in card['lines']:
            draw.text((x+18,y+12), line, font=font, fill=ink, anchor='lt',
                      stroke_width=1, stroke_fill=outline)
            if underline and line:
                draw.line((x+18,y+12+line_height-6,x+18+font.getlength(line),y+12+line_height-6), fill=outline, width=4)
                draw.line((x+18,y+12+line_height-6,x+18+font.getlength(line),y+12+line_height-6), fill=ink, width=2)
            y += line_height
    for index, line in enumerate(title_lines):
        draw.text((size[0]//2, 20+index*title_line_height), line, font=heading_font, fill='#ffffff', anchor='mt',
                  stroke_width=1, stroke_fill='#000000')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination)
    return size


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=ROOT, help='default: directory containing render.py')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'output')
    parser.add_argument('--font', type=Path, help='Unicode TrueType/OpenType font')
    args = parser.parse_args(argv)
    if not args.input_dir.is_dir():
        parser.error(f'Input directory does not exist: {args.input_dir}')
    files = sorted(p for p in args.input_dir.iterdir() if p.is_file() and p.suffix.lower() == '.json')
    processed = failed = 0
    for source in files:
        try:
            data = validate(json.loads(source.read_text(encoding='utf-8-sig')))
            destination = args.output_dir / (source.stem + '.png')
            width, height = render(data, destination, args.font)
            print(f'Processed {source.name} -> {destination} ({width} × {height})')
            processed += 1
        except (ValueError, OSError) as error:
            print(f'Skipped {source.name}: {error}', file=sys.stderr)
            failed += 1
    print(f'{processed} processed; {failed} skipped; {len(files)} JSON file(s) found.')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
