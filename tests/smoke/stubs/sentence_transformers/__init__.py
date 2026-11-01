"""Заглушка sentence_transformers для дымового теста без torch.
Эмбеддинг = хеш-мешок символьных триграмм, поэтому похожие строки дают высокое косинусное сходство."""
import numpy as np
DIM = 512
def _vec(t):
    t = f"  {str(t).lower()}  "
    v = np.zeros(DIM, dtype=np.float32)
    for i in range(len(t) - 2):
        v[hash(t[i:i+3]) % DIM] += 1
    n = np.linalg.norm(v)
    return v / n if n else v
class SentenceTransformer:
    def __init__(self, name, *a, **k):
        self.name = name
    def encode(self, texts, convert_to_tensor=False, **k):
        if isinstance(texts, str):
            return _vec(texts)
        return np.stack([_vec(t) for t in texts])
class util:
    @staticmethod
    def cos_sim(a, b):
        a = np.atleast_2d(a); b = np.atleast_2d(b)
        return a @ b.T
