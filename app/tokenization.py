# 使用固定版本的 BGE tokenizer，只分词，不在本地加载模型权重。
# 切分时保持原始文本，不用 decode 重建，避免大小写、空白或 UNK 造成内容丢失。
from functools import lru_cache
from pathlib import Path
from tokenizers import Tokenizer


@lru_cache
def tokenizer():
    return Tokenizer.from_file(str(Path(__file__).parent / 'assets' / 'tokenizer.json'))


def token_count(text):
    # 包括 CLS/SEP，预算与模型实际输入一致。
    return len(tokenizer().encode(text).ids)


def text_chunks(text, prefix='', limit=480):
    """按段落/句末优先切分；遇超长段落，用二分查找保证每片 token 不超限。"""
    if token_count(prefix) >= limit - 16:
        raise ValueError('分片上下文过长')
    position = 0
    while position < len(text):
        window = text[position:position + 4096]
        lo, hi = 1, len(window)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if token_count(prefix + window[:mid]) <= limit:
                lo = mid
            else:
                hi = mid - 1
        end = lo
        if position + end < len(text):
            # 尽量不在一句话中间切断，但不让短段落产生大量碎片。
            for separator in ('\n\n', '\n', '。', '；', '. '):
                boundary = window.rfind(separator, end // 2, end)
                if boundary >= 0:
                    end = boundary + len(separator)
                    break
        part = text[position:position + end]
        if token_count(prefix + part) > limit:
            raise ValueError('单个字符超出分片预算')
        yield part
        position += end
