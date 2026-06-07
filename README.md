# BloodLink — AI-Powered Blood Donor Matching

> AI4Good Hackathon · Team-017

BloodLink connects Thalassemia patients with compatible blood donors in real time. An AI coordinator collects patient details via chat, instantly ranks 7,000+ donors using a 15-tier priority system, and contacts top donors via WhatsApp and email within seconds.

---

## Live URLs

| Service | URL |
|---|---|
| Frontend (CloudFront) | https://d1v3y4dtb1e88n.cloudfront.net |
| Backend (API Gateway) | https://a7aq7j8pce.execute-api.eu-north-1.amazonaws.com |

---

## Features

### Patient Portal — AI Chat
- Conversational AI coordinator powered by **Amazon Bedrock** (Claude Haiku 4.5)
- Collects patient user ID (or blood group if unregistered), city, and urgency level
- Voice input via browser Web Speech API — works in both Hindi and English
- Contextual button shortcuts for blood group and urgency selection
- Real-time status banner that polls every 5 seconds showing SEARCHING → DONOR_FOUND with elapsed timer

### Volunteer Pipeline
- Patient lookup by user ID — returns full bridge donor card from DynamoDB
- Live requests table with full status pipeline: `SEARCHING → DONOR_FOUND → ARRIVING → TRANSFUSING → COMPLETED`
- Visual progress bar with colored dots and checkmarks
- Donor response panel showing ETA, location, and transport method
- Bridge health dashboard showing GREEN / YELLOW / RED classification per patient-donor pair

### Donor Confirm Page
- Mobile-optimized page opened from WhatsApp or email link
- 4-question follow-up flow after YES: availability, location, transport, arrival time
- Donor journey screen — donor advances their own status through ARRIVING → TRANSFUSING → COMPLETED
- Expired link detection — if already declined or another donor confirmed, shows "This link has expired" and blocks resubmission

### Messaging
- **WhatsApp** via Twilio sandbox — single confirm link sent to donor
- **Email** via AWS SES — styled HTML email with Respond Now button, sent in parallel with WhatsApp
- Auto-escalation: if all 5 contacted donors decline, immediately contacts the next 5 without waiting

### Donor Scoring and Prioritisation
- 15-tier priority system (see Scoring section below)
- Location-aware: nearby donors ranked first using Haversine distance
- Chat city matching: if patient's stated city differs from their DB record by more than 50 km, updates DynamoDB and re-ranks donors accordingly

---

## Project Structure

```
BloodLink/
│
├── Dataset.csv                     # 7,033 Blood Warriors donor records
│
├── backend/
│   ├── lambda_function.py          # Single Lambda entry point — all API routes via rawPath
│   ├── db_helpers.py               # All DynamoDB read/write operations
│   ├── matching.py                 # 15-tier donor scoring and prioritisation
│   ├── bridge_health.py            # GREEN/YELLOW/RED bridge classification
│   ├── escalation.py               # 5-level escalation pipeline
│   ├── bedrock_helpers.py          # Claude Bedrock chat + outreach message generation
│   ├── twilio_helpers.py           # WhatsApp (Twilio) + Email (AWS SES) outreach
│   ├── seed_data.py                # CSV loader — seeds DynamoDB from Dataset.csv
│   ├── setup_locations.py          # Populates city/lat/lon cache in DynamoDB
│   └── requirements.txt            # twilio (boto3 is built into Lambda runtime)
│
└── frontend/
    ├── public/
    │   └── index.html              # Tailwind CDN + custom color config
    └── src/
        ├── App.js                  # React Router v6 + nav + API base constant
        ├── index.css               # Inter font + custom Tailwind utilities
        └── pages/
            ├── PatientHome.js      # AI chat interface with voice input + live status banner
            ├── Pipeline.js         # Volunteer dashboard — patient search + live requests
            └── DonorConfirm.js     # Donor response page — yes/no + journey status tracker
```

---

## AWS Infrastructure

```
API Gateway (HTTP API)
    └── ANY /{proxy+}
            └── Lambda: bloodlink (Python 3.11, eu-north-1, 512 MB, 30s timeout)
                    ├── DynamoDB: donors      (7,033 records from Dataset.csv)
                    ├── DynamoDB: requests    (blood requests + status_history list)
                    ├── DynamoDB: locations   (lat/lon → city name cache)
                    ├── Bedrock:  eu.anthropic.claude-haiku-4-5-20251001-v1:0 (eu-north-1)
                    ├── Twilio:   WhatsApp sandbox (whatsapp:+14155238886)
                    └── SES:      pranithreddy16.beeram@gmail.com (eu-north-1)

S3: bloodlink-frontend-017
    └── CloudFront: d1v3y4dtb1e88n.cloudfront.net
```

---

## API Routes

| Method | Route | Purpose |
|---|---|---|
| POST | `/chat` | AI patient intake — returns bot reply, creates request when all fields collected |
| GET | `/request/{id}` | Fetch request details including status_history |
| POST | `/request/{id}/status` | Advance request through the status pipeline |
| GET | `/confirm` | Donor confirm or decline (`?action=yes\|no`) |
| POST | `/donor/response` | Save donor's 4-question answers after YES |
| GET | `/patient/search` | Volunteer patient lookup by user ID |
| GET | `/requests/active` | All non-completed requests |
| GET | `/bridges/health` | All patient bridge donor cards with classification |
| POST | `/escalate/{id}` | Manually trigger next escalation level |
| POST | `/nightly/run` | Sweep all active requests and re-escalate stale ones |

---

## Donor Scoring and Priority System

### Blood Compatibility

A donor can give to compatible patient blood groups:

| Patient Blood Group | Compatible Donor Groups |
|---|---|
| O Negative | O Negative |
| O Positive | O Negative, O Positive |
| A Negative | O Negative, A Negative |
| A Positive | O Negative, O Positive, A Negative, A Positive |
| B Negative | O Negative, B Negative |
| B Positive | O Negative, O Positive, B Negative, B Positive |
| AB Negative | O Negative, A Negative, B Negative, AB Negative |
| AB Positive | All groups |

### 15-Tier Priority System

Donors are split into same-location (within 5 km of patient) and different-location groups, then ranked by donor type and health:

**Same location (≤ 5 km):**

| Tier | Donor Type | Bridge Classification | Blood Match |
|---|---|---|---|
| 1 | Bridge Donor | GREEN | Exact |
| 2 | Bridge Donor | GREEN | Compatible |
| 3 | Bridge Donor | YELLOW | Exact |
| 4 | Bridge Donor | YELLOW | Compatible |
| 5 | Bridge Donor | RED | Exact |
| 6 | Bridge Donor | RED | Compatible |
| 7 | Emergency / One-Time | Active | Exact |
| 8 | Emergency / One-Time | Active | Compatible |
| 9 | Emergency / One-Time | Inactive | Exact |
| 10 | Emergency / One-Time | Inactive | Compatible |

**Different location (> 5 km):**

| Tier | Status | Blood Match |
|---|---|---|
| 11 | Active | Exact |
| 12 | Active | Compatible |
| 13 | Inactive | Exact |
| 14 | Inactive | Compatible |
| 15 | Fallback | Any compatible |

**Within each tier, donors are further sorted by:**
1. **Transfusion gap** — days until the donor's next eligible donation date. More days = safer to contact = ranked higher
2. **Reliability score** — calculated as `1 / (calls_to_donations_ratio + 0.1)`. A lower ratio means the donor shows up more often when called
3. **Distance** — closer donors ranked first (Haversine formula using lat/lon from DynamoDB)
4. **Total donations** — higher donation count ranked higher

### GREEN / YELLOW / RED Classification (Warrior Table)

Each bridge donor is classified per patient using two fields from the dataset:

```
gap_days = patient.expected_next_transfusion_date − donor.next_eligible_date
ratio    = donor.calls_to_donations_ratio
```

| Classification | Condition |
|---|---|
| GREEN | Active AND gap_days ≥ 0 AND ratio ≤ 5 |
| YELLOW | Active BUT gap_days < 0 OR ratio > 5 |
| RED | Inactive (regardless of other fields) |

**Understanding gap_days:**

`gap_days` answers the question: "Will this donor be eligible in time for the patient's next transfusion?"

- `gap_days = +55` → the donor becomes eligible 55 days before the transfusion date — plenty of time (GREEN)
- `gap_days = -46` → the donor won't be eligible until 46 days after the transfusion — too late (YELLOW)
- `gap_days = -266` → this appears in the Pipeline when transfusion dates from the CSV are from 2025 and today is mid-2026 — the patient's next transfusion is long overdue in the dataset. It is a data freshness issue, not a code bug.

**Understanding calls_to_donations_ratio:**

This is the number of times a donor was contacted divided by how many times they actually donated. A ratio > 5 means on average they need to be called more than 5 times before donating — unreliable for critical situations. Values come from the original CSV and are not updated in real time by the current system.

### Escalation Pipeline

When a request is created the system escalates automatically if donors do not respond:

| Level | Action |
|---|---|
| 1 | Contact top 5 exact blood group donors |
| 2 | Contact next 5 + email reminder to level-1 batch |
| 3 | Expand to all compatible blood groups, contact top 10 not yet reached |
| 4 | Flag as NEEDS_HUMAN, send alert email to coordinator |
| 5 | Emergency broadcast to all eligible donors in the same city |

If all donors in a batch decline, the next level triggers immediately.

---

## Environment Variables (Lambda)

| Variable | Purpose |
|---|---|
| `DONORS_TABLE` | DynamoDB donors table name |
| `REQUESTS_TABLE` | DynamoDB requests table name |
| `BEDROCK_MODEL_ID` | Bedrock model ID |
| `BEDROCK_REGION` | AWS region for Bedrock (eu-north-1) |
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_FROM_WHATSAPP` | Twilio WhatsApp sandbox number |
| `API_BASE_URL` | API Gateway invoke URL |
| `FRONTEND_URL` | CloudFront URL used in donor confirm links |
| `SES_FROM_EMAIL` | Verified SES sender address |
| `SES_REGION` | AWS region for SES |
| `DEMO_DONOR_PHONE` | Fallback phone when dataset records have no phone |
| `DEMO_DONOR_EMAIL` | Fallback email for demo outreach |
| `COORDINATOR_EMAIL` | Receives level-4 escalation alert emails |
| `COORDINATOR_PHONE` | Phone for coordinator alerts |

---

## Deployment

**Backend:**
```bash
cd backend
pip install -r requirements.txt -t ./package/
cp *.py ./package/
cd package && zip -r ../lambda_deployment.zip .
aws lambda update-function-code --function-name bloodlink \
  --zip-file fileb://lambda_deployment.zip --region eu-north-1
```

**Frontend:**
```bash
cd frontend
echo "REACT_APP_API_URL=https://a7aq7j8pce.execute-api.eu-north-1.amazonaws.com" > .env
npm run build
aws s3 sync build s3://bloodlink-frontend-017 --delete --region eu-north-1
aws cloudfront create-invalidation --distribution-id <DISTRIBUTION_ID> --paths "/*"
```

**Seed data:**
```bash
cd backend
python seed_data.py  # requires AWS credentials with DynamoDB write access
```
