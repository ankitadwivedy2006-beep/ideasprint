# Surplus Meal Matcher — Hackathon MVP

Uses Python's standard library + SQLite. No pip packages, API keys, or internet connection required after files are present.

## Run in VS Code (Windows)

1. Extract the ZIP.
2. Open the `surplus_meal_matcher` folder in VS Code.
3. Choose Terminal → New Terminal.
4. Check Python: `python --version` (or `py --version`).
5. Run `python app.py` (or `py app.py`).
6. Open http://127.0.0.1:8000 in your browser.
7. Keep the terminal open. Press Ctrl+C to stop.

## Demo flow

1. Add a new meal listing.
2. Click Find matches.
3. Explain the top recipient and weighted score.
4. Copy the generated alert (it does not send SMS).
5. Mark the meal Claimed, then Collected.

## Be accurate in your pitch

Real: Python server, SQLite persistence, input validation, recipient ranking, in-app alert generation, and collection status transitions.

Demo logic: recipient distances, capacities, and needs are illustrative sample data. Matching is a transparent weighted heuristic, not a trained machine-learning model.

Not implemented: live GPS, real recipient discovery, real SMS, login/authentication, or food-safety verification.

The database file `surplus_meals.db` is created automatically. Delete it with the server stopped to reset sample data.


## AI-style surplus triage (local demo)

Use the **AI surplus triage assistant** to enter a plain-language surplus description. The local rule-based parser returns structured food type, quantity, location, availability window, urgency, and recommended next steps. It uses Python regular expressions and keyword rules, not a live LLM or external API. The result is shown in the interface and can prefill detected fields in the meal form; review all fields before saving.

API endpoint: `POST /api/ai-triage` with JSON `{ "description": "..." }`. It validates input length and returns structured JSON or a clear 400 error. No MCP server is needed for this local single-purpose workflow; MCP could later expose this analysis function as a tool to an LLM agent.

## Campus walking-time estimates

Enter a current campus zone (for example `Library`, `Hostel Block 3`, or `North Canteen`) and choose **Calculate walking times**. Python estimates walking minutes using a Haversine distance calculation, a 1.25 path factor, and a 4.5 km/h walking pace. Listings estimated at five minutes or less are highlighted. The named zone coordinates are illustrative campus-demo coordinates and should be replaced with real campus coordinates before deployment; this is not GPS or turn-by-turn navigation.

## Simulated priority dispatch + impact analytics

Publishing a meal with 30 minutes or less until its deadline automatically writes a formatted high-priority alert payload to the SQLite `dispatch_history` table, targeted to sample volunteer groups. The **Dispatch History** section shows timestamps, groups, and payloads. This is simulation only: no WhatsApp/SMS provider is contacted. `/api/stats` dynamically calculates total servings logged, claimed/collected servings, and estimated CO₂ savings at 0.5 kg per claimed/collected serving. The dashboard refreshes these values after a listing is created or a status changes.
