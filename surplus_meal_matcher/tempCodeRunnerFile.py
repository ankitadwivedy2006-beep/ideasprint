import json, os, re, sqlite3, threading, math
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "surplus_meals.db")
LOCK = threading.Lock()
HOST, PORT = "127.0.0.1", 8000
RECIPIENTS = [
    {"id": 1, "name": "Campus Volunteer Network", "kind": "Student volunteers", "distance_km": 0.8, "needed": 35, "capacity": 50},
    {"id": 2, "name": "Uday Shelter", "kind": "Community shelter", "distance_km": 2.1, "needed": 45, "capacity": 60},
    {"id": 3, "name": "Community Food Circle", "kind": "Local NGO", "distance_km": 3.4, "needed": 20, "capacity": 25},
    {"id": 4, "name": "Student Outreach Club", "kind": "Student volunteers", "distance_km": 1.3, "needed": 15, "capacity": 20},
]

def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c

# Approximate campus-local coordinates (latitude/longitude-like values). Replace with surveyed
# coordinates for a real campus. Distances are estimates, not live GPS/navigation directions.
CAMPUS_ZONES = {
    "library": (20.0000, 85.0000),
    "hostel block 1": (20.0004, 85.0002),
    "hostel block 2": (20.0007, 85.0005),
    "hostel block 3": (20.0010, 85.0008),
    "north canteen": (20.0008, 85.0000),
    "campus canteen": (20.0008, 85.0000),
    "main auditorium": (20.0012, 85.0005),
    "sports complex": (19.9995, 85.0007),
    "admin block": (20.0002, 85.0007),
    "student center": (20.0005, 85.0009),
}
WALKING_SPEED_KMH = 4.5
NEARBY_MINUTES = 5


def zone_key(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def zone_coords(value):
    return CAMPUS_ZONES.get(zone_key(value))


def walking_distance(origin, destination):
    """Return approximate straight-line-to-walking estimate for known demo campus zones."""
    a, b = zone_coords(origin), zone_coords(destination)
    if not a or not b:
        return None
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2-lat1, lon2-lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    meters = 6371000 * 2 * math.asin(min(1, math.sqrt(h)))
    # Inflate straight-line distance by 1.25 for paths, then estimate at 4.5 km/h.
    walk_meters = meters * 1.25
    minutes = max(0, int(math.ceil(walk_meters / (WALKING_SPEED_KMH * 1000 / 60))))
    return {"distance_m": int(round(walk_meters)), "walking_minutes": minutes, "within_5_minutes": minutes <= NEARBY_MINUTES}


def dispatch_alert(c, meal):
    """Persist a simulated broadcast; never sends an actual WhatsApp/SMS message."""
    try:
        remaining = (datetime.fromisoformat(meal["deadline"]) - datetime.now()).total_seconds() / 60
    except Exception:
        remaining = 60
    high_priority = remaining <= 30
    if not high_priority:
        return None
    target_groups = ["Campus Volunteer Network", "Student Outreach Club", "Nearby Food Rescue Volunteers"]
    payload = {
        "channel": "SIMULATED_WHATSAPP_SMS",
        "priority": "HIGH",
        "title": "Urgent surplus meal pickup",
        "message": f"URGENT FOOD RESCUE\n{meal['food_name']} — {meal['quantity']} servings\nPickup: {meal['location']}\nAvailable for about {max(0, int(remaining))} more minutes.\nReply/claim in the Surplus Meal Matcher demo.",
        "target_groups": target_groups,
        "meal_id": int(meal["id"]),
    }
    c.execute("INSERT INTO dispatch_history (meal_id, sent_at, priority, target_groups, payload, channel) VALUES (?,?,?,?,?,?)",
              (int(meal["id"]), datetime.now().isoformat(timespec="seconds"), "HIGH", ", ".join(target_groups), json.dumps(payload), payload["channel"]))
    return payload


def init_db():
    with LOCK:
        c = db()
        c.execute("CREATE TABLE IF NOT EXISTS meals (id INTEGER PRIMARY KEY AUTOINCREMENT, food_name TEXT NOT NULL, food_type TEXT NOT NULL, quantity INTEGER NOT NULL, location TEXT NOT NULL, deadline TEXT NOT NULL, notes TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'Available', created_at TEXT NOT NULL, zone_tag TEXT DEFAULT '')")
        c.execute("CREATE TABLE IF NOT EXISTS dispatch_history (id INTEGER PRIMARY KEY AUTOINCREMENT, meal_id INTEGER NOT NULL, sent_at TEXT NOT NULL, priority TEXT NOT NULL, target_groups TEXT NOT NULL, payload TEXT NOT NULL, channel TEXT NOT NULL, FOREIGN KEY(meal_id) REFERENCES meals(id))")
        # Safe migration for an existing SQLite database from the earlier MVP.
        columns = {row[1] for row in c.execute("PRAGMA table_info(meals)").fetchall()}
        if "zone_tag" not in columns:
            c.execute("ALTER TABLE meals ADD COLUMN zone_tag TEXT DEFAULT ''")
        c.execute("UPDATE meals SET zone_tag=location WHERE zone_tag IS NULL OR zone_tag='' ")
        if c.execute("SELECT COUNT(*) FROM meals").fetchone()[0] == 0:
            now = datetime.now()
            samples = [
                ("Vegetarian meal boxes","Vegetarian",40,"Campus Canteen",(now+timedelta(minutes=45)).isoformat(timespec="minutes"),"Packed meals; verify handling before pickup.","Available",now.isoformat(timespec="minutes")),
                ("Rice and dal portions","Vegetarian",24,"Main Auditorium",(now+timedelta(minutes=70)).isoformat(timespec="minutes"),"Event surplus; pickup at side entrance.","Available",now.isoformat(timespec="minutes")),
                ("Fruit cups","Vegan",18,"Sports Complex",(now+timedelta(minutes=30)).isoformat(timespec="minutes"),"Keep chilled where required.","Claimed",now.isoformat(timespec="minutes")),
            ]
            c.executemany("INSERT INTO meals (food_name,food_type,quantity,location,deadline,notes,status,created_at) VALUES (?,?,?,?,?,?,?,?)", samples)
        c.commit(); c.close()

def rowdict(row):
    return dict(row)

def score(meal, recipient):
    qty, need, cap = int(meal["quantity"]), recipient["needed"], recipient["capacity"]
    qty_fit = max(0, 100 - abs(qty - min(need, cap)) / max(qty, 1) * 100)
    distance = max(0, 100 - recipient["distance_km"] * 15)
    try:
        mins = (datetime.fromisoformat(meal["deadline"]) - datetime.now()).total_seconds()/60
    except Exception:
        mins = 30
    urgency = max(0, min(100, 100 - max(0, mins)*1.2))
    total = round(.45*distance + .35*qty_fit + .20*urgency)
    return {**recipient, "score": total, "reasons": [f'{recipient["distance_km"]} km away', f'capacity {cap} meals', f'needs about {need} meals']}

def analyze_surplus_text(text):
    """Local, deterministic demo assistant: extracts fields and gives explainable next steps."""
    raw = str(text or '').strip()
    if len(raw) < 12:
        raise ValueError("Describe the surplus in at least 12 characters (food, quantity, and location help).")
    if len(raw) > 1000:
        raise ValueError("Please keep the description under 1,000 characters.")
    quantity_match = re.search(r'\b(\d{1,5})\s+(?:(?:vegetarian|vegan|non-vegetarian|nonveg|meal|food)\s+){0,2}(?:meals?|portions?|boxes|plates|servings?)\b', raw, re.I)
    quantity = int(quantity_match.group(1)) if quantity_match else None
    if quantity is not None and not 1 <= quantity <= 10000:
        raise ValueError("Detected quantity must be between 1 and 10,000.")
    lower = raw.lower()
    if any(w in lower for w in ['chicken','meat','fish','beef','pork','non-veg','non veg']):
        food_type = 'Non-vegetarian'
    elif 'egg' in lower:
        food_type = 'Contains eggs'
    elif any(w in lower for w in ['vegan','fruit','salad','plant-based','plant based']):
        food_type = 'Vegan'
    else:
        food_type = 'Vegetarian' if any(w in lower for w in ['vegetarian','veg','rice','dal','roti','meal','food']) else 'Needs confirmation'
    mins_match = re.search(r'\b(15|30|45|60|90|120)\s*(?:minutes?|mins?)\b', lower)
    deadline_mins = int(mins_match.group(1)) if mins_match else (30 if any(w in lower for w in ['urgent','soon','quickly','right now']) else 60)
    location_match = re.search(r'\b(?:at|near|from|pickup at|location)\s+([A-Za-z][A-Za-z0-9 &-]{1,55}?)(?=[,.;]|\s+(?:and|available|before|for|until|with|kept|packed)\b|$)', raw, re.I)
    if location_match:
        candidate = location_match.group(1).strip()
        candidate = re.sub(r'\s+(?:vegetarian|vegan|non-vegetarian|nonveg|meal|food|boxes|portions?|meals?)\b.*$', '', candidate, flags=re.I).strip()
        location = candidate or None
    else:
        location = None
    location = location_match.group(1).strip() if location_match else None
    if location:
        location = re.sub(r'^(?:pickup at|location)\s+', '', location, flags=re.I).strip()
    actions = []
    if quantity is None: actions.append('Confirm the number of portions before publishing.')
    if not location: actions.append('Add a precise pickup location so a recipient can find the food.')
    if food_type == 'Needs confirmation': actions.append('Confirm dietary type and clearly label common allergens.')
    actions.extend(['Check safe holding temperature and preparation time.', 'Confirm a recipient and collection time before the deadline.'])
    urgency = 'High' if deadline_mins <= 30 or any(w in lower for w in ['urgent','soon','quickly','right now']) else ('Medium' if deadline_mins <= 60 else 'Low')
    return {
        'mode': 'Local demo assistant (rule-based; no external AI API)',
        'input_summary': raw,
        'extracted': {'food_type': food_type, 'quantity': quantity, 'pickup_location': location, 'availability_minutes': deadline_mins, 'urgency': urgency},
        'recommendation': 'Prioritize a nearby recipient with enough capacity and confirm collection before the food availability window closes.',
        'next_steps': actions,
        'confidence_note': 'Fields are inferred from keywords and simple patterns. Review every field; missing values are shown as unknown rather than guessed.'
    }

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))
    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body))); self.send_header("Cache-Control","no-store")
        self.end_headers(); self.wfile.write(body)
    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length","0"))
            if length > 1000000: raise ValueError("Request is too large.")
            return json.loads(self.rfile.read(length).decode() or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Invalid JSON request.")
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            try:
                with open(os.path.join(ROOT,"index.html"),"rb") as f: body=f.read()
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            except OSError: self.send_json(500,{"error":"index.html is missing."})
            return
        if u.path == "/api/meals":
            origin = parse_qs(u.query).get("origin", [""])[0].strip()
            with LOCK:
                c=db(); rows=[rowdict(r) for r in c.execute("SELECT * FROM meals ORDER BY id DESC").fetchall()]; c.close()
            for meal in rows:
                meal["proximity"] = walking_distance(origin, meal.get("zone_tag") or meal["location"]) if origin else None
            return self.send_json(200,{"meals":rows, "origin":origin})
        if u.path == "/api/stats":
            with LOCK:
                c=db()
                total=int(c.execute("SELECT COALESCE(SUM(quantity),0) FROM meals").fetchone()[0])
                saved=int(c.execute("SELECT COALESCE(SUM(quantity),0) FROM meals WHERE status IN ('Claimed','Collected')").fetchone()[0])
                available=int(c.execute("SELECT COALESCE(SUM(quantity),0) FROM meals WHERE status='Available' AND deadline > ?",(datetime.now().isoformat(timespec="minutes"),)).fetchone()[0])
                listings=int(c.execute("SELECT COUNT(*) FROM meals").fetchone()[0])
                c.close()
            return self.send_json(200,{"total_servings_logged":total,"meals_saved":saved,"estimated_co2_kg":round(saved*0.5,1),"available_servings":available,"total_listings":listings,"co2_multiplier_kg_per_serving":0.5})
        if u.path == "/api/dispatch-history":
            with LOCK:
                c=db(); rows=c.execute("SELECT h.*, m.food_name, m.quantity, m.location FROM dispatch_history h LEFT JOIN meals m ON m.id=h.meal_id ORDER BY h.id DESC LIMIT 100").fetchall(); c.close()
            out=[]
            for r in rows:
                item=rowdict(r); item["payload"]=json.loads(item["payload"]); out.append(item)
            return self.send_json(200,{"dispatches":out})
        if u.path == "/api/match":
            try: meal_id=int(parse_qs(u.query).get("meal_id",[""])[0])
            except ValueError: return self.send_json(400,{"error":"Select a valid meal listing."})
            with LOCK:
                c=db(); r=c.execute("SELECT * FROM meals WHERE id=?",(meal_id,)).fetchone(); c.close()
            if not r: return self.send_json(404,{"error":"Meal listing not found."})
            meal=rowdict(r)
            if meal["status"]!="Available" or datetime.fromisoformat(meal["deadline"])<=datetime.now():
                return self.send_json(400,{"error":"This meal is no longer available for matching."})
            matches=sorted([score(meal,x) for x in RECIPIENTS],key=lambda x:x["score"],reverse=True)
            return self.send_json(200,{"meal":meal,"matches":matches})
        return self.send_json(404,{"error":"Endpoint not found."})
    def do_POST(self):
        if urlparse(self.path).path == "/api/ai-triage":
            try:
                d = self.read_json()
                return self.send_json(200, {"result": analyze_surplus_text(d.get("description", ""))})
            except ValueError as e:
                return self.send_json(400, {"error": str(e)})
            except Exception:
                return self.send_json(500, {"error": "The local assistant could not analyze this description. Please try again."})
        if urlparse(self.path).path!="/api/meals": return self.send_json(404,{"error":"Endpoint not found."})
        try:
            d=self.read_json(); name=str(d.get("food_name","")).strip(); typ=str(d.get("food_type","")).strip()
            loc=str(d.get("location","")).strip(); notes=str(d.get("notes","")).strip()
            q=d.get("quantity"); mins=d.get("deadline_minutes",45)
            if not name or len(name)>80: raise ValueError("Meal name is required (maximum 80 characters).")
            if not loc or len(loc)>100: raise ValueError("Pickup location is required (maximum 100 characters).")
            if typ not in {"Vegetarian","Vegan","Contains eggs","Non-vegetarian"}: raise ValueError("Choose a valid food type.")
            if isinstance(q,bool) or not isinstance(q,int) or not 1<=q<=10000: raise ValueError("Quantity must be a whole number from 1 to 10,000.")
            if mins not in [15,30,45,60,120]: raise ValueError("Choose a valid availability window.")
            now=datetime.now(); deadline=(now+timedelta(minutes=mins)).isoformat(timespec="minutes")
            with LOCK:
                c=db(); cur=c.execute("INSERT INTO meals (food_name,food_type,quantity,location,deadline,notes,status,created_at,zone_tag) VALUES (?,?,?,?,?,?,?,?,?)",(name,typ,q,loc,deadline,notes[:200],"Available",now.isoformat(timespec="minutes"),loc))
                r=c.execute("SELECT * FROM meals WHERE id=?",(cur.lastrowid,)).fetchone(); meal=rowdict(r)
                dispatch=dispatch_alert(c, meal)
                c.commit(); c.close()
            return self.send_json(201,{"meal":meal,"dispatch":dispatch})
        except ValueError as e: return self.send_json(400,{"error":str(e)})
        except Exception: return self.send_json(500,{"error":"Could not save listing. Please try again."})
    def do_PATCH(self):
        p=urlparse(self.path).path.strip("/").split("/")
        if len(p)!=4 or p[:2]!=["api","meals"] or p[3]!="status": return self.send_json(404,{"error":"Endpoint not found."})
        try:
            mid=int(p[2]); status=self.read_json().get("status")
            if status not in {"Claimed","Collected"}: raise ValueError("Status must be Claimed or Collected.")
            with LOCK:
                c=db(); r=c.execute("SELECT * FROM meals WHERE id=?",(mid,)).fetchone()
                if not r: c.close(); return self.send_json(404,{"error":"Meal listing not found."})
                if status=="Claimed" and r["status"]!="Available": c.close(); return self.send_json(400,{"error":"Only available meals can be claimed."})
                if status=="Collected" and r["status"]!="Claimed": c.close(); return self.send_json(400,{"error":"Only claimed meals can be collected."})
                c.execute("UPDATE meals SET status=? WHERE id=?",(status,mid)); c.commit()
                updated=c.execute("SELECT * FROM meals WHERE id=?",(mid,)).fetchone(); c.close()
            return self.send_json(200,{"meal":rowdict(updated)})
        except ValueError as e: return self.send_json(400,{"error":str(e)})
        except Exception: return self.send_json(500,{"error":"Could not update status. Please try again."})

if __name__=="__main__":
    init_db()
    server=ThreadingHTTPServer((HOST,PORT),Handler)
    print("\nSurplus Meal Matcher is running!")
    print(f"Open http://{HOST}:{PORT} in your browser")
    print("Press Ctrl+C to stop.\n")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nServer stopped.")
    finally: server.server_close()
