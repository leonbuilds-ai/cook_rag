"""Resolve only existing recipe images; never serve arbitrary filesystem paths."""
import re
from pathlib import Path
from urllib.parse import unquote, urlparse, quote
from rag_modules.sources import canonical_source

EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


class RecipeMedia:
    def __init__(self, data_root, generated_root=None):
        self.root = Path(data_root).resolve()

    def valid_image(self, path):
        if not path.is_relative_to(self.root) or path.suffix.lower() not in EXTENSIONS or not path.is_file():
            return False
        with path.open('rb') as image:
            header = image.read(12)
        # Empty downloads and Git LFS pointers are not displayable images.
        return (header.startswith(b'\xff\xd8\xff') or header.startswith(b'\x89PNG\r\n\x1a\n')
                or header.startswith((b'GIF87a', b'GIF89a'))
                or (header.startswith(b'RIFF') and header[8:12] == b'WEBP'))

    def file_for(self, source):
        relative = canonical_source(source).removeprefix('data/cook/')
        path = (self.root / relative).resolve()
        return path if path.is_relative_to(self.root) and path.suffix == '.md' and path.is_file() else None

    def local_image(self, base, value):
        value = unquote(value.strip().strip('<>')).split(' "', 1)[0]
        if urlparse(value).scheme or value.startswith('//'):
            return None
        path = (base / value).resolve()
        return path if self.valid_image(path) else None

    def image_url(self, path):
        return '/recipe-media/' + quote(path.relative_to(self.root).as_posix(), safe='/')

    def describe(self, source):
        recipe = self.file_for(source)
        if recipe:
            body = recipe.read_text(encoding='utf-8')
            for value in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', body):
                path = self.local_image(recipe.parent, value)
                if path:
                    return {'url': self.image_url(path), 'kind': 'original', 'label': '食谱原图'}
            # Prefer the recipe name; a folder may contain multiple recipes.
            candidates = sorted(p for p in recipe.parent.iterdir() if self.valid_image(p))
            same_name = [p for p in candidates if p.stem == recipe.stem]
            if same_name or (len(list(recipe.parent.glob('*.md'))) == 1 and candidates):
                return {'url': self.image_url((same_name or candidates)[0]), 'kind': 'original', 'label': '食谱原图'}
        return {'url': None, 'kind': 'missing', 'label': '暂无配图'}

    def rewrite(self, source, content):
        recipe = self.file_for(source)
        if not recipe:
            return content
        def substitute(match):
            path = self.local_image(recipe.parent, match[2])
            return f'![{match[1]}]({self.image_url(path)})' if path else f'【图片：{match[1] or "原文图片"}，原文件不可用】'
        return re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', substitute, content)

    def public_file(self, relative):
        path = (self.root / relative).resolve()
        return path if self.valid_image(path) else None
