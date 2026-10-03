"""The public search page's JS tokenizer must match textnorm.index_tokens (shard dictionary lookups depend on it)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from textnorm import bucket_of, fold, index_tokens  # noqa: E402

ENGINE = Path.home() / "Projects" / "tsm-strait-layers" / "tools" / "rhetoric-search" / "js" / "engine.js"
SAMPLES = ["美国NNSA近日 W87-1 Türkiye", "Ёлка зелёная, ПЛУТОНИЕВЫЕ сердечники!", "هسته پلوتونيوم ۱۲۳ لس آلاموس",
           "İstanbul ß 123456", "한국어 테스트 钚弹芯", "«питы» Лос-Аламосская лаборатория"]


@pytest.mark.skipif(not shutil.which("node") or not ENGINE.exists(), reason="node or engine.js not available")
def test_js_python_tokenizer_parity():
    js = ("import { tokens, bucketOf, fold } from " + json.dumps(ENGINE.as_uri()) + ";"
          "const S = JSON.parse(process.argv[1]);"
          "console.log(JSON.stringify(S.map(s => { const t = [...tokens(s)].sort(); return [t, t.map(x => bucketOf(x, 512)), fold(s)]; })));")
    out = subprocess.run(["node", "--input-type=module", "-e", js, json.dumps(SAMPLES)], capture_output=True, text=True, check=True)
    for s, (jt, jb, jf) in zip(SAMPLES, json.loads(out.stdout)):
        pt = sorted(index_tokens(s))
        assert pt == sorted(jt), s
        assert [bucket_of(t, 512) for t in sorted(jt)] == jb, s
        assert fold(s) == jf, s
