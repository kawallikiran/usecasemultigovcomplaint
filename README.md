# AIGP26 use case 8: Multilingual citizen grievance desk

A prototype chatbot that registers citizen grievances in 15 Indian languages (see Languages supported below), plus a test harness that runs the eight tech-critic tests from the AIGP26 capstone brief.

For each complaint, the bot:
- identifies the language and routes the complaint to a department
- sets a priority
- screens for sensitive content (violence, harassment, caste atrocity, corruption, threats, self-harm)
- flags manipulation attempts
- replies only from approved templates.

**It never decides a case.** An officer approves every substantive reply, and only a designated officer handles sensitive cases.

All data is synthetic. The app makes no outbound calls and Streamlit telemetry is switched off. The language layer is offline. For a sovereign pilot, plug Bhashini and an India-hosted model into `grievdesk/adapters.py`, then re-run the same tests.

## Run with Docker (recommended)

```bash
docker compose up --build
```

Open **http://localhost:8502**. The container is ready when `docker compose ps` shows `healthy`.

- **Stop:** `docker compose down`. Decisions, the audit log and reports stay in the `reports` volume.
- **Start fresh:** `docker compose down -v`.
- **Run the 8 critic tests with no network:** `docker compose --profile tools run --rm critic`
- **Unit tests, including a headless test of the Streamlit app:** `docker compose --profile tools run --rm tests`

This repo uses port 8502 and the livestock repo uses 8501, so both can run at the same time.

**If you see "port is already allocated"**, another program is using port 8502. You can stop it, or choose another port:

```
set GRIEVANCE_PORT=8601              (Command Prompt)
$env:GRIEVANCE_PORT=8601             (PowerShell)
docker compose up
```

Then open http://localhost:8601.

**If the browser says it cannot connect:** the "Local URL" and "Network URL" lines in the log come from inside the container, so ignore them. Run `docker compose ps` and open the host port shown on the left of `->8501` in the PORTS column, typed as `http://127.0.0.1:<port>`. If PORTS is empty, the container stopped; check `docker compose logs`.

## Languages supported

The desk recognises complaints in **all 22 scheduled-language scripts** and routes **15 languages** end to end.

| Level | Languages | What happens |
| --- | --- | --- |
| Routed | English, Hindi, Chhattisgarhi, Hinglish, mixed Hindi-English, Marathi, Bengali, Assamese, Gujarati, Punjabi, Odia, Tamil, Telugu, Kannada, Malayalam, Urdu | Language identified, keyword routing, safety screen, and a reply line in the citizen's language above the English template |
| Recognised only | Santali (Ol Chiki), Manipuri (Meetei Mayek) | Sent to a human desk for that language, flagged `language_desk` |
| Not told apart offline | Nepali, Konkani, Maithili, Bodo, Dogri, Sanskrit (Devanagari, read as Hindi); Kashmiri, Sindhi (Perso-Arabic, read as Urdu) | Routed with the Hindi or Urdu keywords; the AI engine can label them properly |

**How it works**
- **Script identification:** `grievdesk/adapters.py` reads each character's Unicode script. Marker words then separate Hindi, Marathi and Chhattisgarhi, and the letters ৰ and ৱ separate Assamese from Bengali.
- **Normalisation:** the normaliser keeps every Indic vowel sign and removes invisible zero-width characters, so words stay whole.
- **Configuration files:**
  - `data/grievance/languages.yaml`: each language's script, support level, trust score and reply lines.
  - `data/grievance/lexicon_indic.yaml`: about 480 department, sensitive-content and urgency keywords for the 11 added languages, merged at start-up.
- **Reply design:** the citizen's line says only "registered", "an officer will contact you" or "priority, call 112". The ticket, department and response time stay in the English template, so every fact appears once and test 7 (hallucinations) can still check it.
- **Testing:** `config/grievance_multilingual.yaml` runs the 8 critic tests on 35 complaints in the 11 added languages. `grievance_multilingual_ai.yaml` runs the same set with an AI engine.

**Review before real use (a governance control, not a footnote)**
- **Native review:** the non-English keyword lists and reply lines were drafted for this prototype. Native speakers must check and extend them. Missing words fail safe to an officer; wrong words cause wrong routing, which the critic tests are there to catch.
- **Same-author test set:** the multilingual test set was written by the same people as the keywords, so its 100% score proves the plumbing, not real-world accuracy. Ask native speakers or the partner group to write a held-out set per language.
- **Deployment path:** in a deployment, Bhashini language detection, speech and translation, or an India-hosted model through the AI engine, would replace the keyword lists. The same tests then measure the change.

## Early warning: from single complaints to district alerts

Suggested by a subject-matter expert in our group. The **Early warning** tab groups many complaints into alerts for the district administration, for example:

> **Emerging water-service issue, South Block**
> 127 related complaints across 23 villages in the last 14 days
> up 65% compared with the previous 14 days (77)
> Mostly Chhattisgarhi-language submissions (52%); 28% by voice
> Priority: High. Field verification recommended.

**How it works** (`grievdesk/early_warning.py`, settings in `config/early_warning.yaml`):
1. Every complaint is sorted by the normal router. Complaints sent to an officer still count, using the suggested issue, and are marked "awaiting officer confirmation". Without them, the water alert above would show about 88 instead of 127, and local-language complaints would be under-counted.
2. Complaints are grouped by issue and block, and the last 14 days are compared with the 14 days before.
3. A group alerts only with **at least 20 complaints and a rise of at least 50%**. It is High priority at 50 or more.
4. Alerts carry counts only, never complaint text. Village counts below 5 show as "<5". An alert recommends field verification and never triggers action by itself.

**Data** (all synthetic, regenerate with `python -m grievdesk ew-generate`):
- **Complaints:** `data/grievance/early_warning_complaints.csv` holds 500 complaints from 4 blocks and 62 invented villages over 28 days, with planted patterns:
  - a water spike that should alert High;
  - a health cluster that should alert Medium;
  - a small rise and a tiny group, neither of which should alert.
- **Villages:** `data/grievance/villages.csv` lists the 62 villages, with Devanagari names.

**Outputs:** `python -m grievdesk early-warning` writes to `reports/early_warning/`:
- `alerts.txt`: the alert cards;
- `issue_trends.csv`: every issue and block;
- the check report;
- `early_warning.xlsx`: a workbook whose counts and alerts are live formulas driven by a Settings sheet.

**What the checks currently find:**
- **Both planted patterns caught, no false alarms:** both planted patterns are caught and nothing else alerts.
- **Counts:** counts match the true numbers exactly.
- **Privacy:** small numbers are hidden and no complaint text appears in alerts.
- **Language mix (failed):** the alert says 52% Chhattisgarhi, but the true figure is 72%. Chhattisgarhi written in English letters is misread as Hinglish.
- **Borderline alert (failed):** the 22-complaint health alert disappears in 3 of 5 runs when 10% of complaints are missing.

**Not covered yet:**
- **Gondi:** there are no Gondi keywords. Devanagari Gondi would be read as Hindi, and the Gondi scripts are not recognised.
- **Real voice:** only transcripts are handled; real speech needs speech-to-text (for example Bhashini).
- **Grouping:** alerts are grouped by block, not by distance between villages.

## AI engines: bundled open model and other AI services

By default the app uses the offline **rules engine**. You can also use AI engines, and every one that is ready appears in the **Engine** list on the first tab.

### 1. Bundled open model (runs on this computer, no key)

```
start-local-ai.bat          (Windows)
./start-local-ai.sh         (macOS / Linux)
```

This starts the app together with **Ollama** (an open-source model server) and downloads **Gemma 3 4B**. Gemma 3 4B is an open-weights model from Google that reads text and images and supports over 140 languages, so one model covers both use cases.

- **First start:** the download is about 3.3 GB. It goes into a Docker volume shared by both AIGP26 repos, so it happens once.
- **After that:** the demo works with **no internet** and nothing leaves the computer.
- **Watching the download:** `docker compose -f docker-compose.yml -f docker-compose.local-ai.yml logs -f model-pull`. Until it finishes, the Test report tab's status table shows "not downloaded yet".
- **Stopping:** `stop-local-ai.bat` or `./stop-local-ai.sh`.
- **Memory:** give Docker Desktop at least **8 GB** (Settings, then Resources).
- **Speed:** without a GPU, one AI answer takes about 5 to 60 seconds, and photos are slower. For local models, AI test runs are cut to 8 cases. For a large speed-up on an NVIDIA GPU, uncomment the GPU lines in `docker-compose.local-ai.yml`.
- **Other models:** set `LOCAL_MODEL` in `.env`, for example `qwen2.5vl:7b`, or a newer Gemma or Qwen-VL from the Ollama library. Use a model that accepts images for use case 5. Small models are weaker at Chhattisgarhi; that is a finding to report, not hide.

### 2. Other AI services (cloud APIs)

All services are listed in **`config/ai_providers.yaml`**, which is ready for OpenAI, Anthropic Claude, Google Gemini, Groq, OpenRouter and a **custom** slot for any other OpenAI-compatible API (for example an Indian provider or your own vLLM server). To use one:

1. Copy `.env.example` to `.env`.
2. Fill in that service's key and model name, for example `GEMINI_API_KEY` and `GEMINI_MODEL`.
3. Run `docker compose up -d --force-recreate`, or the start script again.

**To add a new service,** copy a block in `ai_providers.yaml`, give it a name, set `provider` (`openai`, `anthropic` or `gemini` style), `base_url`, `model` and `api_key_env`, then put the key in `.env`. Keys never go in the YAML file. `.env` is excluded from git and from the Docker image.

To check which services are ready:
- `docker compose exec grievance-desk-ui python -m grievdesk ai-status`
- or open the **AI services and their status** table on the Test report tab.

### What is sent, and the safeguards
The complaint text is sent with Aadhaar, phone numbers and emails masked first. With the bundled model, nothing leaves the computer. With cloud services, use synthetic data only.

- **Safety backstop:** the rule-based safety screen always runs too, so the AI can add a sensitive flag but never remove one.
- **Invented answers discarded:** departments or categories the AI makes up are thrown away.
- **Failures:** on any error, the rules engine is used and the case goes to an officer.
- **Replies:** citizen messages still come only from approved templates.

### Before and after with the same 8 tests
On the Test report tab, pick an AI test set and choose **AI service for this run**. Each service's results are saved separately, so you can compare the rules engine, the local open model and a cloud service on identical tests. That comparison is the evidence for the "after critique" part of the final presentation.

**Testing without a key:** `docker compose --profile tools run --rm tests` checks all provider formats and the local-model states against a built-in mock server.

## Run without Docker

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## The portal

A simple guided flow for citizens, with officer pages behind a login. The screen language is picked in the top corner (13 Indian languages and English; Chhattisgarhi uses the Hindi screen text). The choice is kept in the address, for example `?lang=ta`.

**Citizens: no login**

| Page | What it does |
| --- | --- |
| File a complaint | 1. Choose **Type** or **Speak**. 2. Type the complaint or record it with the microphone. 3. Pick the block and village. An optional mobile number is stored with only its last 4 digits. **Submit** shows a complaint number (for example `GRV/2026/00012`) and its status, with **Download receipt**: an acknowledgement slip in the chosen language that can be printed or saved as PDF from the browser. |
| Track complaint | Enter the complaint number to see the status in your language, and the office's reply once sent. |

Citizens never pick a template. They type or speak in their own words. Sample complaints are kept for officers under **System checks**.

**Officers: "Officer login"**

The password is set by `OFFICER_PASSWORD` in `.env` and defaults to `officer@123`. Change it.

| Page | What it does |
| --- | --- |
| Work queue | Voice complaints still to be typed and sensitive complaints come first. Officers can play a voice complaint, type it in, and the system sorts it. They see the suggested department, priority and reasons, then **forward**, **escalate** or **send reply and close**. The rules are enforced: name required, a note for forwarding or escalating, the draft placeholder replaced, and sensitive cases cannot be closed from the desk. |
| District dashboard | Alerts for emerging issues by block and village, with a report date picker. Complaints filed on the portal are included. |
| Reports and downloads | Complaint register and officer actions (CSV, opens in Excel with Indian scripts), dashboard workbook (Excel), issues by block, and alerts. |
| System checks | Runs the eight standard checks on a test set and downloads the results. Also lets officers try sample complaints without registering them. |
| Settings | Chooses the complaint-sorting engine (rules or an AI service) and the speech-to-text service, shows the status of each service, lists the languages, and has **Log out**. |

**Voice complaints**
- **Recording:** recorded in the browser. The microphone needs the page opened as `http://127.0.0.1:<port>` or `http://localhost:<port>`, or over HTTPS. Browsers block it on a plain network address.
- **Transcription:** if a speech-to-text service is ready (OpenAI, Groq, or a local server with the same API; see `speech:` in `config/ai_providers.yaml`), the complaint is transcribed and sorted at once.
- **Without a speech service:** the recording is kept and the complaint goes to the work queue for an officer to listen to and type. Voice complaints therefore always work.

**Data kept** (in the `reports` volume): `reports/portal/complaints.json` holds the register, with personal numbers hidden. `reports/portal/voice/` holds the recordings.

## Command line

```bash
python -m grievdesk all
python -m grievdesk run --config config/grievance_challenge.yaml
python -m grievdesk demo --text "Gaon mein 3 din se bijli nahi hai"
```

Departments, keywords, sensitive-content lists, manipulation patterns and response times live in `data/grievance/routing.yaml`. Reply templates and banned phrases live in `templates.yaml`. To test your own set, use a CSV with the columns `id, text, expected` (where `expected` is `auto:<department>`, `human` or `sensitive`) plus any group columns.

## What the tests currently find (your "before critique" baseline)

- **Main set:** 39 of 40 correct, and all 38 checks pass.
- **Held-out set: Critical.** 3 of 4 sensitive complaints were missed. One, a teacher beating a child described in Chhattisgarhi, went out automatically.
- **Held-out set: High.** Accuracy drops to 25% on unseen phrasing, though most cases fail safe to a human. Chhattisgarhi accuracy is 50 points behind the other languages.

## Limitations

The routing is a transparent keyword and rule engine. It shows the pipeline and the controls but not LLM behaviour. Once an LLM adapter is plugged in, the consistency, hallucination and injection tests become much more demanding.

## Layout

```
app.py                  Streamlit UI (display only)
grievdesk/system.py     routing pipeline
grievdesk/adapters.py   offline language identification; Bhashini stub
grievdesk/kit.py        use-case probes for the 8 critic tests
grievdesk/ui_logic.py   decision rules and audit rows (tested without Streamlit)
grievdesk/critic/       generic 8-test runner
grievdesk/common/       PII masking, audit log, config, reports
config/  data/  tests/
```
