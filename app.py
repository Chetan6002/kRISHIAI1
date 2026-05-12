"""
KrishiAI Backend — app.py
Full backend for the KrishiAI Smart Farming Assistant
Run: pip install flask flask-cors requests Pillow python-dotenv && python app.py
"""

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
import requests, os, base64, json, random
from datetime import datetime
from io import BytesIO

# Try to load dotenv if available
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ .env file loaded successfully")
except ImportError:
    print("⚠️  python-dotenv not installed, using system environment variables")
except Exception as e:
    print(f"⚠️  Error loading .env file: {e}")

# Check required environment variables
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OWM_API_KEY = os.getenv("OWM_API_KEY")

if not OPENROUTER_API_KEY:
    print("⚠️  OPENROUTER_API_KEY not found - chat and scan will use demo mode")
if not OWM_API_KEY:
    print("⚠️  OWM_API_KEY not found - weather will use demo data")

app = Flask(__name__, static_folder='.')
CORS(app)  # Allow frontend to call this backend

# ── CONFIG ──────────────────────────────────────────────────────────────
OWM_API_KEY  = os.getenv("OWM_API_KEY", "YOUR_OPENWEATHERMAP_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
# ────────────────────────────────────────────────────────────────────────


def has_owm_key():
    return bool(OWM_API_KEY and OWM_API_KEY not in ("YOUR_OPENWEATHERMAP_KEY", "YOUR_OPENWEATHERMAP_KEY_HERE"))


def demo_weather_data(lat="12.97", lon="77.59"):
    return {
        "city": "Bengaluru",
        "lat": float(lat),
        "lon": float(lon),
        "temp": 35.0,
        "feels_like": 39.0,
        "humidity": 72,
        "wind_speed": 18.0,
        "wind_deg": 45,
        "description": "Partly cloudy",
        "icon": "03d",
        "rain_1h": 0,
        "pressure": 1008,
        "uv_index": 8,
        "forecast": [
            {"day": "Today", "pop": 20, "tmax": 35.0, "tmin": 27.0, "icon": "03d"},
            {"day": "Tue", "pop": 40, "tmax": 34.0, "tmin": 26.0, "icon": "10d"},
            {"day": "Wed", "pop": 55, "tmax": 31.0, "tmin": 24.0, "icon": "09d"},
            {"day": "Thu", "pop": 70, "tmax": 29.0, "tmin": 23.0, "icon": "10d"},
            {"day": "Fri", "pop": 65, "tmax": 28.0, "tmin": 22.0, "icon": "09d"},
        ],
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


# ══════════════════════════════════════════════════════════════════════════
# 1. SERVE FRONTEND
# ══════════════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


# ══════════════════════════════════════════════════════════════════════════
# 2. WEATHER API — /api/weather?lat=12.97&lon=77.59
# ══════════════════════════════════════════════════════════════════════════
@app.route("/api/weather")
def get_weather():
    lat = request.args.get("lat", "12.97")
    lon = request.args.get("lon", "77.59")

    if not has_owm_key():
        return jsonify(demo_weather_data(lat, lon))

    # Current weather
    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?lat={lat}&lon={lon}&appid={OWM_API_KEY}&units=metric"
    )
    r = requests.get(url, timeout=8)
    if r.status_code != 200:
        return jsonify(demo_weather_data(lat, lon))
    data = r.json()

    # Forecast (5-day / 3h)
    fc_url = (
        f"https://api.openweathermap.org/data/2.5/forecast"
        f"?lat={lat}&lon={lon}&appid={OWM_API_KEY}&units=metric&cnt=40"
    )
    fc_r = requests.get(fc_url, timeout=8)
    forecast = []
    if fc_r.status_code == 200:
        raw = fc_r.json().get("list", [])
        # Group by day → take max rain prob per day
        from collections import defaultdict
        daily = defaultdict(list)
        for entry in raw:
            day = datetime.fromtimestamp(entry["dt"]).strftime("%a")
            rain_prob = entry.get("pop", 0) * 100          # percent
            temp_max  = entry["main"]["temp_max"]
            temp_min  = entry["main"]["temp_min"]
            icon      = entry["weather"][0]["icon"]
            daily[day].append({"pop": rain_prob, "tmax": temp_max, "tmin": temp_min, "icon": icon})
        for day, items in list(daily.items())[:5]:
            forecast.append({
                "day": day,
                "pop": round(max(i["pop"] for i in items), 1),
                "tmax": round(max(i["tmax"] for i in items), 1),
                "tmin": round(min(i["tmin"] for i in items), 1),
                "icon": items[0]["icon"],
            })

    return jsonify({
        "city": data["name"],
        "lat": lat, "lon": lon,
        "temp": round(data["main"]["temp"], 1),
        "feels_like": round(data["main"]["feels_like"], 1),
        "humidity": data["main"]["humidity"],
        "wind_speed": round(data["wind"]["speed"] * 3.6, 1),   # m/s → km/h
        "wind_deg": data["wind"].get("deg", 0),
        "description": data["weather"][0]["description"].title(),
        "icon": data["weather"][0]["icon"],
        "rain_1h": data.get("rain", {}).get("1h", 0),
        "pressure": data["main"]["pressure"],
        "uv_index": None,   # requires separate OWM One Call API
        "forecast": forecast,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })


# ══════════════════════════════════════════════════════════════════════════
# 2.1 OPENWEATHERMAP TILE PROXY — /api/owm-tile/<layer>/<z>/<x>/<y>.png

OWM_TILE_LAYERS = {
    "rain": "precipitation_new",
    "wind": "wind_new",
    "temp": "temp_new",
    "cloud": "clouds_new",
}

@app.route("/api/owm-tile/<layer>/<int:z>/<int:x>/<int:y>.png")
def owm_tile(layer, z, x, y):
    tile_name = OWM_TILE_LAYERS.get(layer)
    if not tile_name:
        return Response("", status=404)

    appid = OWM_API_KEY if has_owm_key() else "demo"
    url = (
        f"https://tile.openweathermap.org/map/{tile_name}/{z}/{x}/{y}.png"
        f"?appid={appid}"
    )
    response = requests.get(url, timeout=10)
    if response.status_code == 200:
        return Response(response.content, content_type="image/png")

    return Response("", status=response.status_code)


# ══════════════════════════════════════════════════════════════════════════
# 3. AI CHAT — /api/chat  (POST JSON: {message, language, crop, lat, lon})
# ══════════════════════════════════════════════════════════════════════════
@app.route("/api/chat", methods=["POST"])
def chat():
    body     = request.json or {}
    message  = body.get("message", "")
    language = body.get("language", "en")
    crop     = body.get("crop", "general")
    lat      = body.get("lat", "12.97")
    lon      = body.get("lon", "77.59")

    # Fetch current weather for context
    try:
        wr = requests.get(
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={lat}&lon={lon}&appid={OWM_API_KEY}&units=metric",
            timeout=6
        ).json()
        weather_ctx = (
            f"Current weather: {wr['main']['temp']}°C, "
            f"humidity {wr['main']['humidity']}%, "
            f"wind {round(wr['wind']['speed']*3.6,1)} km/h, "
            f"condition: {wr['weather'][0]['description']}."
        )
    except Exception:
        weather_ctx = "Weather data unavailable."

    lang_map = {
        "en": "English", "kn": "Kannada", "hi": "Hindi",
        "ta": "Tamil", "te": "Telugu"
    }
    respond_in = lang_map.get(language, "English")

    system_prompt = f"""You are KrishiAI, an expert agricultural AI assistant for Indian farmers.
You provide accurate, concise, actionable advice.
Always respond in {respond_in}.
{weather_ctx}
Farmer's primary crop: {crop}.
Provide:
1. Direct answer to the question
2. Weather-based recommendation
3. Specific action with timing
Keep responses under 80 words. Use simple language farmers understand."""

    # Call Anthropic (Claude)
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    }
    payload = {
        "model": "anthropic/claude-3-haiku",
    "messages": [
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": message
        }
    ],
    "max_tokens": 300
    }
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers, json=payload, timeout=15
        )
        result = resp.json()
        ai_text = result["choices"][0]["message"]["content"]
    except Exception as e:
        ai_text = f"[Demo mode — connect OpenRouter API key for live AI responses. Error: {e}]"

    return jsonify({"reply": ai_text, "weather_context": weather_ctx})


# ══════════════════════════════════════════════════════════════════════════
# 4. CROP SCANNER — /api/scan  (POST multipart: file=<image>)
# ══════════════════════════════════════════════════════════════════════════
@app.route("/api/scan", methods=["POST"])
def scan_crop():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    img_file = request.files["file"]
    img_bytes = img_file.read()
    b64_image = base64.standard_b64encode(img_bytes).decode("utf-8")
    mime_type = img_file.content_type or "image/jpeg"

    # Send image to OpenRouter Vision
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "openai/gpt-4o",
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": """Analyze this crop image and respond ONLY with a JSON object:
{
  "crop_type": "<crop name>",
  "health_status": "Healthy | Diseased | Stressed",
  "disease_detected": "<disease name or 'None'>",
  "confidence": "<percentage 0-100>",
  "severity": "None | Mild | Moderate | Severe",
  "recommended_action": "<specific treatment or action>",
  "urgency": "Low | Medium | High | Critical"
}
No explanation, only valid JSON."""
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{b64_image}"
                    }
                }
            ]
        }],
        "max_tokens": 400
    }
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers, json=payload, timeout=20
        )
        result = resp.json()
        raw = result["choices"][0]["message"]["content"]
        # Strip any accidental markdown fences
        raw = raw.strip().lstrip("```json").rstrip("```").strip()
        result = json.loads(raw)
    except Exception as e:
        # Fallback demo result
        result = {
            "crop_type": "Unable to determine",
            "health_status": "Analysis failed",
            "disease_detected": "N/A",
            "confidence": "0",
            "severity": "N/A",
            "recommended_action": f"API error: {e}. Please ensure OpenRouter key is set.",
            "urgency": "N/A",
        }

    return jsonify(result)


# ══════════════════════════════════════════════════════════════════════════
# 5. SMART ALERTS — /api/alerts?lat=12.97&lon=77.59&crop=tomato
# ══════════════════════════════════════════════════════════════════════════
@app.route("/api/alerts")
def get_alerts():
    lat  = request.args.get("lat", "12.97")
    lon  = request.args.get("lon", "77.59")
    crop = request.args.get("crop", "general")

    # Fetch weather
    try:
        if not has_owm_key():
            raise ValueError("No OpenWeatherMap API key configured")
        wr = requests.get(
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={lat}&lon={lon}&appid={OWM_API_KEY}&units=metric",
            timeout=8
        ).json()
        temp     = wr["main"]["temp"]
        humidity = wr["main"]["humidity"]
        wind_kmh = wr["wind"]["speed"] * 3.6
        rain_1h  = wr.get("rain", {}).get("1h", 0)
    except Exception:
        temp = 35.0
        humidity = 72
        wind_kmh = 18.0
        rain_1h = 0

    alerts = []

    # Rule-based alert engine
    if rain_1h > 10 or humidity > 85:
        alerts.append({
            "type": "warning", "level": "high",
            "icon": "🌧",
            "title": "Heavy Rainfall Alert",
            "message": f"Rain intensity {rain_1h:.1f}mm/hr. Avoid field operations. Clear drainage.",
            "action": "Clear drainage channels immediately"
        })

    if humidity > 70 and temp > 28:
        if crop in ("tomato", "potato", "general"):
            alerts.append({
                "type": "disease", "level": "medium",
                "icon": "🍄",
                "title": "Fungal Disease Risk",
                "message": f"Humidity {humidity}% + {temp}°C = high fungal risk for {crop}.",
                "action": f"Apply copper fungicide before 10 AM today"
            })

    if wind_kmh > 20:
        alerts.append({
            "type": "spray", "level": "medium",
            "icon": "💨",
            "title": "Avoid Pesticide Spraying",
            "message": f"Wind speed {wind_kmh:.0f} km/h exceeds safe spraying threshold (15 km/h).",
            "action": "Postpone spraying to early morning calm conditions"
        })

    if temp > 40:
        alerts.append({
            "type": "heat", "level": "high",
            "icon": "🌡",
            "title": "Extreme Heat Warning",
            "message": f"Temperature {temp}°C. Risk of crop wilting and heat stress.",
            "action": "Irrigate immediately, apply mulch, shade nets recommended"
        })

    if temp < 15:
        alerts.append({
            "type": "cold", "level": "medium",
            "icon": "❄",
            "title": "Cold Wave Alert",
            "message": f"Temperature {temp}°C. Risk of frost damage on sensitive crops.",
            "action": "Cover seedlings, irrigate to protect from frost"
        })

    if not alerts:
        alerts.append({
            "type": "ok", "level": "low",
            "icon": "✅",
            "title": "Conditions Favorable",
            "message": "No critical weather alerts. Good time for field activities.",
            "action": "Proceed with scheduled farming activities"
        })

    return jsonify({
        "alerts": alerts,
        "weather_summary": {
            "temp": temp, "humidity": humidity,
            "wind_kmh": round(wind_kmh, 1), "rain_1h": rain_1h
        },
        "generated_at": datetime.utcnow().isoformat() + "Z"
    })


# ══════════════════════════════════════════════════════════════════════════
# 6. FARMING CALENDAR — /api/calendar?crop=tomato&lat=12.97&lon=77.59
# ══════════════════════════════════════════════════════════════════════════
@app.route("/api/calendar")
def farming_calendar():
    crop = request.args.get("crop", "tomato")
    # In production: pull real forecast + ML model for optimal dates
    # Here we return rule-based calendar
    today = datetime.now()
    month = today.month

    kharif  = month in [6,7,8,9,10]
    rabi    = month in [11,12,1,2,3]

    events = [
        {
            "event": "Optimal Sowing Window",
            "date": "May 15 – May 22",
            "type": "sowing",
            "color": "green",
            "advice": f"Weather pattern shows favorable conditions. Sow {crop} seeds."
        },
        {
            "event": "Basal Fertilizer Application",
            "date": "May 16 (morning)",
            "type": "fertilizer",
            "color": "amber",
            "advice": "Apply NPK 10:26:26 @ 50kg/acre before sowing."
        },
        {
            "event": "First Irrigation",
            "date": "May 22 (06:00 AM)",
            "type": "irrigation",
            "color": "blue",
            "advice": "Drip irrigation recommended. 4L/plant/day."
        },
        {
            "event": "Top Dressing",
            "date": "June 10",
            "type": "fertilizer",
            "color": "amber",
            "advice": "Apply Urea 30kg/acre when plant is 30 days old."
        },
        {
            "event": "Pest Scouting",
            "date": "Every Monday",
            "type": "pest",
            "color": "red",
            "advice": "Check for aphids, whitefly, and leaf miner weekly."
        },
        {
            "event": "Estimated Harvest",
            "date": "August 28 – September 10",
            "type": "harvest",
            "color": "green",
            "advice": f"Expected yield for {crop}: 18–24 tons/acre under optimal conditions."
        },
    ]
    return jsonify({"crop": crop, "season": "Kharif" if kharif else "Rabi", "events": events})


# ══════════════════════════════════════════════════════════════════════════
# 7. DISEASE PREDICTION — /api/disease?crop=tomato&temp=35&humidity=72&rain=15
# ══════════════════════════════════════════════════════════════════════════
@app.route("/api/disease")
def predict_disease():
    crop     = request.args.get("crop", "tomato")
    temp     = float(request.args.get("temp", 35))
    humidity = float(request.args.get("humidity", 72))
    rain     = float(request.args.get("rain", 0))

    # Rule-based risk engine (replace with ML model in production)
    risks = []

    if crop in ("tomato", "potato"):
        # Late blight: cool + wet
        if humidity > 80 and temp < 22:
            risks.append({"disease": "Late Blight (Phytophthora)", "risk": "High", "prob": 82,
                "conditions": f"Humidity {humidity}% + Temp {temp}°C", "action": "Apply metalaxyl + mancozeb immediately"})
        # Early blight: warm + humid
        if humidity > 65 and 25 < temp < 35:
            risks.append({"disease": "Early Blight (Alternaria)", "risk": "Medium", "prob": 64,
                "conditions": f"Humidity {humidity}% + Temp {temp}°C", "action": "Apply chlorothalonil 2g/L"})
        # Leaf curl: hot + dry + whitefly season
        if temp > 32 and humidity < 60:
            risks.append({"disease": "Tomato Leaf Curl Virus", "risk": "Medium", "prob": 55,
                "conditions": f"Hot {temp}°C + dry conditions favor whitefly", "action": "Apply imidacloprid, install yellow sticky traps"})

    elif crop == "rice":
        if humidity > 85 and temp > 25:
            risks.append({"disease": "Rice Blast (Pyricularia)", "risk": "High", "prob": 78,
                "conditions": f"High humidity {humidity}%", "action": "Apply tricyclazole 6g/L"})
        if rain > 20 and temp > 28:
            risks.append({"disease": "Bacterial Leaf Blight", "risk": "Medium", "prob": 60,
                "conditions": "Post-rainfall warm conditions", "action": "Apply copper oxychloride 3g/L"})

    elif crop == "cotton":
        if humidity > 70 and 28 < temp < 38:
            risks.append({"disease": "Cotton Bollworm", "risk": "High", "prob": 73,
                "conditions": f"Temp {temp}°C favorable for bollworm lifecycle", "action": "Set pheromone traps, apply spinosad 45% SC"})

    if not risks:
        risks.append({"disease": "No significant risk", "risk": "Low", "prob": 5,
            "conditions": "Current weather not favorable for major diseases", "action": "Continue regular monitoring"})

    return jsonify({"crop": crop, "diseases": risks,
                    "input": {"temp": temp, "humidity": humidity, "rain": rain}})


# ══════════════════════════════════════════════════════════════════════════
# 8. MARKET PRICES — /api/market?crop=tomato
# ══════════════════════════════════════════════════════════════════════════
@app.route("/api/market")
def market_prices():
    """
    In production: integrate with Agmarknet API or data.gov.in
    Demo: simulated prices
    """
    crop = request.args.get("crop", "tomato")
    base_prices = {
        "tomato": 2200, "rice": 2100, "cotton": 6800,
        "wheat": 2100, "onion": 1800, "potato": 900
    }
    base = base_prices.get(crop.lower(), 1500)
    # Simulate 7-day prices
    prices = []
    p = base
    for i in range(7):
        change = random.uniform(-0.04, 0.05)
        p = round(p * (1 + change), 0)
        prices.append({
            "date": f"May {5+i}",
            "price": int(p),
            "unit": "₹/quintal",
            "market": "APMC Bengaluru"
        })

    trend = "up" if prices[-1]["price"] > prices[0]["price"] else "down"
    diff  = prices[-1]["price"] - prices[0]["price"]

    return jsonify({
        "crop": crop,
        "current_price": prices[-1]["price"],
        "unit": "₹/quintal",
        "trend": trend,
        "change_7d": diff,
        "history": prices,
        "forecast": f"{'Prices expected to rise 5–8%' if trend == 'up' else 'Prices may soften 3–5%'} due to weather impact on supply."
    })


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"\n🌱 KrishiAI Backend running → http://localhost:{port}\n")
    print("Required env vars:")
    print("  OWM_API_KEY         — openweathermap.org (free tier)")
    print("  OPENROUTER_API_KEY  — openrouter.ai")
    print("  OPENROUTER_BASE_URL — https://openrouter.ai/api/v1 (optional)\n")
    app.run(debug=True, host="0.0.0.0", port=port)
