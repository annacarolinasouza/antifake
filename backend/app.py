"""
════════════════════════════════════════════════════════════════════
 ANTIFAKE 2.0 — checagem de fatos 100% GRATUITA
════════════════════════════════════════════════════════════════════
 Funciona SEM nenhuma chave de API paga:
   • Texto / link  → extração com trafilatura + análise do domínio (RDAP)
   • Imagem        → OCR local (Tesseract por+eng) + metadados EXIF
                      + links de busca reversa (Google Lens, Yandex, TinEye, Bing)
   • Vídeo         → ffmpeg (áudio + quadros) + transcrição local (faster-whisper)
                      + OCR dos quadros
   • Evidências    → Google Notícias (RSS) filtrado em agências de checagem
                      brasileiras + DuckDuckGo + Bing (RSS) — tudo gratuito
   • Veredito      → motor de regras transparente (sinais + checagens)

 Opcionais (também gratuitos), apenas melhoram o resultado:
   GOOGLE_FACTCHECK_KEY → Google Fact Check Tools API (grátis)
   GEMINI_API_KEY       → Gemini (camada gratuita do Google AI Studio)
   GROQ_API_KEY         → Groq (camada gratuita, Llama 3)
   OLLAMA_URL           → LLM local (ex.: http://localhost:11434)

 Rotas:
   GET  /                    → interface web
   POST /api/check           → inicia verificação (form: text e/ou file)
   GET  /api/check/{job}     → progresso ao vivo + resultado
   GET  /api/analysis/{id}   → análise salva
   GET  /api/history         → últimas verificações
   GET  /api/stats           → contagem por veredito
   GET  /api/health          → status e recursos disponíveis
   GET  /v/{id}              → página pública do card (compartilhável)
"""
import asyncio
import hashlib
import html
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import feedparser
import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from collections import deque, OrderedDict

# ──────────────────────────────────────────────────────────────────
# Configuração
# ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
FRONTEND = BASE_DIR.parent / "frontend" / "index.html"
DATA_DIR = Path(os.getenv("ANTIFAKE_DATA", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR = Path(tempfile.gettempdir()) / "antifake"
TMP_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "antifake.db"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
RATE_CHECKS = int(os.getenv("RATE_CHECKS", "15"))      # verificações por IP a cada 10 min

GOOGLE_FACTCHECK_KEY = os.getenv("GOOGLE_FACTCHECK_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OLLAMA_URL = os.getenv("OLLAMA_URL", "")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "80"))
VIDEO_MAX_SECONDS = int(os.getenv("VIDEO_MAX_SECONDS", "180"))

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6"}

VERDICTS = ["FALSO", "ENGANOSO", "VERDADEIRO", "SEM EVIDÊNCIA"]

# Agências de checagem (IFCN / referência no Brasil e no mundo)
FACTCHECKERS = {
    "lupa.uol.com.br": "Agência Lupa", "lupa.news": "Agência Lupa",
    "aosfatos.org": "Aos Fatos", "boatos.org": "Boatos.org",
    "checamos.afp.com": "AFP Checamos", "projetocomprova.com.br": "Projeto Comprova",
    "e-farsas.com": "E-farsas", "g1.globo.com/fato-ou-fake": "Fato ou Fake (g1)",
    "estadao.com.br/estadao-verifica": "Estadão Verifica",
    "noticias.uol.com.br/confere": "UOL Confere", "uol.com.br/confere": "UOL Confere",
    "folhadelondrina.com.br/confere": "Folha de Londrina Confere",
    "reuters.com/fact-check": "Reuters Fact Check", "snopes.com": "Snopes",
    "politifact.com": "PolitiFact", "factcheck.org": "FactCheck.org",
    "fullfact.org": "Full Fact", "maldita.es": "Maldita", "newtral.es": "Newtral",
    "chequeado.com": "Chequeado", "poligrafo.sapo.pt": "Polígrafo",
    "gov.br/saude/pt-br/fato-ou-fake": "Saúde sem Fake News",
}
FC_GNEWS_SITES = ["lupa.uol.com.br", "aosfatos.org", "boatos.org", "checamos.afp.com",
                  "projetocomprova.com.br", "e-farsas.com", "g1.globo.com/fato-ou-fake",
                  "estadao.com.br/estadao-verifica", "noticias.uol.com.br/confere"]
RELIABLE = ["g1.globo.com", "globo.com", "folha.uol.com.br", "uol.com.br", "estadao.com.br",
            "cnnbrasil.com.br", "bbc.com", "bbc.co.uk", "agenciabrasil.ebc.com.br",
            "gov.br", "oglobo.globo.com", "valor.globo.com", "exame.com", "veja.abril.com.br",
            "terra.com.br", "r7.com", "band.uol.com.br", "metropoles.com", "poder360.com.br",
            "correiobraziliense.com.br", "nexojornal.com.br", "piaui.folha.uol.com.br",
            "reuters.com", "apnews.com", "nytimes.com", "theguardian.com", "elpais.com",
            "dw.com", "wikipedia.org", "who.int", "paho.org", "bcb.gov.br", "tse.jus.br",
            "stf.jus.br", "camara.leg.br", "senado.leg.br", "ibge.gov.br", "fiocruz.br",
            "butantan.gov.br", "infomoney.com.br", "jovempan.com.br", "sbtnews.sbt.com.br"]
SATIRE = ["sensacionalista.com.br", "theonion.com", "babylonbee.com", "joselitomuller",
          "revistapiaui.estadao", "diariopernambucano.com.br/humor", "piaui.folha.uol.com.br/herald"]

SENSATIONAL = ["urgente", "compartilhe", "compartilhem", "antes que apaguem", "antes que tirem",
               "repasse", "repassem", "divulgue", "a midia nao mostra", "a globo nao mostra",
               "ninguem esta falando", "bomba", "chocante", "exclusivo", "vazou", "vazado",
               "censurado", "censuraram", "acordem", "acorda brasil", "nao vao mostrar",
               "imprensa esconde", "verdade escondida", "cura milagrosa", "100% comprovado",
               "medicos odeiam", "nao deixe de ver", "envie para todos", "manda pra todo mundo",
               "atencao", "alerta", "inacreditavel", "absurdo", "escandalo"]

STOP = set("""a o e é de do da dos das em no na nos nas um uma uns umas por para pra com sem
sob que se ao aos à às ou mas como mais menos muito muita já não sim ser são foi era será
seu sua seus suas meu minha ele ela eles elas isso isto esse essa este esta aquele aquela
lhe lhes nós vocês você eu tu te me nosso nossa até após entre sobre também só quando onde
porque pois então vai vão ter tem têm tinha está estão estava há qual quais quem todo toda
todos todas cada outro outra outros outras mesmo mesma depois antes agora ainda hoje ontem
amanhã the of and to in is for on that with by from at an be this are was it as or
diz dizem disse segundo após contra pelo pela pelos pelas sendo pode podem vem vêm aqui
passar passa passará partir mês meses semana ano anos dia dias próximo próxima novo nova
vai vão irá começar começa todas todos toda gente pessoa pessoas coisa coisas sabia veja
verdade verdadeira esconde escondem escondendo mentira real realmente mundo
""".split())

# sinônimos frequentes em boatos (agrupa radicais para medir relevância)
SINON = {"cobra": "taxa", "taxar": "taxa", "taxad": "taxa", "taxaç": "taxa", "impos": "taxa", "tarif": "taxa",
         "tribu": "taxa", "morre": "morte", "morte": "morte", "morto": "morte", "falec": "morte",
         "morri": "morte", "vacin": "vacin", "imuni": "vacin", "chip": "chip", "nanoc": "chip",
         "micro": "chip", "presi": "presi", "gover": "gover", "proib": "proib", "banid": "proib",
         "veta": "proib", "vetad": "proib", "multa": "multa", "prend": "prisa", "preso": "prisa",
         "prisa": "prisa", "eleiç": "eleic", "urna": "urna", "urnas": "urna", "fraud": "fraud"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def keywords(text: str, limit: int = 10) -> list:
    words = re.findall(r"[\wÀ-ÿ%$]+", text or "")
    out, seen = [], set()
    for w in words:
        lw = w.lower()
        if lw in STOP or len(lw) < 3 and not any(ch.isdigit() for ch in lw):
            continue
        k = norm(lw)
        if k in seen:
            continue
        seen.add(k)
        out.append(w)
    # prioriza números, nomes próprios e palavras longas
    out.sort(key=lambda w: -(3 * any(ch.isdigit() for ch in w) + 2 * w[:1].isupper()
                             + min(len(w), 10) / 10))
    return out[:limit]


def stem(w: str) -> str:
    w = norm(w)
    s = w[:5] if len(w) > 5 else w
    return SINON.get(s, SINON.get(w[:4], s))


def relevance(claim_kws: list, text: str) -> float:
    if not claim_kws:
        return 0.0
    tstems = {stem(w) for w in re.findall(r"[\wÀ-ÿ%$]+", text or "")}
    # palavras mais características (início da lista ordenada) pesam mais
    # números (valores, %, datas) variam entre versões do mesmo boato: pesam menos
    palavras = [k for k in claim_kws if not any(ch.isdigit() for ch in k)]
    pesos = [0.6 if any(ch.isdigit() for ch in k) else (1.6 if k in palavras[:2] else 1.0) for k in claim_kws]
    hit = sum(p for k, p in zip(claim_kws, pesos) if stem(k) in tstems)
    return min(1.0, hit / sum(pesos))


def domain_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().removeprefix("www.")
    except Exception:
        return ""


def match_list(url: str, lst) -> str | None:
    u = (url or "").lower().replace("https://", "").replace("http://", "").removeprefix("www.")
    for d in lst:
        if u.startswith(d) or ("." + d) in u.split("/")[0] or u.split("/")[0] == d.split("/")[0] and "/" not in d:
            return d
    return None


def factchecker_name(url: str, source_href: str = "") -> str | None:
    for cand in (url, source_href):
        d = match_list(cand, FACTCHECKERS.keys())
        if d:
            return FACTCHECKERS[d]
    return None


def is_reliable(url: str) -> bool:
    return bool(match_list(url, RELIABLE))


# ──────────────────────────────────────────────────────────────────
# Persistência: PostgreSQL (DATABASE_URL, ex.: Neon grátis) ou SQLite local
# ──────────────────────────────────────────────────────────────────
_POOL = None


def _pg_pool():
    global _POOL
    if _POOL is None:
        from psycopg_pool import ConnectionPool

        _POOL = ConnectionPool(DATABASE_URL, min_size=1, max_size=4, timeout=20,
                               check=ConnectionPool.check_connection, open=True,
                               kwargs={"autocommit": True, "prepare_threshold": None})
    return _POOL


def sql(query: str, params: tuple = (), fetch: str | None = None):
    """Executa SQL nos dois bancos. Use '?' como marcador. fetch: None | 'one' | 'all'."""
    if USE_PG:
        from psycopg.rows import dict_row

        q = query.replace("?", "%s")
        with _pg_pool().connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(q, params)
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
                return None
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(query, params)
        if fetch == "one":
            r = cur.fetchone()
            return dict(r) if r else None
        if fetch == "all":
            return [dict(r) for r in cur.fetchall()]
        conn.commit()
        return None
    finally:
        conn.close()


def init_db():
    blob = "BYTEA" if USE_PG else "BLOB"
    sql("""CREATE TABLE IF NOT EXISTS analyses(
        id TEXT PRIMARY KEY, created_at TEXT, input_type TEXT, input_ref TEXT,
        input_hash TEXT, alegacao TEXT, veredito TEXT, confianca INTEGER, data TEXT)""")
    for col, typ in (("publico", "INTEGER DEFAULT 1"), ("busca", "TEXT DEFAULT ''")):
        try:
            sql(f"ALTER TABLE analyses ADD COLUMN {col} {typ}")
        except Exception:
            pass
    sql(f"""CREATE TABLE IF NOT EXISTS media(
        name TEXT PRIMARY KEY, analysis_id TEXT, mime TEXT, content {blob}, created_at TEXT)""")
    sql("""CREATE TABLE IF NOT EXISTS feedback(
        id TEXT PRIMARY KEY, analysis_id TEXT, created_at TEXT, tipo TEXT, comentario TEXT, ip_hash TEXT)""")
    for ix in ("CREATE INDEX IF NOT EXISTS ix_hash ON analyses(input_hash)",
               "CREATE INDEX IF NOT EXISTS ix_created ON analyses(created_at)",
               "CREATE INDEX IF NOT EXISTS ix_veredito ON analyses(veredito)",
               "CREATE INDEX IF NOT EXISTS ix_fb ON feedback(analysis_id)",
               "CREATE INDEX IF NOT EXISTS ix_media ON media(analysis_id)"):
        sql(ix)


def texto_busca(rec: dict) -> str:
    m = rec.get("midia") or {}
    partes = [rec.get("alegacao", ""), rec.get("input_ref", ""), (rec.get("pagina") or {}).get("titulo") or "",
              (m.get("ocr") or "")[:600], (m.get("transcricao") or "")[:800], (m.get("ocr_quadros") or "")[:400]]
    return " " + re.sub(r"\s+", " ", norm(" ".join(p for p in partes if p))) + " "


def save(rec: dict):
    params = (rec["id"], rec["created_at"], rec["input_type"], rec["input_ref"][:500], rec["input_hash"],
              rec["alegacao"], rec["veredito"], rec["confianca"], json.dumps(rec, ensure_ascii=False),
              1 if rec.get("publico", True) else 0, texto_busca(rec))
    sql("""INSERT INTO analyses(id, created_at, input_type, input_ref, input_hash, alegacao, veredito,
           confianca, data, publico, busca) VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (id) DO UPDATE SET data = excluded.data, veredito = excluded.veredito,
           confianca = excluded.confianca, publico = excluded.publico, busca = excluded.busca""", params)


def load(aid: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{6,32}", aid or ""):
        raise HTTPException(404, "Análise não encontrada")
    row = sql("SELECT data FROM analyses WHERE id=?", (aid,), "one")
    if not row:
        raise HTTPException(404, "Análise não encontrada")
    rec = json.loads(row["data"])
    rec["feedback"] = feedback_counts(aid)
    return rec


def cached(input_hash: str, max_age_h: int = 24) -> dict | None:
    row = sql("SELECT data, created_at FROM analyses WHERE input_hash=? ORDER BY created_at DESC LIMIT 1",
              (input_hash,), "one")
    if not row:
        return None
    age = datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])
    return json.loads(row["data"]) if age.total_seconds() < max_age_h * 3600 else None


def media_put(name: str, aid: str, mime: str, content: bytes):
    sql("""INSERT INTO media(name, analysis_id, mime, content, created_at) VALUES (?,?,?,?,?)
           ON CONFLICT (name) DO NOTHING""", (name, aid, mime, content, now_iso()))


def media_get(name: str):
    return sql("SELECT mime, content FROM media WHERE name=?", (name,), "one")


def feedback_counts(aid: str) -> dict:
    rows = sql("SELECT tipo, COUNT(*) AS n FROM feedback WHERE analysis_id=? GROUP BY tipo", (aid,), "all") or []
    d = {"util": 0, "errado": 0}
    d.update({r["tipo"]: int(r["n"]) for r in rows})
    return d


def thumb_jpeg(img, max_side: int = 1280, quality: int = 80) -> bytes:
    from PIL import Image

    im = img.convert("RGB")
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


init_db()

# ──────────────────────────────────────────────────────────────────
# Limite de uso por IP (anti-abuso) — em memória
# ──────────────────────────────────────────────────────────────────
_RATE: dict = {}


def client_ip(req: Request) -> str:
    xff = req.headers.get("x-forwarded-for", "")
    return (xff.split(",")[0].strip() if xff else (req.client.host if req.client else "?")) or "?"


def rate_ok(key: str, limit: int, window_s: int) -> bool:
    now = time.time()
    dq = _RATE.setdefault(key, deque())
    while dq and dq[0] < now - window_s:
        dq.popleft()
    if len(dq) >= limit:
        return False
    dq.append(now)
    if len(_RATE) > 20000:
        _RATE.clear()
    return True


# ──────────────────────────────────────────────────────────────────
# Jobs com progresso ao vivo
# ──────────────────────────────────────────────────────────────────
JOBS: dict = {}


class Job:
    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        self.status = "running"
        self.steps: list = []
        self.result = None
        self.error = None
        self.created = time.time()
        JOBS[self.id] = self

    def step(self, key: str, label: str, state: str = "run", detail: str = ""):
        for s in self.steps:
            if s["key"] == key:
                s.update(state=state, label=label, detail=detail or s.get("detail", ""))
                return
        self.steps.append({"key": key, "label": label, "state": state, "detail": detail})

    def done(self, key: str, detail: str = "", state: str = "ok"):
        for s in self.steps:
            if s["key"] == key:
                s["state"] = state
                if detail:
                    s["detail"] = detail

    def public(self):
        return {"id": self.id, "status": self.status, "steps": self.steps,
                "result": self.result, "error": self.error}


def gc_jobs():
    lim = time.time() - 3600
    for k in [k for k, j in JOBS.items() if j.created < lim]:
        JOBS.pop(k, None)


# ──────────────────────────────────────────────────────────────────
# 1) EXTRAÇÃO
# ──────────────────────────────────────────────────────────────────
async def extrair_url(url: str) -> dict:
    import trafilatura

    async with httpx.AsyncClient(timeout=20, headers=UA, follow_redirects=True) as c:
        r = await c.get(url)
    final_url = str(r.url)
    if r.status_code >= 400:
        raise RuntimeError(f"a página respondeu HTTP {r.status_code}")
    raw = r.text

    def _parse():
        texto = trafilatura.extract(raw, url=final_url, include_comments=False) or ""
        meta = trafilatura.extract_metadata(raw, default_url=final_url)
        titulo = (getattr(meta, "title", None) or "") if meta else ""
        data = (getattr(meta, "date", None) or "") if meta else ""
        autor = (getattr(meta, "author", None) or "") if meta else ""
        site = (getattr(meta, "sitename", None) or "") if meta else ""
        if not titulo:
            m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
            titulo = html.unescape(m.group(1).strip()) if m else ""
        og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', raw, re.I)
        return {"titulo": titulo, "texto": texto, "data": data, "autor": autor,
                "site": site, "url_final": final_url, "imagem": og.group(1) if og else ""}

    return await asyncio.to_thread(_parse)


async def analisar_dominio(url: str) -> dict:
    dom = domain_of(url)
    info = {"dominio": dom, "https": url.startswith("https://"),
            "checador": factchecker_name(url), "confiavel": is_reliable(url),
            "satira": bool(match_list(url, SATIRE)), "idade_dias": None, "registro": None}
    parts = dom.split(".")
    base = ".".join(parts[-3:]) if dom.endswith(".br") and len(parts) >= 3 else ".".join(parts[-2:])
    rdap = (f"https://rdap.registro.br/domain/{base}" if base.endswith(".br")
            else f"https://rdap.org/domain/{base}")
    try:
        async with httpx.AsyncClient(timeout=10, headers=UA, follow_redirects=True) as c:
            r = await c.get(rdap)
        if r.status_code == 200:
            for ev in r.json().get("events", []):
                if ev.get("eventAction") == "registration":
                    d = datetime.fromisoformat(ev["eventDate"].replace("Z", "+00:00"))
                    info["registro"] = d.date().isoformat()
                    info["idade_dias"] = (datetime.now(timezone.utc) - d).days
    except Exception:
        pass
    return info


def _ocr(img) -> str:
    import pytesseract
    from PIL import ImageOps

    g = ImageOps.grayscale(img)
    if max(g.size) < 1400:
        f = 1400 / max(g.size)
        g = g.resize((int(g.width * f), int(g.height * f)))
    langs = "por+eng"
    try:
        txt = pytesseract.image_to_string(g, lang=langs)
    except Exception:
        txt = pytesseract.image_to_string(g)
    lines = [ln.strip() for ln in txt.splitlines()]
    lines = [ln for ln in lines if len(re.findall(r"[A-Za-zÀ-ÿ]", ln)) >= 3]
    return "\n".join(lines).strip()


def analisar_imagem(path: Path) -> dict:
    from PIL import Image, ExifTags

    try:
        import pillow_heif  # noqa
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    img = Image.open(path)
    img.load()
    exif = {}
    try:
        raw = img.getexif()
        for k, v in raw.items():
            name = ExifTags.TAGS.get(k, str(k))
            if name in ("Make", "Model", "Software", "DateTime", "DateTimeOriginal", "Artist"):
                exif[name] = str(v)[:80]
        sub = raw.get_ifd(0x8769) if hasattr(raw, "get_ifd") else {}
        for k, v in (sub or {}).items():
            name = ExifTags.TAGS.get(k, str(k))
            if name in ("DateTimeOriginal",):
                exif[name] = str(v)
    except Exception:
        pass
    texto = _ocr(img.convert("RGB"))
    return {"largura": img.width, "altura": img.height, "formato": img.format or "",
            "exif": exif, "ocr": texto, "_thumb": thumb_jpeg(img)}


_WHISPER = None


def transcrever(audio_path: Path) -> dict:
    global _WHISPER
    from faster_whisper import WhisperModel

    if _WHISPER is None:
        _WHISPER = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    import numpy as np
    import wave

    with wave.open(str(audio_path), "rb") as w:  # PCM 16 kHz mono gerado pelo ffmpeg
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    audio = pcm.astype(np.float32) / 32768.0
    segs, info = _WHISPER.transcribe(
        audio, beam_size=3, vad_filter=True, language=os.getenv("WHISPER_LANG", "pt") or None,
        initial_prompt=("Atenção! Mensagem do WhatsApp sobre Pix, Receita Federal, Banco Central, INSS, "
                        "governo Lula, Bolsonaro, STF, TSE, vacina, eleições, imposto e notícias do Brasil."))
    texto = " ".join(s.text.strip() for s in segs).strip()
    return {"texto": texto, "idioma": info.language}


def processar_video(path: Path, aid: str) -> dict:
    out = {"transcricao": "", "idioma": "", "duracao": None, "quadros": [], "ocr_quadros": ""}
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True, timeout=30)
        out["duracao"] = round(float(p.stdout.strip()), 1)
    except Exception:
        pass
    dur = out["duracao"] or 30
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-t", str(VIDEO_MAX_SECONDS),
                        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], timeout=180)
        if wav.exists() and wav.stat().st_size > 2000:
            try:
                t = transcrever(wav)
                out["transcricao"], out["idioma"] = t["texto"], t["idioma"]
            except Exception as e:
                out["erro_transcricao"] = str(e)[:200]
    # 4 quadros distribuídos
    from PIL import Image

    ocrs = []
    for i, frac in enumerate((0.1, 0.35, 0.6, 0.85)):
        name = f"{aid}_q{i}.jpg"
        dest = TMP_DIR / name
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{dur * frac:.2f}", "-i", str(path),
                        "-frames:v", "1", "-vf", "scale='min(960,iw)':-2", "-q:v", "3", str(dest)], timeout=60)
        if dest.exists():
            try:
                fr = Image.open(dest).convert("RGB")
                media_put(name, aid, "image/jpeg", thumb_jpeg(fr, 720, 72))
                out["quadros"].append(name)
                t = _ocr(fr)
                if t:
                    ocrs.append(t)
            except Exception:
                pass
            finally:
                dest.unlink(missing_ok=True)
    # remove linhas repetidas entre quadros
    seen, lines = set(), []
    for t in ocrs:
        for ln in t.splitlines():
            k = norm(ln)
            if k not in seen:
                seen.add(k)
                lines.append(ln)
    out["ocr_quadros"] = "\n".join(lines)
    return out


# ──────────────────────────────────────────────────────────────────
# 2) ALEGAÇÃO + SINAIS
# ──────────────────────────────────────────────────────────────────
def sinais_texto(texto: str) -> dict:
    t = norm(texto)
    achados = sorted({w for w in SENSATIONAL if re.search(r"\b" + re.escape(w) + r"\b", t)})
    letras = re.findall(r"[A-Za-zÀ-ÿ]", texto or "")
    caps = sum(1 for c in letras if c.isupper()) / max(1, len(letras))
    excl = (texto or "").count("!")
    emojis = len(re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", texto or ""))
    score = min(100, len(achados) * 14 + (25 if caps > 0.35 and len(letras) > 30 else 0)
                + min(20, excl * 4) + min(10, emojis * 2))
    return {"termos": achados, "caixa_alta": round(caps * 100), "exclamacoes": excl,
            "emojis": emojis, "score": score}


CTA = re.compile(r"(urgente|aten[cç][aã]o|alerta|compartilh\w*|repass\w*|divulg\w*|envie para todos|"
                 r"manda pra todo mundo|antes que (apaguem|tirem|deletem)|n[aã]o deixe de ver|acorda(m)? brasil|"
                 r",?\s*(e |mas )?(a )?(imprensa|m[ií]dia|globo|tv) (esconde|n[aã]o mostra|n[aã]o fala|est[aá] escondendo)\w*|"
                 r"ningu[eé]m est[aá] falando( disso)?|vazou|bomba)"
                 r"[\s!.,:;]*", re.I)


def heuristica_alegacao(titulo: str, texto: str) -> str:
    texto = CTA.sub(" ", texto or "")
    texto = re.sub(r"(?m)^\W*$", "", texto)
    titulo = (titulo or "").strip()
    texto = re.sub(r"\s+", " ", texto or "").strip()
    if titulo and len(titulo) > 25 and len(titulo.split()) >= 5:
        # remove " - Site" / " | Site" do fim
        return re.split(r"\s[\-|–|—]\s(?=[^\-|–—]{2,40}$)", titulo)[0].strip()
    sents = re.split(r"(?<=[.!?])\s+|\n+", texto)
    best, best_s = "", -1.0
    for i, s in enumerate(sents[:40]):
        s = s.strip(" \"'“”*-•")
        n = len(s)
        if n < 25 or n > 320:
            continue
        sc = 0.0
        sc += 2 if re.search(r"\d", s) else 0
        sc += 1.5 * len(re.findall(r"\b[A-ZÁÉÍÓÚÂÊÔÃÕ][a-zà-ÿ]{2,}", s[1:]))
        sc += 2 if re.search(r"\b(vai|vão|será|serão|anunci|aprov|proib|obrig|cobra|morreu|"
                             r"comprova|descobr|causa|cura|confirm|decret|lei|governo|stf|tse)", norm(s)) else 0
        sc -= i * 0.15
        sc += 1 if 60 <= n <= 200 else 0
        if sc > best_s:
            best, best_s = s, sc
    if not best:
        best = (titulo or texto)[:240]
    return best.strip()


def query_de(alegacao: str) -> str:
    return " ".join(keywords(alegacao, 8))


# ──────────────────────────────────────────────────────────────────
# 3) BUSCA DE EVIDÊNCIAS (gratuita)
# ──────────────────────────────────────────────────────────────────
async def buscar_gnews(client, q: str, checadores: bool) -> list:
    query = q
    if checadores:
        query += " (" + " OR ".join(f"site:{s}" for s in FC_GNEWS_SITES) + ")"
    try:
        r = await client.get("https://news.google.com/rss/search",
                             params={"q": query, "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"})
        d = feedparser.parse(r.text)
    except Exception:
        return []
    out = []
    for e in d.entries[:15]:
        src = e.get("source", {}) or {}
        titulo = re.sub(r"\s-\s[^-]+$", "", e.get("title", "")).strip()
        out.append({"titulo": titulo, "url": e.get("link", ""), "trecho": "",
                    "veiculo": src.get("title", "") or "", "dominio": domain_of(src.get("href", "")),
                    "data": e.get("published", ""), "origem": "Google Notícias",
                    "src_href": src.get("href", "")})
    return out


async def buscar_ddg(client, q: str) -> list:
    try:
        r = await client.post("https://html.duckduckgo.com/html/", data={"q": q, "kl": "br-pt"})
    except Exception:
        return []
    out = []
    blocks = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)'
                        r'(?=<a[^>]+class="result__a"|$)', r.text, re.S)
    for href, tit, rest in blocks[:10]:
        m = re.search(r"uddg=([^&]+)", href)
        url = httpx.URL("https://x/?u=" + m.group(1)).params.get("u") if m else href
        if url.startswith("//"):
            url = "https:" + url
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
        out.append({"titulo": html.unescape(re.sub("<.*?>", "", tit)).strip(), "url": url,
                    "trecho": html.unescape(re.sub("<.*?>", "", sn.group(1))).strip() if sn else "",
                    "veiculo": "", "dominio": domain_of(url), "data": "", "origem": "DuckDuckGo"})
    return out


async def buscar_bing(client, q: str) -> list:
    try:
        r = await client.get("https://www.bing.com/search",
                             params={"format": "rss", "q": q, "setlang": "pt-BR", "cc": "BR"})
        d = feedparser.parse(r.text)
    except Exception:
        return []
    return [{"titulo": e.get("title", ""), "url": e.get("link", ""),
             "trecho": re.sub("<.*?>", "", e.get("summary", ""))[:260], "veiculo": "",
             "dominio": domain_of(e.get("link", "")), "data": e.get("published", ""),
             "origem": "Bing"} for e in d.entries[:10]]


async def buscar_agencias_direto(client, q: str) -> list:
    """Busca interna dos sites das agências (gratuita e independente dos buscadores)."""
    qq = quote(q)
    feeds = [("Boatos.org", f"https://www.boatos.org/?s={qq}&feed=rss2"),
             ("Projeto Comprova", f"https://projetocomprova.com.br/?s={qq}&feed=rss2"),
             ("E-farsas", f"https://www.e-farsas.com/?s={qq}&feed=rss2")]

    async def feed(nome, url):
        try:
            r = await client.get(url, timeout=12)
            d = feedparser.parse(r.text)
        except Exception:
            return []
        return [{"titulo": html.unescape(e.get("title", "")), "url": e.get("link", ""),
                 "trecho": html.unescape(re.sub("<.*?>", "", e.get("summary", "")))[:260], "veiculo": nome,
                 "dominio": domain_of(e.get("link", "")), "data": e.get("published", ""),
                 "origem": nome} for e in d.entries[:8]]

    async def aosfatos():
        try:
            r = await client.get(f"https://www.aosfatos.org/noticias/?q={qq}", timeout=12)
        except Exception:
            return []
        out, seen = [], set()
        for path, tit in re.findall(r'<a[^>]+href="(/noticias/[^"]+)"[^>]*title="([^"]+)"', r.text):
            if path in seen:
                continue
            seen.add(path)
            out.append({"titulo": html.unescape(tit), "url": "https://www.aosfatos.org" + path, "trecho": "",
                        "veiculo": "Aos Fatos", "dominio": "aosfatos.org", "data": "", "origem": "Aos Fatos"})
        return out[:8]

    res = await asyncio.gather(*(feed(n, u) for n, u in feeds), aosfatos(), return_exceptions=True)
    return [i for r in res if isinstance(r, list) for i in r]


async def buscar_wikipedia(client, q: str) -> list:
    try:
        r = await client.get("https://pt.wikipedia.org/w/api.php",
                             params={"action": "query", "list": "search", "srsearch": q, "format": "json",
                                     "srlimit": 5, "utf8": 1})
        hits = r.json().get("query", {}).get("search", [])
    except Exception:
        return []
    return [{"titulo": h["title"] + " — Wikipédia",
             "url": "https://pt.wikipedia.org/wiki/" + quote(h["title"].replace(" ", "_")),
             "trecho": html.unescape(re.sub("<.*?>", "", h.get("snippet", ""))), "veiculo": "Wikipédia",
             "dominio": "pt.wikipedia.org", "data": "", "origem": "Wikipédia"} for h in hits]


async def buscar_factcheck_api(client, q: str) -> list:
    if not GOOGLE_FACTCHECK_KEY:
        return []
    try:
        r = await client.get("https://factchecktools.googleapis.com/v1alpha1/claims:search",
                             params={"query": q, "key": GOOGLE_FACTCHECK_KEY, "languageCode": "pt"})
        claims = r.json().get("claims", [])
    except Exception:
        return []
    out = []
    for cl in claims[:8]:
        for cr in (cl.get("claimReview") or [])[:1]:
            out.append({"titulo": cr.get("title") or cl.get("text", ""), "url": cr.get("url", ""),
                        "trecho": f"Alegação: {cl.get('text', '')}", "rating": cr.get("textualRating", ""),
                        "veiculo": (cr.get("publisher") or {}).get("name", ""),
                        "dominio": domain_of(cr.get("url", "")), "data": cr.get("reviewDate", ""),
                        "origem": "Google Fact Check"})
    return out


RX_FALSO = re.compile(r"(\bé falso|\bfalso\b|#fake|\bé fake|\bfake\b|\bmentira|\bboato|\bgolpe\b|"
                      r"\bdesmente|\bnão é verdade|\bnão procede|\binverídic|\bmontagem|\bfabricad|"
                      r"\bfalsa\b|\bfalsamente|\bnão existe|\bfraude|\bpants on fire|\bfalse\b|\bhoax|"
                      r"teoria conspirat|\bconspira|\bcontrário do que)", re.I)
RX_ENGANOSO = re.compile(r"(enganos|fora de contexto|descontextualiz|distorc|exager|imprecis|"
                         r"\bsátira|\bsatír|\bediltad|editad|manipulad|meia[- ]verdade|parcialmente|"
                         r"insustent|sem provas|sem evidência|misleading|out of context|half true|"
                         r"mostly false|not the whole)", re.I)
RX_VERDADE = re.compile(r"(\bé verdade|\bverdadeir|#fato\b|\bé fato\b|\bconfirmad|\bé real\b|"
                        r"\bprocede\b|\bcorreto\b|\btrue\b|mostly true)", re.I)
RX_DESMENTIDO = re.compile(r"(^\W*(falso|é falso|#fake|é #fake|fake|boato|mentira|enganoso)\b|é falso que|é #fake que|"
                           r"não é verdade que|ao contrário do que|é boato que|checagem)", re.I)
# títulos que não afirmam o fato (perguntas, explicações, alertas de golpe)
RX_NAO_AFIRMA = re.compile(r"(\?|\bgolpe|\balerta|\bexplica|\bentenda|\bregras\b|\bmito|\bboato|\bsaiba\b|"
                           r"\bcomo funciona|\bquando\b.*\b(pode|é)\b|\bverdade ou)", re.I)
RX_NEG = re.compile(r"\b(não|nunca|nem)\b", re.I)


def classificar_checagem(texto: str, rating: str = "", titulo: str = "") -> str | None:
    if titulo and not rating:  # o título da checagem é o sinal mais forte
        if RX_FALSO.search(titulo) and not RX_ENGANOSO.search(titulo):
            return "FALSO"
        if RX_ENGANOSO.search(titulo):
            return "ENGANOSO"
    t = f"{rating} {texto}"
    if rating:
        if RX_ENGANOSO.search(rating):
            return "ENGANOSO"
        if RX_FALSO.search(rating):
            return "FALSO"
        if RX_VERDADE.search(rating):
            return "VERDADEIRO"
    if RX_ENGANOSO.search(t):
        return "ENGANOSO"
    if RX_FALSO.search(t):
        return "FALSO"
    if RX_VERDADE.search(t) and not RX_NEG.search(t):
        return "VERDADEIRO"
    if RX_NEG.search(t):  # títulos de agências do tipo "Pix não terá taxa…"
        return "FALSO"
    return None


EVID_CACHE: dict = {}


async def coletar_evidencias(alegacao: str) -> dict:
    chave = " ".join(sorted(stem(k) for k in keywords(alegacao, 8)))
    hit = EVID_CACHE.get(chave)
    if hit and time.time() - hit[0] < 6 * 3600:
        return json.loads(hit[1])
    evid = await _coletar(alegacao)
    if evid["itens"]:
        EVID_CACHE[chave] = (time.time(), json.dumps(evid, ensure_ascii=False))
    return evid


async def _coletar(alegacao: str) -> dict:
    q = query_de(alegacao)
    kws = keywords(alegacao, 8)
    q_curta = " ".join(keywords(alegacao, 4))
    async with httpx.AsyncClient(timeout=15, headers=UA, follow_redirects=True) as c:
        res = await asyncio.gather(
            buscar_gnews(c, q, True), buscar_ddg(c, q + " checagem fato ou fake"),
            buscar_gnews(c, q_curta, True), buscar_ddg(c, q_curta + " é falso"),
            buscar_ddg(c, q), buscar_gnews(c, q, False), buscar_factcheck_api(c, alegacao),
            buscar_gnews(c, q_curta, False), buscar_bing(c, q), buscar_bing(c, q_curta + " checagem"),
            buscar_wikipedia(c, q_curta),
            # buscas extras nas agências (funcionam mesmo quando o DuckDuckGo bloqueia o servidor)
            buscar_bing(c, q_curta + " (" + " OR ".join(f"site:{s.split('/')[0]}" for s in FC_GNEWS_SITES[:6]) + ")"),
            buscar_gnews(c, q_curta + " falso", True), buscar_gnews(c, q_curta + " fake", False),
            buscar_agencias_direto(c, " ".join(keywords(alegacao, 3))),
            return_exceptions=True)
        res = [r if isinstance(r, list) else [] for r in res]
    todos, vistos = [], set()
    for lst in res:
        for it in lst:
            key = norm(it["titulo"])[:80]
            if not it["titulo"] or key in vistos or len(it["titulo"].split()) < 4:
                continue
            vistos.add(key)
            it["relevancia"] = round(relevance(kws, it["titulo"] + " " + it.get("trecho", "")), 2)
            it["checador"] = factchecker_name(it["url"], it.get("src_href", ""))
            if not it["checador"] and it.get("origem") == "Google Fact Check":
                it["checador"] = it.get("veiculo") or "Agência de checagem"
            it["confiavel"] = bool(it["checador"]) or is_reliable(it["url"]) or is_reliable(it.get("src_href", ""))
            if not it["checador"] and it["confiavel"] and RX_DESMENTIDO.search(it["titulo"]):
                it["checador"] = (it.get("veiculo") or it["dominio"]) + " (checagem)"
            it["classificacao"] = (classificar_checagem(it["titulo"] + " " + it.get("trecho", ""),
                                                        it.get("rating", ""), it["titulo"]) if it["checador"] else None)
            it.pop("src_href", None)
            todos.append(it)
    todos.sort(key=lambda x: (-(bool(x["checador"])), -x["relevancia"]))
    return {"query": q, "palavras_chave": kws, "itens": todos}


# ──────────────────────────────────────────────────────────────────
# 4) VEREDITO (regras transparentes + LLM gratuito opcional)
# ──────────────────────────────────────────────────────────────────
def veredito_regras(alegacao: str, evid: dict, sinais: dict, dominio: dict | None) -> dict:
    itens = evid["itens"]
    checks = [i for i in itens if i["checador"] and i["classificacao"] and i["relevancia"] >= 0.5]
    peso = {v: 0.0 for v in VERDICTS}
    for i in checks:
        peso[i["classificacao"]] += i["relevancia"] ** 2
    total = sum(peso.values())
    top_check = max((i["relevancia"] for i in checks), default=0)
    # corroboração na imprensa: veículos reconhecidos contam 1; demais sites, só se quase idênticos
    corrob = [i for i in itens if not i["checador"] and i["relevancia"] >= 0.6
              and not RX_FALSO.search(i["titulo"]) and not RX_NAO_AFIRMA.search(i["titulo"])
              and (i["confiavel"] or i["relevancia"] >= 0.8)]
    pontos = sum(1 if i["confiavel"] else 0.34 for i in corrob)
    if dominio and dominio.get("confiavel") and not dominio.get("satira"):
        pontos += 1
    top_corrob = max((i["relevancia"] for i in corrob if i["confiavel"]), default=0)
    motivos = []

    if dominio and dominio.get("satira"):
        return {"veredito": "ENGANOSO", "confianca": 85, "motivos": [
            f"O site {dominio['dominio']} é conhecido por publicar humor/sátira — o conteúdo não é notícia real."],
            "base": "satira"}

    imprensa_vence = (pontos >= 2 and top_corrob >= 0.85 and top_check < 0.8
                      and top_check < top_corrob - 0.1 and sinais["score"] < 40)
    if total > 0 and not imprensa_vence:
        v = max(peso, key=peso.get)
        share = peso[v] / total
        n = sum(1 for i in checks if i["classificacao"] == v)
        conf = int(min(96, 40 + 30 * share + 6 * min(n, 3) + 14 * top_check))
        agencias = sorted({i["checador"] for i in checks if i["classificacao"] == v})
        rot = {"FALSO": "falso", "ENGANOSO": "enganoso", "VERDADEIRO": "verdadeiro",
               "SEM EVIDÊNCIA": "sem evidência"}[v]
        if n == 1:
            motivos.append(f"Uma checagem profissional relacionada classifica o conteúdo como {rot} "
                           f"({agencias[0]}).")
        else:
            motivos.append(f"{n} checagens profissionais relacionadas classificam o conteúdo como {rot} "
                           f"({', '.join(agencias[:4])}).")
        if share < 0.75:
            motivos.append("Algumas checagens trazem classificações diferentes (por exemplo, versões distorcidas "
                           "do mesmo boato) — leia as fontes para os detalhes.")
        if top_check < 0.7:
            motivos.append("As checagens tratam de alegações parecidas, não necessariamente idênticas — confira se "
                           "é o mesmo caso.")
            conf -= 10
        base = "checagens"
    elif pontos >= 2 and sinais["score"] < 40:
        v = "VERDADEIRO"
        veic = sorted({i["veiculo"] or i["dominio"] for i in corrob if i["confiavel"]}) or \
            sorted({i["veiculo"] or i["dominio"] for i in corrob})
        conf = int(min(85, 48 + 8 * pontos))
        motivos.append(f"O fato é noticiado por fontes reconhecidas ({', '.join(veic[:4])})"
                       + (" e as checagens encontradas tratam de outros boatos sobre o tema." if total > 0
                          else " e não há checagens desmentindo."))
        base = "imprensa"
    else:
        v = "SEM EVIDÊNCIA"
        conf = 45 + min(30, sinais["score"] // 3)
        motivos.append("Não encontramos checagens profissionais nem cobertura de imprensa confiável "
                       "que confirmem essa alegação.")
        base = "ausencia"

    if sinais["score"] >= 40:
        motivos.append("O texto usa linguagem típica de desinformação"
                       + (f" ({', '.join(sinais['termos'][:4])})" if sinais["termos"] else "")
                       + ", como apelo à urgência e ao compartilhamento.")
        if v == "VERDADEIRO":
            conf -= 10
    if dominio:
        if dominio.get("idade_dias") is not None and dominio["idade_dias"] < 180:
            motivos.append(f"O domínio {dominio['dominio']} foi registrado há apenas "
                           f"{dominio['idade_dias']} dias — sites recém-criados são comuns em campanhas falsas.")
            if v == "SEM EVIDÊNCIA":
                conf += 8
        elif dominio.get("confiavel"):
            motivos.append(f"A página é de um veículo reconhecido ({dominio['dominio']}).")
    return {"veredito": v, "confianca": max(20, min(97, conf)), "motivos": motivos, "base": base}


PROMPT_LLM = """Você é um verificador de fatos brasileiro, rigoroso e imparcial.
Avalie a ALEGAÇÃO usando SOMENTE as EVIDÊNCIAS numeradas abaixo (títulos de checagens e notícias).
Não invente fatos nem links. Se as evidências não bastarem, use "SEM EVIDÊNCIA".
Responda APENAS um JSON: {{"veredito":"FALSO|ENGANOSO|VERDADEIRO|SEM EVIDÊNCIA","confianca":0-100,
"explicacao":"até 3 frases em português simples, citando as evidências pelo número [n]","alegacao":"a alegação reescrita em 1 frase clara"}}

ALEGAÇÃO: "{alegacao}"
SINAIS DE ALERTA NO TEXTO: {sinais}
PRÉ-ANÁLISE AUTOMÁTICA: {pre}
EVIDÊNCIAS:
{evid}"""


async def llm_json(prompt: str) -> dict | None:
    async with httpx.AsyncClient(timeout=45) as c:
        try:
            if GEMINI_API_KEY:
                r = await c.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                    params={"key": GEMINI_API_KEY},
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}})
                txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            elif GROQ_API_KEY:
                r = await c.post("https://api.groq.com/openai/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                                 json={"model": GROQ_MODEL, "temperature": 0.1,
                                       "response_format": {"type": "json_object"},
                                       "messages": [{"role": "user", "content": prompt}]})
                txt = r.json()["choices"][0]["message"]["content"]
            elif OLLAMA_URL:
                r = await c.post(f"{OLLAMA_URL.rstrip('/')}/api/generate",
                                 json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                                       "format": "json", "options": {"temperature": 0.1}}, timeout=120)
                txt = r.json()["response"]
            else:
                return None
            m = re.search(r"\{.*\}", txt, re.S)
            return json.loads(m.group(0)) if m else None
        except Exception:
            return None


def llm_nome():
    if GEMINI_API_KEY:
        return f"Gemini ({GEMINI_MODEL})"
    if GROQ_API_KEY:
        return f"Groq ({GROQ_MODEL})"
    if OLLAMA_URL:
        return f"Ollama ({OLLAMA_MODEL})"
    return None


def montar_explicacao(v: dict) -> str:
    return " ".join(v["motivos"][:3])


# ──────────────────────────────────────────────────────────────────
# 5) PIPELINE
# ──────────────────────────────────────────────────────────────────
def base_url(req: Request) -> str:
    if PUBLIC_URL:
        return PUBLIC_URL
    proto = req.headers.get("x-forwarded-proto", req.url.scheme)
    host = req.headers.get("x-forwarded-host", req.headers.get("host", req.url.netloc))
    return f"{proto}://{host}"


def links_busca_reversa(img_url: str) -> list:
    u = quote(img_url, safe="")
    return [{"nome": "Google Lens", "url": f"https://lens.google.com/uploadbyurl?url={u}"},
            {"nome": "Yandex", "url": f"https://yandex.com/images/search?rpt=imageview&url={u}"},
            {"nome": "TinEye", "url": f"https://tineye.com/search?url={u}"},
            {"nome": "Bing", "url": f"https://www.bing.com/images/search?view=detailv2&iss=sbi&q=imgurl:{u}"}]


MEDIA_SEM = asyncio.Semaphore(int(os.getenv("MEDIA_CONCURRENCY", "1")))


async def executar(job: Job, texto: str, arquivo: tuple | None, burl: str, publico: bool = True):
    aid = job.id
    try:
        tipo, ref, titulo, corpo = "texto", "", "", ""
        dominio, midia, pagina = None, {}, {}
        texto = (texto or "").strip()
        url_m = re.fullmatch(r"\s*(https?://\S+)\s*", texto) if texto else None
        if not url_m and texto:
            mm = re.search(r"https?://\S+", texto)
            if mm and len(texto) - len(mm.group(0)) < 15:
                url_m = mm

        h = hashlib.sha256()
        if arquivo:
            h.update(arquivo[2][:5_000_000])
        h.update(texto.encode())
        input_hash = h.hexdigest()
        hit = cached(input_hash)
        if hit:
            job.step("cache", "Já verificamos este conteúdo recentemente", "ok",
                     "Reaproveitando a análise para responder na hora.")
            if publico and not hit.get("publico", True):
                hit["publico"] = True
                save(hit)
            job.result, job.status = hit, "done"
            return

        # ── Entrada
        if arquivo:
            fname, ctype, data = arquivo
            ext = (Path(fname).suffix or "").lower()[:6]
            is_video = (ctype or "").startswith("video") or ext in (".mp4", ".mov", ".webm", ".mkv", ".avi", ".3gp", ".m4v")
            stored = TMP_DIR / f"{aid}{ext or ('.mp4' if is_video else '.jpg')}"
            stored.write_bytes(data)
            ref = fname
            if MEDIA_SEM.locked():
                job.step("fila", "Aguardando a vez (outra mídia está sendo analisada)", "wait")
            async with MEDIA_SEM:
                job.done("fila", "pronto")
                try:
                    if is_video:
                        tipo = "video"
                        job.step("media", "Extraindo áudio e quadros do vídeo")
                        job.step("asr", "Transcrevendo a fala", "wait")
                        v = await asyncio.to_thread(processar_video, stored, aid)
                        if not v["quadros"] and not v["transcricao"] and v.get("duracao") is None:
                            raise ValueError("Não conseguimos abrir esse vídeo. Envie em MP4, MOV ou WEBM.")
                        job.done("media", f"{v.get('duracao') or '?'} s · {len(v['quadros'])} quadros")
                        job.done("asr", (f"{len(v['transcricao'].split())} palavras transcritas"
                                         if v["transcricao"] else "sem fala detectada"),
                                 "ok" if v["transcricao"] else "warn")
                        midia = {"tipo": "video", "arquivo": "", **v,
                                 "quadros": [f"/media/{q}" for q in v["quadros"]],
                                 "busca_reversa": links_busca_reversa(f"{burl}/media/{v['quadros'][min(1, len(v['quadros']) - 1)]}")
                                 if v["quadros"] else []}
                        corpo = "\n".join(x for x in (v["transcricao"], v["ocr_quadros"]) if x)
                    else:
                        tipo = "imagem"
                        job.step("ocr", "Lendo o texto da imagem (OCR)")
                        try:
                            im = await asyncio.to_thread(analisar_imagem, stored)
                        except Exception:
                            raise ValueError("Não conseguimos abrir essa imagem. Envie em JPG, PNG ou WEBP.")
                        job.done("ocr", f"{len(im['ocr'].split())} palavras lidas" if im["ocr"] else
                                 "nenhum texto legível", "ok" if im["ocr"] else "warn")
                        nome = f"{aid}.jpg"
                        media_put(nome, aid, "image/jpeg", im.pop("_thumb"))
                        job.step("meta", "Checando metadados e preparando busca reversa", "ok",
                                 ("EXIF: " + ", ".join(f"{k}={v}" for k, v in im["exif"].items()))
                                 if im["exif"] else "sem metadados (comum em imagens de WhatsApp/redes)")
                        midia = {"tipo": "imagem", "arquivo": f"/media/{nome}", **im,
                                 "busca_reversa": links_busca_reversa(f"{burl}/media/{nome}")}
                        corpo = im["ocr"]
                finally:
                    stored.unlink(missing_ok=True)
            if texto:
                corpo = texto + "\n" + corpo
        elif url_m:
            tipo, ref = "link", url_m.group(1) if url_m.groups() else url_m.group(0)
            job.step("fetch", "Abrindo a página e extraindo o texto")
            job.step("domain", "Investigando o site (idade do domínio, reputação)", "wait")
            pg, dominio = await asyncio.gather(extrair_url(ref), analisar_dominio(ref),
                                               return_exceptions=True)
            if isinstance(pg, Exception):
                job.done("fetch", f"não foi possível abrir a página ({str(pg)[:80] or type(pg).__name__}) — "
                                  "usando o endereço como pista", "warn")
                pg = {"titulo": "", "texto": "", "url_final": ref}
            else:
                job.done("fetch", pg["titulo"][:90] or f"{len(pg['texto'].split())} palavras")
            if isinstance(dominio, Exception):
                dominio = {"dominio": domain_of(ref)}
            det = []
            if dominio.get("registro"):
                det.append(f"registrado em {dominio['registro']}")
            if dominio.get("checador"):
                det.append("agência de checagem")
            elif dominio.get("confiavel"):
                det.append("veículo reconhecido")
            if dominio.get("satira"):
                det.append("site de humor/sátira")
            job.done("domain", f"{dominio.get('dominio')} · " + (", ".join(det) or "sem histórico conhecido"))
            pagina = pg
            titulo, corpo = pg.get("titulo", ""), pg.get("texto", "")
            if not titulo and not corpo:
                # sem conteúdo: usa o próprio caminho da URL como pista
                slug = urlparse(ref).path.replace("-", " ").replace("_", " ").replace("/", " ")
                slug = re.sub(r"\.\w{2,5}\b|\b\w*\d\w*\b", " ", slug)
                palavras = [w for w in slug.split() if len(w) > 2]
                corpo = " ".join(palavras) if len(palavras) >= 5 else ""
        else:
            if len(texto) < 12:
                raise ValueError("Escreva uma frase completa, cole um link ou envie uma imagem/vídeo.")
            corpo = texto
            ref = texto[:200]

        # ── Alegação
        job.step("claim", "Identificando a alegação principal")
        sinais = sinais_texto((titulo + "\n" + corpo)[:8000])
        alegacao = heuristica_alegacao(titulo, corpo)
        if not alegacao or len(alegacao) < 8:
            alegacao = ""
        job.done("claim", f"“{alegacao[:110]}”" if alegacao else "nenhuma alegação verificável encontrada",
                 "ok" if alegacao else "warn")

        # ── Evidências
        evid = {"query": "", "palavras_chave": [], "itens": []}
        if alegacao:
            job.step("search", "Pesquisando em agências de checagem e na imprensa")
            evid = await coletar_evidencias(alegacao)
            nfc = sum(1 for i in evid["itens"] if i["checador"] and i["relevancia"] >= 0.4)
            job.done("search", f"{len(evid['itens'])} resultados · {nfc} checagens relacionadas")

        # ── Veredito
        job.step("verdict", "Cruzando as evidências e emitindo o veredito")
        if alegacao:
            v = veredito_regras(alegacao, evid, sinais, dominio)
        else:
            v = {"veredito": "SEM EVIDÊNCIA", "confianca": 30, "base": "sem_alegacao",
                 "motivos": [("Não conseguimos ler o conteúdo desta página (o site bloqueou o acesso ou ela não existe). "
                              "Copie e cole o texto da notícia aqui para verificarmos.") if tipo == "link" else
                             ("Não encontramos texto ou fala verificável neste conteúdo. "
                              "Use a busca reversa abaixo para descobrir onde a imagem já circulou.")]}
        explicacao = montar_explicacao(v)
        motor = "regras"
        if alegacao and llm_nome():
            top = [i for i in evid["itens"] if i["relevancia"] >= 0.3][:10]
            ev_txt = "\n".join(f"[{n + 1}] {i['titulo']} — {i['checador'] or i['veiculo'] or i['dominio']}"
                               f"{' (checagem: ' + i['classificacao'] + ')' if i.get('classificacao') else ''}"
                               f"{' · ' + i['trecho'][:160] if i.get('trecho') else ''}"
                               for n, i in enumerate(top)) or "(nenhuma)"
            out = await llm_json(PROMPT_LLM.format(alegacao=alegacao, sinais=json.dumps(sinais, ensure_ascii=False),
                                                   pre=f"{v['veredito']} ({v['confianca']}%)", evid=ev_txt))
            if out and out.get("veredito") in VERDICTS:
                v["veredito"] = out["veredito"]
                v["confianca"] = int(max(10, min(98, int(out.get("confianca", v["confianca"])))))
                explicacao = (out.get("explicacao") or explicacao).strip()
                motor = llm_nome()
                for n, i in enumerate(top):
                    i["ref"] = n + 1
        job.done("verdict", f"{v['veredito']} · confiança {v['confianca']}%")

        fontes = [i for i in evid["itens"] if i["relevancia"] >= 0.3][:8]
        checagens = [i for i in evid["itens"] if i["checador"] and i["relevancia"] >= 0.4][:6]
        rec = {"id": aid, "created_at": now_iso(), "input_type": tipo, "input_ref": ref,
               "input_hash": input_hash, "alegacao": alegacao or "(sem alegação verificável)",
               "veredito": v["veredito"], "confianca": v["confianca"], "explicacao": explicacao,
               "motivos": v["motivos"], "base": v["base"], "motor": motor,
               "fontes": fontes, "checagens": checagens, "sinais": sinais, "dominio": dominio,
               "pagina": {k: pagina.get(k) for k in ("titulo", "data", "autor", "site", "url_final")} if pagina else None,
               "midia": midia or None, "busca": {"query": evid["query"], "total": len(evid["itens"])},
               "publico": bool(publico),
               "verificar_tambem": [
                   {"nome": "Google Fact Check Explorer",
                    "url": "https://toolbox.google.com/factcheck/explorer/search/" + quote(alegacao or ref) + ";hl=pt"},
                   {"nome": "Buscar no Google Notícias",
                    "url": "https://news.google.com/search?q=" + quote(evid["query"] or ref) + "&hl=pt-BR&gl=BR&ceid=BR:pt-419"},
               ]}
        if not alegacao:  # não reaproveitar análises sem conteúdo
            rec["input_hash"] = "nocache-" + aid
        save(rec)
        job.result, job.status = rec, "done"
    except ValueError as e:
        job.error, job.status = str(e), "error"
    except Exception as e:  # noqa
        job.error, job.status = f"Falha inesperada: {type(e).__name__}: {str(e)[:200]}", "error"


# ──────────────────────────────────────────────────────────────────
# 6) APP / ROTAS
# ──────────────────────────────────────────────────────────────────
app = FastAPI(title="Antifake API", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def headers_seguranca(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.url.path == "/" or request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(FRONTEND)


@app.get("/media/{name}")
def media(name: str):
    if not re.fullmatch(r"[a-f0-9]{6,32}(_q\d)?\.jpg", name):
        raise HTTPException(404, "Arquivo não encontrado")
    row = media_get(name)
    if not row:
        raise HTTPException(404, "Arquivo não encontrado")
    return Response(bytes(row["content"]), media_type=row["mime"] or "image/jpeg",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/health")
def health():
    banco = "PostgreSQL" if USE_PG else "SQLite (local)"
    try:
        sql("SELECT 1", (), "one")
        banco_ok = True
    except Exception:
        banco_ok = False
    return {"status": "ok" if banco_ok else "degradado", "version": app.version, "custo": "gratuito",
            "banco": banco, "banco_ok": banco_ok, "persistente": USE_PG,
            "limites": {"max_upload_mb": MAX_UPLOAD_MB, "video_max_s": VIDEO_MAX_SECONDS},
            "recursos": {"ocr": bool(shutil.which("tesseract")), "video": bool(shutil.which("ffmpeg")),
                         "transcricao": True, "fact_check_api": bool(GOOGLE_FACTCHECK_KEY), "llm": llm_nome()}}


@app.post("/api/check")
async def check(request: Request, text: str = Form(""), file: UploadFile | None = File(None),
                publico: str = Form("1")):
    gc_jobs()
    ip = client_ip(request)
    if not rate_ok("check:" + ip, RATE_CHECKS, 600):
        raise HTTPException(429, "Muitas verificações seguidas deste aparelho. Aguarde alguns minutos e tente de novo.")
    if len(text) > 20000:
        text = text[:20000]
    arquivo = None
    if file is not None and file.filename:
        data = await file.read()
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"Arquivo maior que {MAX_UPLOAD_MB} MB. Envie um trecho menor do vídeo.")
        if not data:
            raise HTTPException(422, "O arquivo chegou vazio. Tente anexar de novo.")
        ctype = (file.content_type or "").lower()
        ext = Path(file.filename).suffix.lower()
        ok_ext = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp",
                  ".mp4", ".mov", ".webm", ".m4v", ".3gp", ".mkv", ".avi")
        if not (ctype.startswith("image") or ctype.startswith("video") or ext in ok_ext):
            raise HTTPException(415, "Envie uma imagem (JPG, PNG, WEBP, HEIC) ou um vídeo (MP4, MOV, WEBM).")
        arquivo = (file.filename, ctype, data)
    if not text.strip() and not arquivo:
        raise HTTPException(422, "Cole um texto ou link, ou envie uma imagem/vídeo.")
    job = Job()
    asyncio.create_task(executar(job, text, arquivo, base_url(request), publico not in ("0", "false", "nao")))
    return {"job": job.id}


@app.get("/api/check/{job_id}")
def check_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Essa verificação não está mais em andamento (o servidor pode ter reiniciado). "
                                 "Envie de novo.")
    return job.public()


@app.get("/api/analysis/{aid}")
def analysis(aid: str):
    return load(aid)


LISTA_COLS = "id, created_at, input_type, alegacao, veredito, confianca"


@app.get("/api/history")
def history(limit: int = 12):
    return sql(f"SELECT {LISTA_COLS} FROM analyses WHERE publico=1 AND alegacao <> '(sem alegação verificável)' "
               "ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 50)),), "all") or []


@app.get("/api/search")
def search(q: str = "", veredito: str = "", tipo: str = "", page: int = 1, per_page: int = 15):
    per_page = max(1, min(per_page, 50))
    page = max(1, min(page, 200))
    where = ["publico=1", "alegacao <> '(sem alegação verificável)'"]
    params: list = []
    termos = [norm(t) for t in re.findall(r"[\wÀ-ÿ%$]+", q or "") if len(t) >= 2][:8]
    for t in termos:
        where.append("busca LIKE ?")
        params.append(f"%{t}%")
    if veredito in VERDICTS:
        where.append("veredito=?")
        params.append(veredito)
    if tipo in ("texto", "link", "imagem", "video"):
        where.append("input_type=?")
        params.append(tipo)
    w = " AND ".join(where)
    total = int((sql(f"SELECT COUNT(*) AS n FROM analyses WHERE {w}", tuple(params), "one") or {"n": 0})["n"])
    rows = sql(f"SELECT {LISTA_COLS} FROM analyses WHERE {w} ORDER BY created_at DESC LIMIT ? OFFSET ?",
               tuple(params) + (per_page, (page - 1) * per_page), "all") or []
    return {"total": total, "page": page, "per_page": per_page,
            "pages": max(1, -(-total // per_page)), "itens": rows}


@app.get("/api/stats")
def stats():
    pub = "publico=1 AND alegacao <> '(sem alegação verificável)'"
    rows = sql(f"SELECT veredito, COUNT(*) AS n FROM analyses WHERE {pub} GROUP BY veredito", (), "all") or []
    d = {v: 0 for v in VERDICTS}
    d.update({r["veredito"]: int(r["n"]) for r in rows})
    d["total"] = sum(d[v] for v in VERDICTS)
    hoje = datetime.now(timezone.utc).date().isoformat()
    d["hoje"] = int((sql(f"SELECT COUNT(*) AS n FROM analyses WHERE {pub} AND created_at >= ?",
                         (hoje,), "one") or {"n": 0})["n"])
    return d


@app.post("/api/analysis/{aid}/feedback")
async def feedback(aid: str, request: Request):
    load(aid)
    try:
        body = await request.json()
    except Exception:
        body = {}
    tipo = body.get("tipo")
    if tipo not in ("util", "errado"):
        raise HTTPException(422, "Tipo de avaliação inválido.")
    ip = client_ip(request)
    if not rate_ok("fb:" + ip, 20, 3600):
        raise HTTPException(429, "Muitas avaliações seguidas. Tente mais tarde.")
    iph = hashlib.sha256((ip + aid).encode()).hexdigest()[:24]
    if sql("SELECT id FROM feedback WHERE analysis_id=? AND ip_hash=?", (aid, iph), "one"):
        return {"ok": True, "repetido": True, "contagem": feedback_counts(aid)}
    sql("INSERT INTO feedback(id, analysis_id, created_at, tipo, comentario, ip_hash) VALUES (?,?,?,?,?,?)",
        (uuid.uuid4().hex[:12], aid, now_iso(), tipo, str(body.get("comentario", ""))[:1000], iph))
    return {"ok": True, "contagem": feedback_counts(aid)}


@app.get("/api/admin/feedback")
def admin_feedback(token: str = "", limit: int = 100):
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(403, "Acesso negado.")
    return sql("""SELECT f.created_at, f.tipo, f.comentario, a.id, a.veredito, a.alegacao
                  FROM feedback f JOIN analyses a ON a.id = f.analysis_id
                  ORDER BY f.created_at DESC LIMIT ?""", (min(limit, 500),), "all")


@app.post("/api/admin/hide/{aid}")
def admin_hide(aid: str, token: str = ""):
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(403, "Acesso negado.")
    rec = load(aid)
    rec["publico"] = False
    rec.pop("feedback", None)
    save(rec)
    return {"ok": True}


@app.get("/robots.txt", response_class=Response)
def robots():
    return Response("User-agent: *\nAllow: /\nDisallow: /api/\n", media_type="text/plain")


# ── keep-alive opcional: evita que o plano grátis durma (use PUBLIC_URL)
@app.on_event("startup")
async def _keepalive():
    if not (PUBLIC_URL and os.getenv("KEEPALIVE", "1") == "1"):
        return

    async def loop():
        await asyncio.sleep(60)
        async with httpx.AsyncClient(timeout=20) as c:
            while True:
                try:
                    await c.get(PUBLIC_URL + "/api/ping")
                except Exception:
                    pass
                await asyncio.sleep(600)

    asyncio.create_task(loop())


@app.get("/api/ping")
def ping():
    return {"pong": True}


CORES = {"FALSO": "#C8102E", "ENGANOSO": "#C77700", "VERDADEIRO": "#1E7F4F", "SEM EVIDÊNCIA": "#4A5866"}
SUBT = {"FALSO": "não é verdade", "ENGANOSO": "distorcido ou fora de contexto",
        "VERDADEIRO": "confirmado por fontes", "SEM EVIDÊNCIA": "não há como confirmar"}
FONT_DIR = BASE_DIR / "fonts"
_OG_CACHE: "OrderedDict[str, bytes]" = OrderedDict()


def _font(nome: str, size: int, peso: str):
    from PIL import ImageFont

    try:
        f = ImageFont.truetype(str(FONT_DIR / nome), size)
        try:
            f.set_variation_by_name(peso)
        except Exception:
            pass
        return f
    except Exception:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if peso in ("Bold", "ExtraBold") else "DejaVuSans.ttf", size)


def _wrap(draw, text, font, width, max_lines):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and " ".join(lines) != " ".join(words):
        while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines


def og_image(r: dict) -> bytes:
    from PIL import Image, ImageDraw

    W, H = 1200, 630
    cor = CORES.get(r["veredito"], "#4A5866")
    img = Image.new("RGB", (W, H), "#E6ECF1")
    d = ImageDraw.Draw(img)
    for x in range(0, W, 18):
        for y in range(0, H, 18):
            d.point((x, y), fill="#D6DEE5")
    d.rounded_rectangle((40, 40, W - 40, H - 40), 10, fill="#FFFFFF")
    d.rectangle((40, H - 120, W - 40, H - 40), fill="#0E1A24")
    d.rounded_rectangle((40, H - 130, W - 40, H - 40), 10, fill="#0E1A24")
    d.rectangle((40, H - 130, W - 40, H - 110), fill="#FFFFFF")
    # carimbo
    st = Image.new("RGBA", (520, 200), (0, 0, 0, 0))
    sd = ImageDraw.Draw(st)
    f_st = _font("Bricolage.ttf", 84 if len(r["veredito"]) < 12 else 58, "ExtraBold")
    tw = sd.textlength(r["veredito"], font=f_st)
    bw = int(tw + 60)
    sd.rounded_rectangle((10, 10, 10 + bw, 150), 12, outline=cor, width=9)
    sd.text((10 + bw / 2, 72), r["veredito"], font=f_st, fill=cor, anchor="mm")
    f_sub = _font("Plex.ttf", 22, "SemiBold")
    sd.text((10 + bw / 2, 126), SUBT.get(r["veredito"], "").upper(), font=f_sub, fill=cor, anchor="mm")
    st = st.crop((0, 0, bw + 24, 170)).rotate(6, expand=True, resample=Image.BICUBIC)
    img.paste(st, (W - 70 - st.width, 62), st)
    f_lab = _font("Plex.ttf", 22, "SemiBold")
    d.text((88, 92), "ALEGAÇÃO VERIFICADA", font=f_lab, fill="#5A6875")
    f_q = _font("Bricolage.ttf", 50, "Bold")
    maxw = W - 88 - 80 - (st.width if st.width < 560 else 0)
    linhas = _wrap(d, "“" + r["alegacao"] + "”", f_q, max(560, maxw), 4)
    y = 140
    for ln in linhas:
        d.text((88, y), ln, font=f_q, fill="#0E1A24")
        y += 62
    f_c = _font("Plex.ttf", 26, "Regular")
    d.text((88, H - 172), f"confiança {r['confianca']}%  ·  {len(r.get('checagens') or [])} checagens  ·  "
                          f"{len(r.get('fontes') or [])} fontes", font=f_c, fill="#2D3B47")
    f_m = _font("Bricolage.ttf", 34, "ExtraBold")
    d.ellipse((86, H - 104, 130, H - 60), outline="#E0475E", width=4)
    d.text((108, H - 82), "AF", font=_font("Bricolage.ttf", 17, "ExtraBold"), fill="#E0475E", anchor="mm")
    d.text((146, H - 82), "Antifake", font=f_m, fill="#FFFFFF", anchor="lm")
    f_f = _font("Plex.ttf", 24, "Regular")
    d.text((W - 88, H - 82), "Recebeu? Confira antes de repassar.", font=f_f, fill="#C9D3DC", anchor="rm")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


@app.get("/og/{aid}.png")
def og(aid: str):
    if aid not in _OG_CACHE:
        _OG_CACHE[aid] = og_image(load(aid))
        while len(_OG_CACHE) > 200:
            _OG_CACHE.popitem(last=False)
    return Response(_OG_CACHE[aid], media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/v/{aid}", response_class=HTMLResponse)
def share_page(aid: str, request: Request):
    r = load(aid)
    e = html.escape
    cor = CORES.get(r["veredito"], "#4A5866")
    fontes = "".join(f'<li><a href="{e(f["url"])}" target="_blank" rel="noopener nofollow">{e(f["titulo"])}</a>'
                     f'<span>{e(f.get("checador") or f.get("veiculo") or f.get("dominio") or "")}'
                     f'{" · " + e(f["classificacao"]) if f.get("classificacao") else ""}</span></li>'
                     for f in r.get("fontes", [])[:8])
    motivos = "".join(f"<li>{e(m)}</li>" for m in r.get("motivos", [])[:4])
    burl = base_url(request)
    url = f"{burl}/v/{aid}"
    titulo = f"{r['veredito']}: {r['alegacao'][:90]}"
    data_br = datetime.fromisoformat(r["created_at"]).strftime("%d/%m/%Y")
    ld = json.dumps({"@context": "https://schema.org", "@type": "ClaimReview", "url": url,
                     "claimReviewed": r["alegacao"], "datePublished": r["created_at"][:10],
                     "author": {"@type": "Organization", "name": "Antifake (verificação automatizada)"},
                     "reviewRating": {"@type": "Rating", "alternateName": r["veredito"].title(),
                                      "ratingValue": {"FALSO": 1, "ENGANOSO": 2, "SEM EVIDÊNCIA": 3, "VERDADEIRO": 5}.get(r["veredito"], 3),
                                      "bestRating": 5, "worstRating": 1}}, ensure_ascii=False)
    img = ""
    m = r.get("midia") or {}
    if m.get("arquivo"):
        img = f'<img class="mid" src="{e(m["arquivo"])}" alt="Imagem verificada">'
    elif m.get("quadros"):
        img = f'<img class="mid" src="{e(m["quadros"][min(1, len(m["quadros"]) - 1)])}" alt="Quadro do vídeo verificado">'
    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(titulo)} · Antifake</title>
<meta name="description" content="{e(r['explicacao'][:180])}">
<link rel="canonical" href="{e(url)}">
<meta property="og:type" content="article"><meta property="og:site_name" content="Antifake">
<meta property="og:title" content="{e(titulo)}"><meta property="og:description" content="{e(r['explicacao'][:200])}">
<meta property="og:url" content="{e(url)}"><meta property="og:image" content="{e(burl)}/og/{aid}.png">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="{e(burl)}/og/{aid}.png">
<script type="application/ld+json">{ld}</script>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,700;12..96,800&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
body{{margin:0;background:#E6ECF1;font-family:'IBM Plex Sans',system-ui,sans-serif;color:#0E1A24;display:flex;justify-content:center;padding:32px 14px}}
.c{{max-width:560px;width:100%;background:#fff;border-radius:6px;box-shadow:0 1px 0 #cfd8e0,0 18px 40px -18px rgba(14,26,36,.35);overflow:hidden}}
.top{{padding:26px 26px 0;display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}
.st{{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;font-size:30px;color:{cor};border:4px solid {cor};
padding:4px 14px;border-radius:6px;transform:rotate(-4deg);letter-spacing:.04em}}
.cf{{font-size:12px;color:#5A6875;text-align:right}}.cf b{{display:block;font-size:22px;color:#0E1A24}}
.b{{padding:20px 26px 26px}}.q{{font-family:'Bricolage Grotesque',sans-serif;font-size:22px;line-height:1.25;margin:6px 0 14px}}
p{{line-height:1.6;color:#2D3B47}}h4{{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#5A6875;margin:22px 0 8px}}
ul{{list-style:none;padding:0;margin:0}}li{{padding:8px 0;border-top:1px solid #E3E8ED;font-size:14px}}li a{{color:#1F4FD8;text-decoration:none}}
li span{{display:block;font-size:12px;color:#5A6875}}ul.m li{{border:0;padding:3px 0;color:#2D3B47}}
.mid{{max-width:100%;max-height:280px;border-radius:6px;border:1px solid #E3E8ED;display:block;margin-top:6px}}
.f{{background:#0E1A24;color:#C9D3DC;padding:14px 26px;font-size:13px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}}
.f a{{color:#fff;font-weight:600}}.av{{font-size:12px;color:#5A6875;margin-top:18px}}</style></head><body><main class="c">
<div class="top"><span class="st">{e(r['veredito'])}</span><div class="cf"><b>{r['confianca']}%</b>confiança</div></div>
<div class="b"><h4>Alegação</h4><h1 class="q">“{e(r['alegacao'])}”</h1><p>{e(r['explicacao'])}</p>
{('<h4>Por que</h4><ul class="m">' + motivos + '</ul>') if motivos and r.get("motor") != "regras" else ''}
{img}
{'<h4>Fontes</h4><ul>' + fontes + '</ul>' if fontes else ''}
<p class="av">Verificação automática feita em {data_br}. Confira sempre as fontes citadas.</p></div>
<div class="f"><span>Antifake · consulta pública e gratuita</span><a href="/#v={aid}">Ver detalhes / verificar outro →</a></div>
</main></body></html>"""


@app.exception_handler(HTTPException)
async def http_err(_, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
