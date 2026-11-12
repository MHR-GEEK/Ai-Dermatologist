import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, render_template, request, session
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BRANDS_FILE = DATA_DIR / "brands.json"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-for-local-dev")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH", str(8 * 1024 * 1024)))


def ensure_data_file():
    DATA_DIR.mkdir(exist_ok=True)
    if not BRANDS_FILE.exists():
        BRANDS_FILE.write_text(
            json.dumps(
                ["CeraVe", "Cetaphil", "La Roche-Posay", "The Ordinary", "Neutrogena"],
                indent=2,
            ),
            encoding="utf-8",
        )


def load_brands():
    ensure_data_file()
    return json.loads(BRANDS_FILE.read_text(encoding="utf-8"))


def save_brands(brands):
    ensure_data_file()
    normalized = sorted({brand.strip() for brand in brands if brand.strip()})
    BRANDS_FILE.write_text(json.dumps(normalized, indent=2), encoding="utf-8")


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def normalize_ollama_base_url():
    """Return the Ollama API root, never embedding credentials in the URL."""
    base_url = os.environ.get("OLLAMA_BASE_URL", "https://ollama.com").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("OLLAMA_BASE_URL is empty. Set it to https://ollama.com or your Ollama host.")
    # Legacy configuration placed the API key in the URL path. Strip it and use
    # the dedicated Authorization header instead.
    base_url = re.sub(r"/(?:api/)?[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}$", "", base_url)
    if base_url.endswith("/api"):
        return base_url
    return f"{base_url}/api"


def ollama_headers():
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def ollama_chat_request(messages, model=None, images=None, timeout=90):
    base_url = normalize_ollama_base_url()
    model = model or os.environ.get("OLLAMA_CHAT_MODEL") or os.environ.get("OLLAMA_MODEL") or "gemma3"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if images:
        payload["messages"][-1]["images"] = images

    request_obj = urllib.request.Request(
        f"{base_url}/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers=ollama_headers(),
        method="POST",
    )

    try:
        with urllib.request.urlopen(request_obj, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code in (401, 403):
            raise RuntimeError("Ollama authentication failed. Replace OLLAMA_API_KEY with a valid key in Vercel Environment Variables.") from exc
        if exc.code == 404:
            raise RuntimeError(f"Ollama model or endpoint not found. Check the model name ({model}) and Ollama URL.") from exc
        raise RuntimeError(f"Ollama returned HTTP {exc.code}: {error_text}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Cannot reach Ollama. Check OLLAMA_BASE_URL and the deployed server's network access.") from exc
    except TimeoutError as exc:
        raise RuntimeError("Ollama timed out. Try again or use a faster vision/chat model.") from exc

    content = (data.get("message") or {}).get("content", "").strip()
    if not content:
        raise RuntimeError(f"Ollama returned an empty response. Response keys: {', '.join(data.keys())}")
    return content


def fallback_result(message):
    return {
        "ai_available": False,
        "skin_type": "-",
        "concerns": [],
        "sensitivity": "-",
        "psl_score": "-",
        "facial_rating": "-",
        "image_ratio_score": "-",
        "proportion_notes": [],
        "routine": [],
        "notes": message,
    }


def build_analysis_prompt(brands):
    brand_text = ", ".join(brands) if brands else "No brands have been approved yet"
    return f"""
You are an educational skincare assistant, not a medical diagnostic tool.
Analyze the uploaded face image only for visible, non-sensitive skincare observations.
Do not identify the person, infer protected traits, diagnose disease, or promise results.
Only recommend products from this admin-approved brand list: {brand_text}.

Return strict JSON with this schema:
{{
  "skin_type": "one of: oily, dry, combination, normal, unclear",
  "concerns": ["short visible skincare concerns, or unclear"],
  "sensitivity": "low, moderate, high, or unclear",
  "psl_score": "subjective visual harmony score from 1.0 to 10.0, or unclear",
  "facial_rating": "subjective facial aesthetic rating such as low, average, above average, high, or unclear",
  "image_ratio_score": "visible facial proportion/photo ratio score from 1 to 100, or unclear",
  "proportion_notes": ["brief notes about visible symmetry, lighting, angle, and photo quality"],
  "routine": [
    {{"step": "AM cleanser", "recommendation": "brand-safe educational recommendation"}},
    {{"step": "AM moisturizer", "recommendation": "brand-safe educational recommendation"}},
    {{"step": "AM sunscreen", "recommendation": "brand-safe educational recommendation"}},
    {{"step": "PM cleanser", "recommendation": "brand-safe educational recommendation"}},
    {{"step": "PM treatment", "recommendation": "brand-safe educational recommendation"}},
    {{"step": "PM moisturizer", "recommendation": "brand-safe educational recommendation"}}
  ],
  "notes": "brief educational disclaimer and when to see a dermatologist"
}}
"""


def normalize_result(result, ai_available):
    return {
        "ai_available": ai_available,
        "skin_type": result.get("skin_type", "-"),
        "concerns": result.get("concerns") if isinstance(result.get("concerns"), list) else [],
        "sensitivity": result.get("sensitivity", "-"),
        "psl_score": result.get("psl_score", "-"),
        "facial_rating": result.get("facial_rating", "-"),
        "image_ratio_score": result.get("image_ratio_score", "-"),
        "proportion_notes": result.get("proportion_notes") if isinstance(result.get("proportion_notes"), list) else [],
        "routine": result.get("routine") if isinstance(result.get("routine"), list) else [],
        "notes": result.get("notes", ""),
    }


def parse_json_result(raw_text):
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    return normalize_result(json.loads(cleaned), ai_available=True)


def analyze_with_openai(image_data_url, brands):
    from openai import OpenAI

    client = OpenAI()
    prompt = build_analysis_prompt(brands)
    response = client.responses.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-5"),
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": image_data_url},
            ],
        }],
        text={"format": {"type": "json_object"}},
    )
    return parse_json_result(response.output_text)


def analyze_with_ollama(image_base64, brands):
    prompt = build_analysis_prompt(brands)
    content = ollama_chat_request(
        [{"role": "user", "content": prompt}],
        model=os.environ.get("OLLAMA_MODEL") or "gemma3",
        images=[image_base64],
        timeout=120,
    )
    return parse_json_result(content)


def chat_with_ollama(message):
    system_prompt = (
        "You are a helpful, concise AI skincare assistant. Reply clearly and naturally. "
        "Give educational skincare guidance, product routine help, and app usage help. "
        "Do not diagnose medical conditions or claim certainty from text alone. "
        "For urgent, painful, spreading, bleeding, infected, or persistent symptoms, "
        "recommend a qualified dermatologist."
    )
    return ollama_chat_request(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ],
        timeout=int(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "90")),
    )


def configured_provider():
    provider = os.environ.get("AI_PROVIDER", "").strip().lower()
    if provider:
        return provider
    if os.environ.get("OLLAMA_API_KEY") or os.environ.get("OLLAMA_BASE_URL"):
        return "ollama"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return ""


def ai_status_label():
    return "AI ready" if configured_provider() in {"ollama", "openai"} else "AI key missing"


@app.get("/")
def index():
    return render_template(
        "index.html",
        brands=load_brands(),
        ai_configured=bool(configured_provider()),
        ai_status=ai_status_label(),
    )


@app.get("/health")
def health():
    provider = configured_provider()
    return jsonify({"ok": True, "provider": provider or None, "configured": provider in {"ollama", "openai"}})


@app.post("/admin/login")
def admin_login():
    payload = request.get_json(silent=True) or {}
    password = payload.get("password", "")
    expected = os.environ.get("ADMIN_PASSWORD", "admin123")
    if password != expected:
        return jsonify({"ok": False, "message": "Wrong admin password"}), 401
    session["admin"] = True
    return jsonify({"ok": True, "message": "Logged in"})


@app.post("/admin/brands")
def add_brand():
    if not session.get("admin"):
        return jsonify({"ok": False, "message": "Admin login required"}), 403
    payload = request.get_json(silent=True) or {}
    brand = str(payload.get("brand", "")).strip()
    if not brand:
        return jsonify({"ok": False, "message": "Brand name is required"}), 400
    brands = load_brands()
    brands.append(brand)
    save_brands(brands)
    return jsonify({"ok": True, "brands": load_brands()})


@app.delete("/admin/brands/<brand>")
def delete_brand(brand):
    if not session.get("admin"):
        return jsonify({"ok": False, "message": "Admin login required"}), 403
    brands = [item for item in load_brands() if item.lower() != brand.lower()]
    save_brands(brands)
    return jsonify({"ok": True, "brands": load_brands()})


@app.post("/analyze")
def analyze():
    image = request.files.get("image")
    if not image or image.filename == "":
        return jsonify({"ok": False, "message": "Upload a face image first"}), 400
    if not allowed_file(image.filename):
        return jsonify({"ok": False, "message": "Use PNG, JPG, JPEG, or WEBP"}), 400

    image_bytes = image.read()
    provider = configured_provider()
    if provider not in {"ollama", "openai"}:
        return jsonify({"ok": True, "result": fallback_result("AI is not configured. Set AI_PROVIDER and the matching provider credentials in Vercel Environment Variables.")})

    try:
        if provider == "ollama":
            result = analyze_with_ollama(base64.b64encode(image_bytes).decode("ascii"), load_brands())
        else:
            mime_type = image.mimetype or "image/png"
            data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
            result = analyze_with_openai(data_url, load_brands())
    except Exception as exc:
        app.logger.exception("Image analysis request failed")
        return jsonify({"ok": True, "result": fallback_result(f"AI request failed: {exc}")})
    return jsonify({"ok": True, "result": result})


@app.post("/chat")
def chat():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    if not message:
        return jsonify({"ok": False, "message": "Type a message first"}), 400
    if len(message) > 8000:
        return jsonify({"ok": False, "message": "Message is too long. Keep it under 8,000 characters."}), 413

    provider = configured_provider()
    if provider != "ollama":
        return jsonify({"ok": False, "message": "AI chat is not configured. Set AI_PROVIDER=ollama, OLLAMA_BASE_URL, and OLLAMA_API_KEY in Vercel Environment Variables."}), 503

    try:
        reply = chat_with_ollama(message)
    except Exception as exc:
        app.logger.exception("Chat request failed")
        return jsonify({"ok": False, "message": str(exc)}), 502
    return jsonify({"ok": True, "reply": reply})


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"ok": False, "message": "Upload is too large. Maximum image size is 8 MB."}), 413


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the AI Dermatologist Assistant")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    ensure_data_file()
    app.run(host="0.0.0.0", port=args.port, debug=args.debug, use_reloader=args.debug)
