"""Build Documentation/kenya_maize_api_guide.docx.

    pip install python-docx        # the only dependency, not in requirements.txt
    python scripts/build_api_guide_docx.py

The guide is the handover document for the frontend team and whoever deploys
the service. It is generated rather than hand-edited so it can be kept in step
with the code: when an endpoint, an environment variable or a lever changes,
edit this script and rerun it rather than editing the .docx.

Figures quoted in the document come from data/models/district_v2/metadata.json,
data/api/lever_curves.json and api/config.py. If you change any of those, check
Part 2 §5 (environment variables), Part 4 §3 (the levers) and Part 4 §4
(measured accuracy) still match.
"""
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

INK = RGBColor(0x1A, 0x1A, 0x1A)
BLUE = RGBColor(0x1F, 0x4E, 0x79)
TEAL = RGBColor(0x0E, 0x6B, 0x63)
RED = RGBColor(0xA6, 0x1B, 0x29)
GREY = RGBColor(0x59, 0x59, 0x59)

doc = Document()

# --- base styles ------------------------------------------------------------
normal = doc.styles["Normal"]
normal.font.name = "Calibri"
normal.font.size = Pt(10.5)
normal.paragraph_format.space_after = Pt(7)
normal.paragraph_format.line_spacing = 1.12

for name, size, colour, before in (("Title", 26, BLUE, 0), ("Heading 1", 17, BLUE, 22),
                                   ("Heading 2", 13.5, TEAL, 15), ("Heading 3", 11.5, INK, 11)):
    st = doc.styles[name]
    st.font.name = "Calibri"
    st.font.size = Pt(size)
    st.font.color.rgb = colour
    st.font.bold = True
    st.paragraph_format.space_before = Pt(before)
    st.paragraph_format.space_after = Pt(5)
    st.paragraph_format.keep_with_next = True

for section in doc.sections:
    section.left_margin = section.right_margin = Inches(0.9)
    section.top_margin = section.bottom_margin = Inches(0.8)


# --- helpers ----------------------------------------------------------------
def shade(element, hexcolour):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hexcolour)
    element.append(shd)


def para(text="", bold=False, italic=False, size=None, colour=None, style=None,
         space_after=None):
    p = doc.add_paragraph(style=style)
    run = p.add_run(text)
    run.bold, run.italic = bold, italic
    if size:
        run.font.size = Pt(size)
    if colour:
        run.font.color.rgb = colour
    if space_after is not None:
        p.paragraph_format.space_after = Pt(space_after)
    return p


def rich(*parts, style=None, space_after=None):
    """parts: (text, bold, italic, mono, colour) tuples or plain strings."""
    p = doc.add_paragraph(style=style)
    for part in parts:
        if isinstance(part, str):
            part = (part,)
        text = part[0]
        bold = part[1] if len(part) > 1 else False
        italic = part[2] if len(part) > 2 else False
        mono = part[3] if len(part) > 3 else False
        colour = part[4] if len(part) > 4 else None
        run = p.add_run(text)
        run.bold, run.italic = bold, italic
        if mono:
            run.font.name = "Consolas"
            run.font.size = Pt(9.5)
        if colour:
            run.font.color.rgb = colour
    if space_after is not None:
        p.paragraph_format.space_after = Pt(space_after)
    return p


def bullet(*parts):
    return rich(*parts, style="List Bullet")


def number(*parts):
    return rich(*parts, style="List Number")


def code(text, fill="F4F5F7"):
    for i, line in enumerate(text.strip("\n").split("\n")):
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(6 if i == 0 else 0)
        pf.space_after = Pt(6 if line == text.strip("\n").split("\n")[-1] else 0)
        pf.left_indent = Inches(0.16)
        pf.line_spacing = 1.0
        shade(p._p.get_or_add_pPr(), fill)
        run = p.add_run(line if line else " ")
        run.font.name = "Consolas"
        run.font.size = Pt(8.8)
    return None


def callout(label, text, fill="FFF6E5", colour=RED):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.1)
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(8)
    shade(p._p.get_or_add_pPr(), fill)
    run = p.add_run(f"{label}  ")
    run.bold = True
    run.font.color.rgb = colour
    p.add_run(text)
    return p


def table(headers, rows, widths=None, font=9.2):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ""
        run = hdr[i].paragraphs[0].add_run(h)
        run.bold = True
        run.font.size = Pt(font)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        shade(hdr[i]._tc.get_or_add_tcPr(), "1F4E79")
    for r, row in enumerate(rows):
        cells = t.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            p.paragraph_format.space_after = Pt(2)
            # `code` marks a cell monospace by wrapping it in backticks
            mono = isinstance(value, str) and value.startswith("`") and value.endswith("`")
            run = p.add_run(value.strip("`") if mono else str(value))
            run.font.size = Pt(font)
            if mono:
                run.font.name = "Consolas"
                run.font.size = Pt(8.6)
            if r % 2 == 1:
                shade(cells[i]._tc.get_or_add_tcPr(), "F4F6F9")
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def rule():
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    pPr = p._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:color"), "C9D2DD")
    borders.append(bottom)
    pPr.append(borders)


def page_break():
    doc.add_page_break()


# ===========================================================================
# COVER
# ===========================================================================
t = doc.add_paragraph()
t.alignment = WD_ALIGN_PARAGRAPH.LEFT
run = t.add_run("Kenya Maize Yield & Recommendation API")
run.font.size = Pt(26)
run.bold = True
run.font.color.rgb = BLUE

sub = doc.add_paragraph()
run = sub.add_run("Implementation, deployment and integration guide")
run.font.size = Pt(13)
run.font.color.rgb = TEAL
sub.paragraph_format.space_after = Pt(14)

table(["", ""], [
    ["Audience", "Frontend engineers building the client, and whoever deploys the service"],
    ["Service", "FastAPI application in api/, version 0.1.0"],
    ["Model", "district_v2 — 5-member ensemble, trained on 23,674 Kenyan maize plots, 2016–2020"],
    ["Recommendation layer", "8 fitted lever response curves, 23,617 plots, 2016–2020"],
    ["Repository", "yield-prediction-and-agronomic-recomendation"],
    ["Companion docs", "Documentation/api_reference.md (HTTP contract), "
                       "kenya_maize_district_model_deployment.md (what the model can do), "
                       "kenya_maize_recommendation_layer.md (how the advice is estimated)"],
], widths=[1.5, 5.4])

para()
para("How to read this document", bold=True, size=12, colour=BLUE)
rich(("Part 1", True), " tells the story of how the service was built, so both teams share "
     "the same mental model. ", ("Part 2", True), " is everything the deployment engineer "
     "needs and nothing else. ", ("Part 3", True), " is everything the frontend team needs "
     "and nothing else. ", ("Part 4", True), " holds the reference tables both will keep "
     "coming back to. ", ("Part 5", True), " says honestly what is not built yet.")
rich("You do not need to read the parts that are not yours. Each is written to stand alone.")

callout("Start here.",
        "If you only have five minutes: the service is a normal FastAPI app started with "
        "one uvicorn command. It has two main endpoints — /api/v1/predict (how much will "
        "this yield?) and /api/v1/recommend (what should this farmer change?). The one "
        "thing that will trip you up is that the trained model file is NOT in the git "
        "repository, because it is too large. Part 2 §4 explains the three ways to supply "
        "it. Everything else has a working default.",
        fill="E8F1F8", colour=BLUE)

page_break()

# ===========================================================================
# PART 1 — HOW IT WAS BUILT
# ===========================================================================
doc.add_heading("Part 1 · What this service is, and how it came to be", 1)

doc.add_heading("1.1  In one paragraph", 2)
rich("One Acre Fund surveys smallholder maize farmers in Kenya every season, physically "
     "weighing harvests. Five seasons of that survey — 23,674 plots across 51 districts, "
     "2016 to 2020 — were cleaned, turned into features, and used to train a yield model. "
     "This service puts that work behind an HTTP API so a field agent's phone or laptop can "
     "ask two questions in under a second: ",
     ("how much is this plot likely to yield", True),
     ", and ", ("what should this farmer change to yield more", True),
     ". No data scientist in the loop.")

doc.add_heading("1.2  The two things it answers", 2)
table(["Question", "Endpoint", "What comes back", "Needs the model file?"], [
    ["How much will this yield?", "`POST /api/v1/predict`",
     "A predicted yield in kg per hectare, with an uncertainty band, for each plot and for "
     "the district average of those plots.", "Yes"],
    ["What should this farmer change?", "`POST /api/v1/recommend`",
     "A ranked list of changes — planting date, fertiliser, seed — each with the expected "
     "yield gain in kg/ha, a confidence band, and the evidence behind it.", "No"],
], widths=[1.5, 1.5, 3.0, 0.95])

rich("That last column matters for deployment and is explained in ", ("Part 2 §4", True),
     ". The recommendation layer was deliberately built so it does not depend on the large "
     "model file. If the file is missing, recommendations still work perfectly; only "
     "predictions stop.")

doc.add_heading("1.3  How it was built, in order", 2)
rich("Five stages. Each produced an artefact the next stage consumes. Knowing this order "
     "makes every error message in Part 2 §9 obvious.")

doc.add_heading("Stage 1 — Clean the survey", 3)
rich("The raw One Acre Fund workbook has 81,411 rows across seven countries and four crops, "
     "and it is messy in specific ways: a DAP fertiliser rate reaching 103,704 kg/ha, plot "
     "sizes of 462 hectares for smallholder fields. ",
     ("scripts/clean_kenya_maize.py", False, False, True),
     " fixes the plot-size denominators first (because every per-hectare column is derived "
     "from them), caps implausible rates, and narrows to Kenyan maize.")

doc.add_heading("Stage 2 — Build features, including weather", 3)
rich(("scripts/build_features.py", False, False, True))
rich("and ", ("scripts/build_weather_features.py", False, False, True),
     " turn cleaned survey rows into the 143 columns the model consumes. That includes 89 "
     "rainfall and temperature columns fetched from Google Earth Engine and matched to each "
     "plot's location and season. ",
     ("This is why a caller cannot simply post 143 numbers", True),
     " — most of them are monthly climate variables nobody at a farm gate knows.")

doc.add_heading("Stage 3 — Train the model", 3)
rich("Six notebooks worked from a baseline to the deployed ensemble. ",
     ("scripts/train_district_model.py", False, False, True),
     " reproduces the final one: five members (ExtraTrees, LightGBM, XGBoost, RandomForest "
     "and a neural network) averaged together, validated ",
     ("out of time", False, True),
     " — fitted on earlier seasons, scored on a season it had never seen. The output is ",
     ("pipeline.joblib", False, False, True),
     ", roughly 90–125 MB, plus a small ", ("metadata.json", False, False, True),
     " carrying its measured accuracy.")

doc.add_heading("Stage 4 — Make a lean request possible", 3)
rich("A field agent knows maybe a dozen facts about a plot, not 143. ",
     ("scripts/build_api_defaults.py", False, False, True),
     " distils the feature file into ", ("data/api/feature_defaults.json", False, False, True),
     " — the median and most common value of every column, for every district and every "
     "district-season. When a request omits something, the service fills it from ",
     ("that district's own history", True),
     ", not a national average. This is what lets a caller send five fields and still get a "
     "sound prediction.")
callout("Measured, not assumed.",
        "Scoring all 6,190 real 2020 plots through this lean path and comparing against the "
        "pipeline given the true engineered rows: plot-level correlation 0.988, "
        "district-level 0.998. Filling from district history does not meaningfully degrade "
        "the prediction.", fill="EAF5EE", colour=TEAL)

doc.add_heading("Stage 5 — Fit the recommendation curves", 3)
rich("The report originally planned to generate advice by searching over the trained "
     "model's predictions. That turned out to be the wrong tool: per-plot accuracy is weak "
     "(R² ≈ 0.16), and about 60% of what accuracy exists comes from simply knowing which ",
     ("district", False, True), " a farm is in — which no farmer can change. Searching that "
     "surface returns confident-looking advice assembled largely from noise.")
rich("So ", ("scripts/build_lever_curves.py", False, False, True),
     " estimates something different and easier: not ",
     ("what will this plot yield", False, True), " but ",
     ("how does yield move when this one decision moves", False, True),
     ". For each of eight controllable decisions it fits a response curve across all 23,617 "
     "plots, comparing each plot only against other plots ",
     ("in the same district in the same season", True),
     " — which strips out the altitude and weather effects that dominate raw correlations. "
     "The result is ", ("data/api/lever_curves.json", False, False, True),
     ", a 37 KB file that is committed to git.")

doc.add_heading("1.4  The files, and what each is for", 2)
table(["File or folder", "What it is", "In git?"], [
    ["`api/main.py`", "The FastAPI application. Wires the routers together and defines / and the error handlers.", "Yes"],
    ["`api/config.py`", "Every setting, read from environment variables. All have working defaults.", "Yes"],
    ["`api/schemas.py`", "The request and response shapes. This file IS the contract the frontend codes against.", "Yes"],
    ["`api/features.py`", "Turns a lean request into the 143 columns the model needs.", "Yes"],
    ["`api/registry.py`", "Loads and holds the trained model. Handles the download-on-first-use path.", "Yes"],
    ["`api/defaults.py`", "Reads the fill-value artefact.", "Yes"],
    ["`api/curves.py`", "Reads the lever curves and does the statistics behind each recommendation.", "Yes"],
    ["`api/recommend.py`", "Chooses and gates the recommendations.", "Yes"],
    ["`api/routers/`", "One file per endpoint group: predict, recommend, model, reference, health.", "Yes"],
    ["`data/api/feature_defaults.json`", "Fill values for every column, per district and season. ~1 MB.", "Yes"],
    ["`data/api/lever_curves.json`", "The eight fitted response curves. 37 KB.", "Yes"],
    ["`data/models/district_v2/metadata.json`", "The model's measured accuracy. Small.", "Yes"],
    ["`data/models/district_v2/pipeline.joblib`", "The trained model itself. 90–125 MB.",
     "NO — see Part 2 §4"],
    ["`requirements.txt`", "Runtime dependencies.", "Yes"],
    ["`Procfile`", "The one-line start command for Heroku-style platforms.", "Yes"],
    ["`tests/`", "test_api.py (prediction) and test_recommend.py (advice).", "Yes"],
], widths=[2.3, 4.0, 0.95])

page_break()

# ===========================================================================
# PART 2 — DEPLOYMENT
# ===========================================================================
doc.add_heading("Part 2 · For whoever deploys this", 1)
rich("Everything in this part is for you. The frontend team does not need it.")

doc.add_heading("2.1  What you need", 2)
bullet(("Python 3.11 or newer.", True), " The app is developed on 3.12–3.14.")
bullet(("About 2 GB of RAM.", True), " The model file is 90–125 MB on disk and expands in memory when loaded.")
bullet(("Somewhere to put a 125 MB file", True), " that is not git. Object storage, a mounted volume, or a release asset. See §4.")
bullet(("Outbound HTTPS", True), " only if you use the MODEL_URL download route.")
rich("No database. No message queue. No background workers. It is a single stateless "
     "process, so scaling is just running more copies behind a load balancer.")

doc.add_heading("2.2  Run it locally in four commands", 2)
code("""
git clone <repo> && cd yield-prediction-and-agronomic-recomendation
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.main:app --reload
""")
rich("Open ", ("http://127.0.0.1:8000/docs", False, False, True),
     " — that is the interactive documentation, generated from the code. Every endpoint can "
     "be called from that page. Point the frontend team at it on day one.")
rich("At this point ", ("/api/v1/recommend", False, False, True), " already works fully. ",
     ("/api/v1/predict", False, False, True), " will return 503 until you supply the model "
     "file. That is expected and correct.")

doc.add_heading("2.3  The two data artefacts", 2)
rich("Both are committed, so a fresh clone has them. You only rebuild them if the underlying "
     "feature files change.")
code("""
python scripts/build_api_defaults.py     # -> data/api/feature_defaults.json
python scripts/build_lever_curves.py     # -> data/api/lever_curves.json   (~1 second)
""")
rich("Both need only pandas and numpy — not the heavy model stack — so they can be rebuilt "
     "on a small machine.")

doc.add_heading("2.4  The model file — read this section carefully", 2)
callout("The single thing most likely to go wrong.",
        "pipeline.joblib is 90–125 MB, above GitHub's 100 MB file limit, so .gitignore "
        "excludes it. A fresh clone does not have it, and /api/v1/predict will return 503 "
        "with a message telling you exactly this. That is by design, not a broken build.")
rich("Three ways to supply it. Pick one.")

doc.add_heading("Option A — Bake it into the image (recommended for containers)", 3)
rich("Copy the file into the image at build time and point ", ("MODEL_DIR", False, False, True),
     " at it. Startup is then instant and there is no runtime network dependency.")
code("""
# Dockerfile sketch
COPY pipeline.joblib /srv/model/pipeline.joblib
COPY data/models/district_v2/metadata.json /srv/model/metadata.json
ENV MODEL_DIR=/srv/model
ENV MODEL_EAGER_LOAD=true
""")

doc.add_heading("Option B — Download on first use", 3)
rich("Set ", ("MODEL_URL", False, False, True),
     " to a URL serving the file. On the first prediction the service downloads it into ",
     ("MODEL_DIR", False, False, True), " and caches it there. Accepts a bare ",
     ("pipeline.joblib", False, False, True), ", a ", (".zip", False, False, True),
     ", or a ", (".tar.gz", False, False, True), ".")
code("""
MODEL_URL=https://storage.example.com/models/district_v2.tar.gz
MODEL_DIR=/var/cache/maize-model
""")
callout("Watch the first request.",
        "With this option the very first /predict call pays the download plus load time. On "
        "a platform with a request timeout that call can fail. Set MODEL_EAGER_LOAD=true so "
        "the download happens during startup instead, and use /ready (not /health) to gate "
        "traffic until it finishes.", fill="FFF6E5", colour=RED)

doc.add_heading("Option C — Mount a volume", 3)
rich("Put the file on a persistent disk or network mount and point ",
     ("MODEL_DIR", False, False, True), " at the directory. Simplest for VM deployments.")

doc.add_heading("Rebuilding it from scratch", 3)
rich("If you have the feature files, the model is reproducible in about four minutes:")
code("python scripts/train_district_model.py --out data/models/district_v2")
rich("This needs the full model stack — scikit-learn, LightGBM, XGBoost and torch — which ",
     ("requirements.txt", False, False, True), " already installs.")

doc.add_heading("2.5  Environment variables", 2)
rich("Every one has a working default. Set only what you need to change.")
table(["Variable", "Default", "What it does"], [
    ["`MODEL_DIR`", "`data/models/district_v2`", "Directory holding pipeline.joblib and metadata.json."],
    ["`MODEL_URL`", "unset", "Archive to download the model from on first use."],
    ["`MODEL_VERSION`", "the MODEL_DIR name", "Reported in every prediction response. Set it to something meaningful."],
    ["`MODEL_EAGER_LOAD`", "`false`", "Load the model during startup rather than on the first request. Set true in production."],
    ["`FEATURE_DEFAULTS_PATH`", "`data/api/feature_defaults.json`", "Where the fill values live."],
    ["`LEVER_CURVES_PATH`", "`data/api/lever_curves.json`", "Where the recommendation curves live."],
    ["`MAX_PLOTS_PER_REQUEST`", "`500`", "Rejects larger batches with a 422 rather than timing out."],
    ["`MIN_LIFT_KG_PH`", "`25`", "Floor on how small a yield gain may be and still be recommended."],
    ["`MIN_LIFT_Z`", "`1.645`", "Statistical threshold a recommendation must clear. 1.645 is one-sided 95%. Raising it makes advice more cautious."],
    ["`PORT`", "`8000`", "Port to bind."],
    ["`CORS_ORIGINS`", "`*`", "Comma-separated list of allowed browser origins. TIGHTEN THIS IN PRODUCTION."],
    ["`ROOT_PATH`", "empty", "Set when served under a path prefix behind a proxy, e.g. /api."],
    ["`ENVIRONMENT`", "`development`", "Label only, echoed by /health."],
    ["`PROJECT_ROOT`", "auto-detected", "Rarely needed. Overrides where the app looks for data/."],
    ["`LOG_LEVEL`", "`INFO`", "Standard Python log levels."],
], widths=[1.85, 1.5, 3.6])

doc.add_heading("2.6  Running it in production", 2)
rich("The repository ships a ", ("Procfile", False, False, True),
     " for Heroku-style platforms:")
code("web: uvicorn api.main:app --host 0.0.0.0 --port $PORT")
rich("For anything else, the same command with your own worker count. Because the process is "
     "stateless, more workers is the whole scaling story — but note each worker loads its "
     "own copy of the model, so multiply your memory estimate by the worker count.")
code("""
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
""")
rich("A minimal production environment:")
code("""
ENVIRONMENT=production
MODEL_DIR=/srv/model
MODEL_VERSION=district_v2
MODEL_EAGER_LOAD=true
CORS_ORIGINS=https://agents.oneacrefund.org
LOG_LEVEL=INFO
""")

doc.add_heading("2.7  Health checks — wire these to the right things", 2)
table(["Endpoint", "Answers 200 when", "Use it for"], [
    ["`GET /health`", "The process is alive. Answers even with no model at all, reporting "
     "status \"ok\" or \"degraded\".", "Liveness probe / platform health check. Restarting "
     "the process will not fix a missing model file, so do not gate on more than this."],
    ["`GET /ready`", "The fill values are present AND the model loads. Otherwise 503.",
     "Readiness probe / load-balancer gate. This is what decides whether an instance should "
     "receive prediction traffic."],
], widths=[1.2, 2.7, 3.05])
callout("If you only use /ready, recommendations go dark too.",
        "/ready requires the model. If your load balancer removes instances that fail /ready, "
        "a missing model file takes the whole service offline — including /api/v1/recommend, "
        "which does not need the model and would otherwise still be serving. If advice "
        "matters more than prediction in your rollout, gate on /health and let /predict "
        "return its own 503.")

doc.add_heading("2.8  What still works when something is missing", 2)
rich("The service is built to degrade rather than fail, so a broken deployment can be "
     "diagnosed instead of just being down.")
table(["Missing", "Still works", "Returns 503"], [
    ["pipeline.joblib\n(the model file)",
     "/api/v1/recommend, /api/v1/model/summary, all /reference endpoints, /health, /",
     "/api/v1/predict, /ready"],
    ["lever_curves.json",
     "everything else, including /predict",
     "/api/v1/recommend, /api/v1/reference/levers"],
    ["feature_defaults.json",
     "/, /health (as \"degraded\"), /api/v1/model/summary, /api/v1/reference/levers",
     "/predict, /recommend, /reference/districts, /reference/seed-types, /reference/input-schema"],
], widths=[1.5, 3.1, 2.35])
rich("Every 503 message names the file and the command that produces it. Read the message "
     "before debugging.")

doc.add_heading("2.9  Troubleshooting", 2)
table(["Symptom", "Cause", "Fix"], [
    ["503 \"no pipeline.joblib in …\" on /predict",
     "The model file is not where MODEL_DIR points.",
     "Supply it by one of the three routes in §4, or check the path in the message."],
    ["503 \"feature defaults not found\"",
     "data/api/feature_defaults.json is missing.",
     "`python scripts/build_api_defaults.py`"],
    ["503 \"lever curves not found\"",
     "data/api/lever_curves.json is missing.",
     "`python scripts/build_lever_curves.py`"],
    ["503 mentioning ModuleNotFoundError: torch",
     "The shipped ensemble includes a neural-network member, so the file only unpickles with torch installed.",
     "Install the full requirements.txt, or rebuild the model with `--no-nn`."],
    ["First /predict call times out",
     "The model is downloading and loading inside the request.",
     "Set MODEL_EAGER_LOAD=true and gate traffic on /ready."],
    ["Browser console: CORS error",
     "CORS_ORIGINS does not include the frontend's origin.",
     "Set CORS_ORIGINS to the exact origin, scheme included."],
    ["Predictions look wrong after a redeploy",
     "MODEL_DIR points at a different or older artefact.",
     "Check `model_version` in any prediction response and /api/v1/model/summary."],
    ["Memory grows with each worker",
     "Each uvicorn worker loads its own copy of the model.",
     "Reduce workers, or increase the memory limit."],
], widths=[2.1, 2.4, 2.45])

doc.add_heading("2.10  Tests", 2)
code("""
pytest tests/                                    # model-dependent tests skip
MODEL_DIR=/srv/model pytest tests/               # full suite
""")
rich("40 tests pass without the model file (9 more run once it is present). ",
     ("tests/test_recommend.py", False, False, True),
     " needs no artefact at all, which makes it a good smoke test in CI.")

doc.add_heading("2.11  Keeping it healthy over time", 2)
bullet(("Retrain each survey round.", True),
       " The survey questionnaire itself changes year to year, so this is not only data "
       "drift. Regenerate the feature files, then rerun train_district_model.py, "
       "build_api_defaults.py and build_lever_curves.py — in that order.")
bullet(("Always rebuild the lever curves with the model.", True),
       " Unlike the fill values, the curves are fitted regression coefficients. They must be "
       "refit, never carried forward, when the feature file changes.")
bullet(("Watch model_version and curves_version.", True),
       " Every prediction and recommendation response carries the version that produced it. "
       "Log them.")
bullet(("Monitor the 503 rate on /predict separately from /recommend.", True),
       " They fail for entirely different reasons.")

page_break()

# ===========================================================================
# PART 3 — FRONTEND
# ===========================================================================
doc.add_heading("Part 3 · For the frontend team", 1)

doc.add_heading("3.1  The basics", 2)
table(["", ""], [
    ["Protocol", "Plain JSON over HTTP. No authentication is built in — put it behind your gateway."],
    ["Content type", "application/json for both requests and responses."],
    ["Interactive docs", "GET /docs — every endpoint is callable from that page. Use it to explore before writing code."],
    ["Machine-readable schema", "GET /openapi.json — feed this to your client generator and you will not hand-write a single type."],
    ["Service index", "GET / — lists every endpoint path. Useful as a connectivity check."],
], widths=[1.6, 5.3])
callout("Generate your types.",
        "The OpenAPI schema at /openapi.json is complete and always matches the running "
        "service. Generating a TypeScript client from it is a five-minute job and removes "
        "every opportunity to mistype a field name. Do that rather than transcribing the "
        "tables in Part 4.", fill="E8F1F8", colour=BLUE)

doc.add_heading("3.2  One form serves both endpoints", 2)
rich("The plot object is identical in both requests. Build the form once; send it to "
     "whichever endpoint the user asked for.")
rich(("Only ", True), ("district", True, False, True), (" is required.", True),
     " Everything else is optional. Anything the user leaves blank is filled from that "
     "district's own history, so a five-field form still returns a sound answer. Never block "
     "submission because a fertiliser field is empty.")

doc.add_heading("Build the form from the API, not from this document", 3)
table(["Call this once at startup", "To populate"], [
    ["`GET /api/v1/reference/input-schema`",
     "Every accepted field with its type, unit, allowed values and the range actually "
     "observed in the survey. Enough to build and validate the whole form."],
    ["`GET /api/v1/reference/districts`",
     "The 51 district names the model knows, plus the available seasons. Use it for a "
     "typeahead. Matching is case-insensitive."],
    ["`GET /api/v1/reference/seed-types`",
     "Seed categories, the full variety list, primary varieties and intercrop species."],
    ["`GET /api/v1/reference/levers`",
     "The eight things the recommendation engine can advise on, with labels and units. Use "
     "it to build a lever filter, or to label results."],
], widths=[2.3, 4.6])
rich("Cache these. They change only when the model is retrained, and you can detect that "
     "from ", ("model_version", False, False, True), " or ",
     ("curves_version", False, False, True), " in any response.")

doc.add_heading("3.3  Endpoint 1 — predict a yield", 2)
rich(("POST /api/v1/predict", True, False, True))
para("Request", bold=True, size=10.5)
code("""
{
  "plots": [
    {
      "plot_id": "farm-104",
      "district": "bungoma",
      "plot_acres": 1.0,
      "seed_category": "hybrid_branded",
      "dap_kg_ph": 120,
      "can_kg_ph": 100,
      "plant_date": "2020-03-05"
    }
  ],
  "year": 2020,
  "include_plot_predictions": true
}
""")
rich("Send up to 500 plots in one call. Plots sharing a district are averaged into a "
     "district result as well as being returned individually.")

para("Response — the parts you will render", bold=True, size=10.5)
code("""
{
  "model_version": "district_v2",
  "unit": "kg/ha",
  "districts": [
    { "district": "bungoma", "predicted_mean_yield_kg_ph": 2802.7,
      "interval_low_kg_ph": 1604.8, "interval_high_kg_ph": 4000.6,
      "interval_coverage": 0.8, "n_plots": 1,
      "below_reporting_threshold": true, "district_known": true }
  ],
  "plots": [
    { "plot_id": "farm-104", "predicted_yield_kg_ph": 2802.7,
      "inputs_supplied_count": 25, "used_season_weather": true,
      "district_known": true, "warnings": [] }
  ],
  "warnings": ["..."],
  "accuracy_note": "..."
}
""")

para("Four flags you must honour in the UI", bold=True, size=10.5)
table(["Field", "When true", "What to show"], [
    ["`district_known`", "false — the model never saw this district in training.",
     "A visible caution. Error for such districts is roughly double."],
    ["`below_reporting_threshold`", "Fewer than 20 plots back that district average.",
     "Label the district figure as indicative. It is largely sampling noise."],
    ["`used_season_weather`", "false — no observed weather for that district-season.",
     "A quiet note that long-run climate averages stood in."],
    ["`inputs_supplied_count`", "Always present. Counts the model columns your request "
     "actually determined.", "Optional, but a good \"how complete is this profile\" meter."],
], widths=[1.75, 2.5, 2.7])

callout("Be honest about per-plot numbers.",
        "The district average is the number this model was validated on: out-of-time R² "
        "0.22–0.59, MAE 300–700 kg/ha. Individual plot predictions are much weaker "
        "(R² ≈ 0.16) because most plot-to-plot variation comes from soil and management "
        "detail the survey never captured. Show the plot figure if a per-farm screen needs "
        "one, but present it as indicative, with its interval, never as a forecast. The "
        "accuracy_note field carries this wording — display it.")

doc.add_heading("3.4  Endpoint 2 — recommend changes", 2)
rich(("POST /api/v1/recommend", True, False, True))
rich("This is the field-agent screen. It takes one plot, not a list.")

para("Simplest possible request", bold=True, size=10.5)
code("""
{ "plot": { "district": "bungoma" } }
""")
rich("That works. Everything is filled from the district's median practice, and every "
     "recommendation comes back flagged ", ("current_is_assumed: true", False, False, True),
     ". The more the user tells you, the more specific the advice.")

para("A fuller request", bold=True, size=10.5)
code("""
{
  "plot": {
    "district": "bungoma", "plant_date_doy": 110, "seed_category": "local",
    "dap_kg_ph": 0, "can_kg_ph": 0, "plot_acres": 1.0
  },
  "year": 2020
}
""")
callout("There is no money in this endpoint.",
        "Every figure is in kilograms of maize per hectare. There are no costs, no prices "
        "and no budget, because the survey behind the model carries no fertiliser or "
        "farm-gate price data — and advice ranked on invented prices would reorder the whole "
        "list. If your product needs cost or affordability, hold prices in the client and "
        "compute it there, where someone actually knows the local figures. The request model "
        "rejects unknown fields, so sending budget or prices returns a 422 rather than being "
        "silently ignored.",
        fill="E8F1F8", colour=BLUE)

para("Optional request fields", bold=True, size=10.5)
table(["Field", "Default", "Effect"], [
    ["`levers`", "all eight", "Restrict to named levers, e.g. only what the agent can supply today."],
    ["`min_lift_kg_ph`", "`25`", "Hide gains smaller than this. Raise it for a shorter, punchier list."],
    ["`min_support`", "`200`", "How many comparable real plots a recommendation must rest on. Raise it for a more conservative list."],
    ["`include_curve`", "`false`", "Also return each lever's full fitted curve — for plotting a response chart, not for advice."],
], widths=[1.6, 1.15, 4.15])

para("Response", bold=True, size=10.5)
code("""
{
  "district": "bungoma", "district_known": true, "year": 2020,
  "curves_version": "2026-09-08T…",
  "baseline_predicted_yield_kg_ph": null,
  "recommendations": [
    {
      "rank": 1,
      "lever": "hybrid_seed",
      "label": "Share of seed that is hybrid",
      "action": "plant a larger share of hybrid seed — 0.95 share of seed (0-1) (now 0)",
      "current_value": 0.0, "recommended_value": 0.95,
      "current_is_assumed": false,
      "expected_lift_kg_ph": 586.4,
      "lift_low_kg_ph": 545.8, "lift_high_kg_ph": 627.0,
      "lift_share_of_district_yield": 0.197,
      "why": "The strongest within-district correlate of yield in the file…",
      "evidence": {
        "n_plots": 22809, "support_at_target": 17481,
        "confidence": "high", "t_statistic": 23.75,
        "uncontrolled_lift_kg_ph": 829.6, "control_absorbed_share": 0.293,
        "trained_to_2019_lift_kg_ph": 591.2,
        "curve_shape": "concave_increasing", "agronomically_plausible": true
      }
    }
  ],
  "bundle": {
    "levers": ["hybrid_seed", "…"],
    "total_expected_lift_kg_ph": 2447.4,
    "lift_low_kg_ph": 1806.3, "lift_high_kg_ph": 3088.5,
    "lift_share_of_district_yield": 0.821,
    "district_mean_yield_kg_ph": 2982.6,
    "note": "…"
  },
  "skipped": [ { "lever": "intercropping", "reason": "no change to this lever clears…" } ],
  "warnings": ["…"],
  "method_note": "…", "causal_note": "…"
}
""")

para("How to lay this out", bold=True, size=10.5)
number(("The list is already ranked", True), " by ", ("rank", False, False, True),
       ". Render in order. Do not re-sort.")
number(("action", True, False, True),
       (" is a finished sentence.", True),
       " It already reads \"plant a larger share of hybrid seed — 0.95 (now 0)\". Show it as "
       "the headline of each card. Use ", ("label", False, False, True), " for the category "
       "chip and ", ("why", False, False, True), " for a one-line explanation.")
number(("Show the range, not just the number.", True), " ",
       ("expected_lift_kg_ph", False, False, True), " with ",
       ("lift_low_kg_ph", False, False, True), " to ",
       ("lift_high_kg_ph", False, False, True), " beside it. A gain of \"+586 kg/ha "
       "(546–627)\" is honest; \"+586 kg/ha\" alone is not.")
number(("lift_share_of_district_yield", True, False, True),
       " turns the number into something a farmer feels: \"about a 20% increase for this "
       "district\". Consider leading with it.")
number(("Everything is kg/ha.", True),
       " If your product needs cost or affordability, hold a price table in the client and "
       "compute it there. Do not present a service figure as a cost — there isn't one.")
number(("evidence.confidence", True, False, True),
       " is \"high\", \"medium\" or \"low\". Map it to a badge. Do not hide low-confidence "
       "items — show them, marked.")
number(("skipped is not an error list.", True),
       " \"No change to this lever clears the evidence threshold\" for a plot already doing "
       "the right thing is the correct answer. Render it as ",
       ("already good", False, True), ", not as a failure.")
number(("An empty recommendations array is a valid, good result.", True),
       " Show ", ("bundle.note", False, False, True),
       ", which explains it in plain words. Never show \"no results\" as an error state.")

callout("current_is_assumed is the flag that protects your users.",
        "When it is true, the user did not tell us what the plot currently does, so the "
        "comparison was made against the district's median practice. The advice then "
        "describes a typical plot in that district, not this one. Mark those cards clearly — "
        "\"based on typical practice in Bungoma\" — and use it to prompt the agent to fill in "
        "the missing field. It is the single highest-value nudge in the whole screen.")

para("The evidence block, in plain words", bold=True, size=10.5)
table(["Field", "What it means", "Suggested use"], [
    ["`confidence`", "high / medium / low, from how strong and how well-supported the estimate is.", "A coloured badge."],
    ["`support_at_target`", "How many real plots were observed doing roughly the recommended thing.", "\"Based on 17,481 similar plots\"."],
    ["`uncontrolled_lift_kg_ph`", "The same gain measured without adjusting for who tends to use that input. Always larger.",
     "Advanced / expandable panel only."],
    ["`control_absorbed_share`", "How much of the raw association was explained away by those adjustments. 0.29 means 29%.",
     "Advanced panel."],
    ["`trained_to_2019_lift_kg_ph`", "The same gain estimated without the most recent season — does it hold up over time?",
     "Advanced panel."],
    ["`agronomically_plausible`", "Whether the fitted shape matches what agronomy expects.",
     "All eight are currently true. Surface it only if one ever turns false."],
], widths=[2.0, 3.0, 1.9])

doc.add_heading("3.5  Rules for showing this honestly", 2)
rich("These are not stylistic preferences. Field staff will stop trusting the tool if it "
     "overstates itself once.")
bullet(("Always show the interval", True), " next to any yield or gain figure.")
bullet(("Never present a per-plot prediction as a forecast.", True),
       " It is indicative. The district figure is the validated one.")
bullet(("Always surface warnings.", True),
       " The array is short, written in plain English, and every entry exists because "
       "something about that answer needs qualifying.")
bullet(("Do not sum the recommendations yourself.", True), " Use ",
       ("bundle.total_expected_lift_kg_ph", False, False, True),
       ". The service already warns when a total looks implausibly large relative to what "
       "the district actually produces.")
bullet(("Show causal_note somewhere reachable.", True),
       " These are strong associations from observational survey data, not results from a "
       "controlled trial. The wording is supplied; put it behind an info icon at minimum.")
bullet(("Round for display.", True),
       " \"About +590 kg/ha\" reads better than 586.4 and is more honest about the precision.")

doc.add_heading("3.6  Errors", 2)
table(["Status", "Meaning", "What the UI should do"], [
    ["`200`", "Success — including a success with zero recommendations.", "Render normally."],
    ["`422`", "The request was malformed: missing district, a negative rate, an unknown "
     "field name, an unknown lever, or more than 500 plots.",
     "Show the `detail` string. It names the problem field, and for levers it lists the "
     "valid names."],
    ["`503`", "A server-side artefact is missing. Not your fault and not the user's.",
     "\"The service is not fully configured\" plus a retry. Alert the deployment owner. "
     "The detail string says which file is missing."],
    ["`500`", "Unexpected failure during scoring.", "Generic error, log the detail, retry once."],
], widths=[0.7, 3.0, 3.2])
rich("Every error body has the same shape: ",
     ("{ \"detail\": \"human-readable message\" }", False, False, True),
     ". The messages are written to be shown to a person.")
callout("An unknown district is not an error.",
        "Send any district name. If the model has not seen it, you still get a 200 with "
        "results, district_known: false, and a warning. Do not validate district names "
        "client-side into a hard block — use the districts list for a typeahead instead.",
        fill="EAF5EE", colour=TEAL)

doc.add_heading("3.7  A suggested screen flow", 2)
number(("Plot profile.", True), " One form, built from /reference/input-schema. District "
       "typeahead required; everything else optional and clearly marked so. A completeness "
       "meter driven by how many fields are filled.")
number(("Result screen, two tabs.", True), " ", ("Yield", False, True),
       " (from /predict) and ", ("What to change", False, True), " (from /recommend). Most "
       "field agents will live in the second.")
number(("Lever and threshold controls", True), " on the recommendation tab: which levers "
       "the agent can actually act on today, and a \"only show me changes worth more than X\" "
       "slider driven by min_lift_kg_ph. Re-request on change — the call is fast and needs no "
       "model.")
number(("Recommendation cards", True), " in rank order: action headline, gain with range and "
       "percentage of district yield, confidence badge, and an expandable evidence panel.")
number(("An \"already doing well\" section", True), " built from ",
       ("skipped", False, False, True), ". It is genuinely reassuring, and it stops the "
       "screen looking empty for a good farmer.")

page_break()

# ===========================================================================
# PART 4 — REFERENCE
# ===========================================================================
doc.add_heading("Part 4 · Reference tables", 1)

doc.add_heading("4.1  Every endpoint", 2)
table(["Method & path", "Purpose", "Needs model?"], [
    ["`GET /`", "Service index — lists every endpoint. Good connectivity check.", "No"],
    ["`GET /health`", "Liveness. Answers even with nothing configured.", "No"],
    ["`GET /ready`", "Readiness — 200 only when defaults are present and the model loads.", "Yes"],
    ["`POST /api/v1/predict`", "Predict yield for up to 500 plots and their district averages.", "Yes"],
    ["`POST /api/v1/recommend`", "Ranked advice for one plot, in kg/ha.", "No"],
    ["`GET /api/v1/model/summary`", "Model version, training seasons, measured accuracy, stated limits.", "No"],
    ["`GET /api/v1/reference/districts`", "The 51 known districts and available seasons.", "No"],
    ["`GET /api/v1/reference/seed-types`", "Seed categories, varieties, intercrop species.", "No"],
    ["`GET /api/v1/reference/input-schema`", "Every input field with type, unit, allowed values, observed range.", "No"],
    ["`GET /api/v1/reference/levers`", "The eight controllable levers advice can cover.", "No"],
    ["`GET /docs`", "Interactive documentation.", "No"],
    ["`GET /openapi.json`", "Machine-readable schema for client generation.", "No"],
], widths=[2.3, 3.7, 0.9])

doc.add_heading("4.2  The plot object — every accepted field", 2)
rich("Used identically by both endpoints. Only ", ("district", False, False, True),
     " is required.")
table(["Field", "Type", "Unit / values"], [
    ["`plot_id`", "string", "Your own identifier, echoed back on the matching result."],
    ["`district`", "string — REQUIRED", "Case-insensitive. 51 known values."],
    ["`year`", "integer", "Season. Defaults to the most recent in the training data."],
    ["`plot_acres`", "number", "acres"],
    ["`seed_category`", "string", "hybrid_branded / other_hybrid / local / mixed"],
    ["`seed_type`", "string", "Exact variety, e.g. h_614d. See /reference/seed-types."],
    ["`hybridseed_kg_ph`", "number", "kg per hectare"],
    ["`localseed_kg_ph`", "number", "kg per hectare"],
    ["`dap_kg_ph`", "number", "kg/ha of DAP (18-46-0)"],
    ["`urea_kg_ph`", "number", "kg/ha of urea (46-0-0)"],
    ["`can_kg_ph`", "number", "kg/ha of CAN (26-0-0), the usual topdress"],
    ["`npk_kg_ph`", "number", "kg/ha of NPK blend, assumed 17-17-17"],
    ["`lime_kg_ph`", "number", "kg/ha of agricultural lime"],
    ["`compost_wheelbarrows_per_acre`", "number", "wheelbarrows per acre"],
    ["`plant_date`", "date (YYYY-MM-DD)", "Only the day of year is used."],
    ["`plant_date_doy`", "number 1–366", "Day of year, if the full date is unknown."],
    ["`intercrop`", "boolean", ""],
    ["`intercrop_type`", "string", "e.g. beans. Legumes are treated distinctly."],
    ["`hh_num`", "number", "Household size."],
    ["`hh_num_under18`", "number", "Household members under 18."],
    ["`cows`", "number", "Cows owned."],
    ["`owns_oxen`", "boolean", ""],
    ["`owns_electricity`", "boolean", ""],
], widths=[2.2, 1.4, 3.3])
callout("Out-of-range values are clipped, not rejected.",
        "A fertiliser rate above the ceiling used during cleaning is capped to that ceiling "
        "and reported in warnings, exactly as the training data was treated. A real survey "
        "row must never fail validation. Do not pre-validate these ranges into a hard block.",
        fill="EAF5EE", colour=TEAL)

doc.add_heading("4.3  The eight levers", 2)
rich("What ", ("/api/v1/recommend", False, False, True), " can advise on. \"Full range\" is "
     "the gain from the worst to the best value of that lever, in kg/ha, across all plots. "
     "There is no price column because the service does not price anything.")
table(["Lever", "Label", "Full range", "Notes"], [
    ["`planting_date`", "Planting date", "−351",
     "Flat until roughly early March, then falls steadily. Needs nothing bought, so it is "
     "usually the most defensible advice on the list."],
    ["`hybrid_seed`", "Share of seed that is hybrid", "+617",
     "The largest single lever. 76% of plots are already fully hybrid, so it only fires for "
     "those who are not."],
    ["`seed_variety`", "Seed variety", "+634",
     "Which variety within a genetics class. Estimated holding hybrid share fixed."],
    ["`topdress_fertiliser`", "CAN topdress", "+530",
     "Biggest single step is from none to some. Flattens around 120–150 kg/ha."],
    ["`basal_fertiliser`", "DAP at planting", "+454",
     "Same shape. Diminishing returns are fitted, not assumed."],
    ["`compost`", "Compost", "+379",
     "84% of plots use none, so support thins out quickly at higher rates."],
    ["`lime`", "Agricultural lime", "+673",
     "Only 4% of plots apply any, so its estimate rests on a thin slice. Flagged low "
     "confidence and easily retired by raising min_support."],
    ["`intercropping`", "Intercropping", "+6",
     "Never recommended: the effect is indistinguishable from zero. That is a finding, not a gap."],
], widths=[1.5, 1.5, 0.8, 3.1])

doc.add_heading("4.4  Measured model accuracy", 2)
rich("Straight from ", ("data/models/district_v2/metadata.json", False, False, True),
     ", also served live at ", ("GET /api/v1/model/summary", False, False, True), ".")
table(["Season", "Districts", "R²", "MAE (kg/ha)", "Correlation"], [
    ["2019", "39", "0.380", "696", "0.653"],
    ["2020", "50", "0.586", "329", "0.765"],
], widths=[1.0, 1.0, 1.0, 1.3, 1.2])
bullet("Trained on 23,674 plots across 2016–2020; validated out of time on 2019 and 2020.")
bullet("Ensemble of five members: ExtraTrees, LightGBM, XGBoost, RandomForest, NeuralNet.")
bullet("Prediction intervals target 80% coverage and are deliberately conservative — the "
       "nominal 80% band actually covered 92% of districts in 2020. Read them as a "
       "rarely-wrong bound.")
bullet("A district average backed by fewer than 20 plots is flagged and is mostly sampling noise.")
bullet("Districts never seen in training carry roughly double the error (MAE 543 vs 271 kg/ha in 2020).")

page_break()

# ===========================================================================
# PART 5 — NOT BUILT
# ===========================================================================
doc.add_heading("Part 5 · What is not built, and what comes next", 1)
rich("Stated plainly so nobody plans around something that does not exist.")

doc.add_heading("5.1  Not built", 2)
bullet(("No authentication or rate limiting.", True),
       " The service is open. Put it behind your API gateway and handle both there.")
bullet(("No credit or portfolio screen.", True),
       " The project report describes a second audience — loan officers seeing repayment "
       "risk. It is not built, and the report itself says it should not ship until the risk "
       "flag has been validated against real repayment data, which nobody has supplied yet.")
bullet(("No costs, prices, budget or return-on-investment figures.", True),
       " Everything is in kilograms per hectare. The survey carries no fertiliser or "
       "farm-gate price data, and the layer refuses to invent any — an assumed price would "
       "reorder the whole ranking. Affordability belongs in the client, where local prices "
       "are actually known.")
bullet(("Kenya and maize only.", True),
       " The underlying survey covers seven countries, but maize is the only crop present in "
       "all of them, and this pipeline was scoped to Kenya.")
bullet(("Recommendation curves are national.", True),
       " District and season effects are removed, but the shape of each curve is the same "
       "everywhere. The best planting date genuinely varies by district; a district-specific "
       "curve is the most valuable next improvement and is limited by sample size, not by method.")

doc.add_heading("5.2  Sensible next steps", 2)
number(("Ship the recommendation screen first.", True),
       " It needs no model file, so it can go live while the deployment of the large artefact "
       "is still being arranged.")
number(("If you need affordability, build it client-side.", True),
       " Hold a regional price table in the app, multiply it against the recommended "
       "quantities, and keep that arithmetic visibly separate from the service's yield "
       "figures.")
number(("Log model_version and curves_version", True),
       " with every request from day one. When a retrained model changes an answer, you will "
       "want to know exactly when it changed.")
number(("Agree a retraining cadence with the MEL team.", True),
       " The survey questionnaire changes between rounds, so this is a coordination problem "
       "before it is a technical one.")

rule()
para()
rich(("Questions about the HTTP contract", True), " → Documentation/api_reference.md. ",
     ("About what the model can and cannot do", True),
     " → kenya_maize_district_model_deployment.md. ",
     ("About how the advice is estimated", True),
     " → kenya_maize_recommendation_layer.md. All three are in the repository, and all three "
     "are kept in step with the code.")

out = Path("Documentation/kenya_maize_api_guide.docx")
doc.save(out)
print(f"wrote {out} ({out.stat().st_size/1024:.0f} KB)")
